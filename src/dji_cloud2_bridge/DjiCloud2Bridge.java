// One process per connection and workspace. Compiled against DJI FlightHub SDK 3.3.7.
// No flight, route dispatch, video, or user-facing authorization is implemented.
import com.dji.flighthubsdk.FlightHubSDK;
import com.dji.flighthubsdk.FlightHubCallback;
import com.dji.flighthubsdk.bean.Device;
import com.dji.flighthubsdk.bean.DeviceTopo;
import com.dji.flighthubsdk.bean.FlightHubError;
import com.dji.flighthubsdk.bean.FlightHubSdkConfig;
import com.dji.flighthubsdk.bean.HeaderKeyValue;
import com.dji.flighthubsdk.core.JniLibraryLoader;
import com.dji.flighthubsdk.generated.FlightHubSdkErrorCode;
import com.dji.flighthubsdk.util.Logger;

import java.io.*;
import java.nio.ByteBuffer;
import java.nio.charset.CodingErrorAction;
import java.nio.charset.StandardCharsets;
import java.util.*;
import java.util.concurrent.*;
import java.util.regex.Pattern;

public final class DjiCloud2Bridge {
    // Available only in our experimental SDK build, not DJI's stock JNI.
    private static native int nativeWebSocketSnapshotV2();
    private static final int MAX_INPUT = 16384, MAX_OUTPUT = 262144, MAX_DEVICES = 1000;
    private static final Pattern HEADER = Pattern.compile("[A-Za-z][A-Za-z0-9_-]{0,63}");
    private static final Set<String> FORBIDDEN = Set.of("host", "connection", "content-length", "transfer-encoding", "cookie", "set-cookie", "proxy-authorization", "proxy-authenticate", "upgrade", "te", "trailer", "keep-alive", "origin", "accept-encoding", "auth_keys");
    private static final Pattern UUID = Pattern.compile("[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}");
    private static final Pattern HOST = Pattern.compile("(?:[0-9]{1,3}\\.){3}[0-9]{1,3}");
    private static void watchParent() {
        ProcessHandle original = ProcessHandle.current().parent().orElse(null);
        if (original == null || original.pid() <= 1) Runtime.getRuntime().halt(71);
        Thread watcher = new Thread(() -> {
            while (true) {
                try {
                    Thread.sleep(250);
                    long currentParent = ProcessHandle.current().parent().map(ProcessHandle::pid).orElse(-1L);
                    if (!original.isAlive() || currentParent != original.pid()) Runtime.getRuntime().halt(71);
                } catch (Throwable e) { Runtime.getRuntime().halt(71); }
            }
        }, "parent-watchdog");
        watcher.setDaemon(true);
        watcher.start();
    }

