import java.io.*;
import java.net.*;
import java.nio.charset.StandardCharsets;
import java.nio.file.*;
import java.security.*;
import java.util.*;
import java.util.concurrent.*;
import java.util.concurrent.atomic.*;
import javax.net.ssl.*;

// Disposable synthetic TLS endpoint. Run only inside an isolated container with
// a patched SDK jar and a bridge compiled to trust the one-day test CA. It never
// uses a real endpoint or credentials and does not print request headers.
final class NativeWebSocketTlsTest {
    private static final String ID = "77fb733a-7dc0-4a94-9bc3-756f487bf854";
    private static final String SECRET = "synthetic-ws-test-secret";
    private static String enc(String s) { return Base64.getUrlEncoder().withoutPadding().encodeToString(s.getBytes(StandardCharsets.UTF_8)); }
    private static void check(boolean ok, String message) { if (!ok) throw new AssertionError(message); }
    public static void main(String[] args) throws Exception {
        KeyStore keyStore = KeyStore.getInstance("PKCS12");
        try (InputStream in = Files.newInputStream(Path.of(System.getProperty("ws.test.dir"), "server.p12"))) { keyStore.load(in, "synthetic".toCharArray()); }
        KeyManagerFactory factory = KeyManagerFactory.getInstance(KeyManagerFactory.getDefaultAlgorithm());
        factory.init(keyStore, "synthetic".toCharArray());
        SSLContext ctx = SSLContext.getInstance("TLS"); ctx.init(factory.getKeyManagers(), null, null);
        SSLServerSocket server = (SSLServerSocket)ctx.getServerSocketFactory().createServerSocket(0, 32, InetAddress.getByName("127.0.0.1"));
        server.setSoTimeout(200);
        AtomicReference<String> mode = new AtomicReference<>("stall");
        AtomicBoolean stop = new AtomicBoolean(), seen = new AtomicBoolean();
        AtomicInteger drops = new AtomicInteger();
        List<Socket> sockets = Collections.synchronizedList(new ArrayList<>());
        ExecutorService peers = Executors.newCachedThreadPool();
        Thread acceptor = new Thread(() -> {
            while (!stop.get()) try {
                SSLSocket socket = (SSLSocket)server.accept(); sockets.add(socket);
                peers.execute(() -> {
                    try {
                        socket.setSoTimeout(12000);
                        BufferedReader input = new BufferedReader(new InputStreamReader(socket.getInputStream(), StandardCharsets.US_ASCII));
                        if (input.readLine() == null) return;
                        Map<String,String> headers = new HashMap<>(); String line;
                        while ((line = input.readLine()) != null && !line.isEmpty()) {
                            int pos = line.indexOf(':'); if (pos > 0) headers.put(line.substring(0, pos).toLowerCase(Locale.ROOT), line.substring(pos+1).trim());
                        }
                        if (!"websocket".equalsIgnoreCase(headers.get("upgrade"))) return;
                        String expected = enc("X-Workspace-Token=" + SECRET);
                        if (!headers.getOrDefault("sec-websocket-protocol", "").contains("auth." + expected)) return;
                        seen.set(true); String choice = mode.get(); OutputStream out = socket.getOutputStream();
                        if (choice.equals("401") || choice.equals("403")) {
                            out.write(("HTTP/1.1 " + choice + " Rejected\r\nContent-Length: 0\r\nConnection: close\r\n\r\n").getBytes(StandardCharsets.US_ASCII)); out.flush(); return;
                        }
                        if (choice.equals("drop")) { drops.incrementAndGet(); return; }
                        if (choice.equals("stall")) { Thread.sleep(10000); return; }
                        String key = headers.get("sec-websocket-key");
                        String accepted = Base64.getEncoder().encodeToString(MessageDigest.getInstance("SHA-1").digest((key + "258EAFA5-E914-47DA-95CA-C5AB0DC85B11").getBytes(StandardCharsets.US_ASCII)));
                        out.write(("HTTP/1.1 101 Switching Protocols\r\nUpgrade: websocket\r\nConnection: Upgrade\r\nSec-WebSocket-Accept: " + accepted + "\r\nSec-WebSocket-Protocol: v1.notify\r\n\r\n").getBytes(StandardCharsets.US_ASCII)); out.flush();
                        while (!stop.get() && mode.get().equals("open")) Thread.sleep(50);
                    } catch (Exception ignored) {} finally { try { socket.close(); } catch (IOException ignored) {} }
                });
            } catch (SocketTimeoutException ignored) {} catch (IOException closed) { break; }
        });
        acceptor.start();
        try {
            runScenario(server.getLocalPort(), mode, seen, drops, "401", "DISCONNECTED\tAUTH_401");
            runScenario(server.getLocalPort(), mode, seen, drops, "403", "DISCONNECTED\tAUTH_403");
            runScenario(server.getLocalPort(), mode, seen, drops, "stall", "PENDING\tNONE");
            System.out.println("Native JNI TLS scenarios passed: 401, 403, sticky rejection, successful reconnect, disconnect, pending");
        } finally {
            stop.set(true); server.close(); synchronized (sockets) { for (Socket socket : sockets) try { socket.close(); } catch (IOException ignored) {} }
            peers.shutdownNow(); acceptor.join(2000);
        }
    }
    private static void runScenario(int port, AtomicReference<String> mode, AtomicBoolean seen, AtomicInteger drops, String initial, String expected) throws Exception {
        mode.set(initial); seen.set(false);
        Path stderr = Files.createTempFile(Path.of(System.getProperty("ws.test.dir")), "jni-stderr-", ".log");
        Process p = new ProcessBuilder("java", "-cp", System.getProperty("java.class.path"), "DjiCloud2Bridge").redirectError(stderr.toFile()).start();
        BufferedReader out = new BufferedReader(new InputStreamReader(p.getInputStream(), StandardCharsets.US_ASCII));
        BufferedWriter in = new BufferedWriter(new OutputStreamWriter(p.getOutputStream(), StandardCharsets.US_ASCII));
        String header = enc("X-Workspace-Token"), secret = enc(SECRET);
        try {
            in.write("INIT\t1\t" + enc("127.0.0.1") + "\t" + port + "\t1\t" + header + "\t" + secret + "\t1\t" + header + "\t" + secret + "\n"); in.flush();
            check("READY\t1".equals(out.readLine()), "SDK initialization failed");
            if (initial.equals("stall")) {
                long end = System.nanoTime() + TimeUnit.SECONDS.toNanos(10);
                while (!seen.get() && System.nanoTime() < end) Thread.sleep(100);
            }
            await(out, in, expected, 10000); check(seen.get(), "WSS handshake was not received");
            if (!initial.equals("stall")) {
                // Automatic retries must not erase an observed authorization rejection.
                Thread.sleep(1500); check(status(out,in).equals(expected), "auth rejection was not sticky");
                int priorDrops = drops.get(); mode.set("drop");
                long dropDeadline = System.nanoTime() + TimeUnit.SECONDS.toNanos(10);
                while (drops.get() == priorDrops && System.nanoTime() < dropDeadline) Thread.sleep(100);
                check(drops.get() > priorDrops, "SDK did not retry after authorization rejection");
                Thread.sleep(300); check(status(out,in).equals(expected), "transport error erased prior authorization rejection");
                mode.set("open"); await(out,in,"CONNECTED\tNONE",10000);
                mode.set("stall"); await(out,in,"DISCONNECTED\tNONE",10000);
            }
            in.write("CLOSE\n"); in.flush();
        } finally {
            p.destroyForcibly(); p.waitFor(5, TimeUnit.SECONDS);
            String log = Files.readString(stderr);
            check(!log.contains(SECRET) && !log.contains(enc(SECRET)) && !log.contains(enc("X-Workspace-Token=" + SECRET)), "credentials appeared in native stderr");
        }
    }
    private static String status(BufferedReader out, BufferedWriter in) throws Exception {
        in.write("WS_STATUS\t" + ID + "\n"); in.flush(); String response = out.readLine();
        check(response != null && response.startsWith("WS_STATUS\t" + ID + "\t") && !response.contains(SECRET) && !response.contains(enc(SECRET)), "invalid or sensitive protocol output");
        return response.substring(("WS_STATUS\t" + ID + "\t").length());
    }
    private static void await(BufferedReader out, BufferedWriter in, String expected, long timeout) throws Exception {
        long end = System.nanoTime() + TimeUnit.MILLISECONDS.toNanos(timeout);
        do { if (status(out,in).equals(expected)) return; Thread.sleep(100); } while (System.nanoTime() < end);
        throw new AssertionError("SDK callback did not produce " + expected);
    }
}