    static final class DeviceView {
        final String sn, name, model;
        final boolean online;
        DeviceView(String sn, String name, String model, boolean online) {
            this.sn = sn; this.name = name; this.model = model; this.online = online;
        }
    }
    static final class BridgeError extends Exception {
        final String code;
        BridgeError(String code) { this.code = code; }
    }
    interface Driver {
        void start(String host, int port, List<HeaderKeyValue> http, List<HeaderKeyValue> ws) throws BridgeError;
        List<DeviceView> list() throws BridgeError;
        String wsStatus() throws BridgeError;
        void close();
    }
    static final class SdkDriver implements Driver {
        private boolean started;
        public void start(String host, int port, List<HeaderKeyValue> http, List<HeaderKeyValue> ws) throws BridgeError {
            try {
                // Prevent the Java SDK logger from writing any credentials to files/console.
                Logger.initialize(null, null, Logger.LogLevel.ERROR, false, false, 1, 1);
                JniLibraryLoader.loadLibrary();
                // Reject old/native-unmodified SDKs before passing either credential.
                try { wsStatus(); } catch (BridgeError e) { throw new BridgeError("SDK_UNSUPPORTED"); }
                if (FlightHubSDK.setWebSocketAuthHeaders(ws) != FlightHubSdkErrorCode.SUCCESS ||
                    FlightHubSDK.setHttpHeaders(http) != FlightHubSdkErrorCode.SUCCESS) throw new BridgeError("AUTH_SETUP");
                FlightHubSdkConfig cfg = new FlightHubSdkConfig();
                cfg.setAddress("https://" + host); cfg.setPort(Integer.toString(port));
                cfg.setNotificationServerAddress("wss://" + host + ":" + port + "/openapi/v0.9/notification/ws/v1/notifications");
                cfg.setVerifySsl(true); cfg.setLogToConsole(false); cfg.setShowVersionInfo(false);
                cfg.setLogLevel("off"); cfg.setLogFilePath("/dev/null");
                if (FlightHubSDK.initialize(cfg) != FlightHubSdkErrorCode.SUCCESS) throw new BridgeError("INITIALIZE");
                started = true;
            } catch (BridgeError e) { throw e; }
              catch (Throwable e) { throw new BridgeError("INITIALIZE"); }
        }
        public List<DeviceView> list() throws BridgeError {
            CompletableFuture<List<DeviceView>> result = new CompletableFuture<>();
            try {
                FlightHubSDK.getWorkspaceInstance().getDeviceManageModule().getDeviceList(new FlightHubCallback.DeviceListCallback() {
                    @Override public void onResult(List<DeviceTopo> topologies, FlightHubError error) {
                        try {
                            if (error != null && !error.isSuccess()) {
                                result.completeExceptionally(new BridgeError(error.getHttpCode() == 401 || error.getHttpCode() == 403 ? "AUTH" : "UPSTREAM")); return;
                            }
                            if (topologies == null) throw new BridgeError("UPSTREAM");
                            List<DeviceView> views = new ArrayList<>();
                            for (DeviceTopo topo : topologies) {
                                if (topo == null) throw new BridgeError("UPSTREAM");
                                add(views, topo.getDevice());
                                List<Device> children = topo.getSubDevices();
                                if (children != null) for (Device child : children) add(views, child);
                            }
                            result.complete(views);
                        } catch (Throwable ex) { result.completeExceptionally(new BridgeError("UPSTREAM")); }
                    }
                });
                return result.get(15, TimeUnit.SECONDS);
            } catch (TimeoutException e) { result.cancel(false); throw new BridgeError("TIMEOUT"); }
              catch (ExecutionException e) {
                Throwable cause = e.getCause();
                if (cause instanceof BridgeError) throw (BridgeError) cause;
                throw new BridgeError("UPSTREAM");
            } catch (Throwable e) { throw new BridgeError("UPSTREAM"); }
        }
        public String wsStatus() throws BridgeError {
            try {
                switch (nativeWebSocketSnapshotV2()) {
                    case 0: return "PENDING\tNONE";
                    case 1: return "CONNECTED\tNONE";
                    case 4: return "DISCONNECTED\tNONE";
                    case 20: return "DISCONNECTED\tAUTH_401";
                    case 28: return "DISCONNECTED\tAUTH_403";
                    default: throw new BridgeError("SDK_UNSUPPORTED");
                }
            } catch (UnsatisfiedLinkError e) { throw new BridgeError("SDK_UNSUPPORTED"); }
        }
        private static void add(List<DeviceView> result, Device d) throws BridgeError {
            if (d == null || result.size() >= MAX_DEVICES) throw new BridgeError("UPSTREAM");
            result.add(new DeviceView(d.getDeviceSn(), d.getDeviceName(), d.getModelKey(), d.isOnline()));
        }
        public void close() { if (started) try { FlightHubSDK.shutdown(); } catch (Throwable ignored) {} }
    }

    private static String readLine(InputStream input) throws IOException {
        ByteArrayOutputStream buffer = new ByteArrayOutputStream();
        while (true) {
            int b = input.read();
            if (b == -1) { if (buffer.size() == 0) return null; throw new IOException(); }
            if (b == '\n') return buffer.toString(StandardCharsets.US_ASCII);
            if (b < 32 && b != '\t' || b > 126 || buffer.size() >= MAX_INPUT) throw new IOException();
            buffer.write(b);
        }
    }
    private static String decode(String s, int max) throws BridgeError {
        try {
            if (!s.matches("[A-Za-z0-9_-]*") || s.length() > max * 2) throw new IllegalArgumentException();
            byte[] bytes = Base64.getUrlDecoder().decode(s);
            if (bytes.length > max || !Base64.getUrlEncoder().withoutPadding().encodeToString(bytes).equals(s)) throw new IllegalArgumentException();
            return StandardCharsets.UTF_8.newDecoder().onMalformedInput(CodingErrorAction.REPORT).onUnmappableCharacter(CodingErrorAction.REPORT).decode(ByteBuffer.wrap(bytes)).toString();
        } catch (Exception e) { throw new BridgeError("PROTOCOL"); }
    }
    private static String encode(String s, int max, boolean required) throws BridgeError {
        if (s == null) s = "";
        byte[] bytes = s.getBytes(StandardCharsets.UTF_8);
        if (bytes.length > max || required && bytes.length == 0) throw new BridgeError("UPSTREAM");
        for (int i = 0; i < s.length(); i++) if (Character.isISOControl(s.charAt(i))) throw new BridgeError("UPSTREAM");
        return Base64.getUrlEncoder().withoutPadding().encodeToString(bytes);
    }
    private static List<HeaderKeyValue> headers(String[] f, int[] index) throws BridgeError {
        int count;
        try { String n = f[index[0]++]; if (!n.matches("[1-8]")) throw new IllegalArgumentException(); count = Integer.parseInt(n); }
        catch (Exception e) { throw new BridgeError("PROTOCOL"); }
        List<HeaderKeyValue> list = new ArrayList<>(); Set<String> names = new HashSet<>();
        for (int i = 0; i < count; i++) {
            if (index[0] + 1 >= f.length) throw new BridgeError("PROTOCOL");
            String key = decode(f[index[0]++], 64), val = decode(f[index[0]++], 2048);
            String lower = key.toLowerCase(Locale.ROOT);
            if (!HEADER.matcher(key).matches() || FORBIDDEN.contains(lower) || lower.startsWith("sec-websocket-") || !names.add(lower) || val.trim().isEmpty()) throw new BridgeError("PROTOCOL");
            for (int j = 0; j < val.length(); j++) if (val.charAt(j) < 32 || val.charAt(j) > 126 || val.charAt(j) == ';') throw new BridgeError("PROTOCOL");
            list.add(new HeaderKeyValue(key, val));
        }
        return list;
    }
    static int run(InputStream in, OutputStream out, Driver driver) {
        PrintWriter writer = new PrintWriter(new OutputStreamWriter(out, StandardCharsets.US_ASCII), true);
        try {
            String line = readLine(in); if (line == null) return 1;
            String[] f = line.split("\\t", -1);
            if (f.length < 8 || !f[0].equals("INIT") || !f[1].equals("1")) throw new BridgeError("PROTOCOL");
            String host = decode(f[2], 15);
            if (!HOST.matcher(host).matches()) throw new BridgeError("PROTOCOL");
            for (String octet : host.split("\\.")) if (Integer.parseInt(octet) > 255 || octet.length() > 1 && octet.charAt(0) == '0') throw new BridgeError("PROTOCOL");
            int port; try { if (!f[3].matches("[1-9][0-9]{0,4}")) throw new IllegalArgumentException(); port = Integer.parseInt(f[3]); if (port > 65535) throw new IllegalArgumentException(); }
            catch (Exception e) { throw new BridgeError("PROTOCOL"); }
            int[] idx = {4}; List<HeaderKeyValue> http = headers(f, idx), ws = headers(f, idx);
            if (idx[0] != f.length) throw new BridgeError("PROTOCOL");
            driver.start(host, port, http, ws); writer.println("READY\t1");
            while ((line = readLine(in)) != null) {
                f = line.split("\\t", -1);
                if (f.length == 1 && f[0].equals("CLOSE")) return 0;
                if (f.length != 2 || !(f[0].equals("LIST") || f[0].equals("WS_STATUS")) || !UUID.matcher(f[1]).matches()) throw new BridgeError("PROTOCOL");
                try {
                    if (f[0].equals("WS_STATUS")) {
                        String status = driver.wsStatus();
                        if (!Set.of("PENDING\tNONE", "CONNECTED\tNONE", "DISCONNECTED\tNONE", "DISCONNECTED\tAUTH_401", "DISCONNECTED\tAUTH_403").contains(status)) throw new BridgeError("SDK_UNSUPPORTED");
                        writer.println("WS_STATUS\t" + f[1] + "\t" + status);
                        continue;
                    }
                    List<DeviceView> devices = driver.list();
                    if (devices == null || devices.size() > MAX_DEVICES) throw new BridgeError("UPSTREAM");
                    StringBuilder output = new StringBuilder("RESULT\t").append(f[1]).append('\t').append(devices.size());
                    for (DeviceView d : devices) {
                        if (d == null) throw new BridgeError("UPSTREAM");
                        output.append('\t').append(encode(d.sn, 256, true)).append('\t').append(encode(d.name, 256, false)).append('\t').append(encode(d.model, 256, false)).append('\t').append(d.online ? '1' : '0');
                        if (output.length() > MAX_OUTPUT) throw new BridgeError("UPSTREAM");
                    }
                    writer.println(output);
                } catch (BridgeError ex) { writer.println("ERROR\t" + f[1] + "\t" + ex.code); }
            }
            return 0;
        } catch (BridgeError e) { writer.println("FATAL\t" + e.code); return 1; }
          catch (Throwable e) { writer.println("FATAL\tPROTOCOL"); return 1; }
          finally { try { driver.close(); } catch (Throwable ignored) {} }
    }
    public static void main(String[] args) {
        // The SDK's JNI loader prints directly to System.out, regardless of logger
        // settings. Preserve the protocol stream and suppress Java SDK output.
        PrintStream protocol = System.out;
        System.setOut(new PrintStream(OutputStream.nullOutputStream()));
        System.setErr(new PrintStream(OutputStream.nullOutputStream()));
        watchParent();
        System.exit(run(System.in, protocol, new SdkDriver()));
    }
}
