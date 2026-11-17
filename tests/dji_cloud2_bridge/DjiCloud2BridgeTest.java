import com.dji.flighthubsdk.bean.HeaderKeyValue;
import java.io.*;
import java.nio.charset.StandardCharsets;
import java.util.*;

// Offline protocol tests with a test double. Does not assert DJI integration.
final class DjiCloud2BridgeTest {
    static final String RID = "77fb733a-7dc0-4a94-9bc3-756f487bf854";
    static final String SECRET = "synthetic-secret-do-not-log";
    static String b64(String s) { return Base64.getUrlEncoder().withoutPadding().encodeToString(s.getBytes(StandardCharsets.UTF_8)); }
    static String init(String value) { return "INIT\t1\t" + b64("10.1.2.3") + "\t443\t1\t" + b64("x-http-auth") + "\t" + b64(value) + "\t1\t" + b64("x-ws-auth") + "\t" + b64("ws-secret") + "\n"; }
    static final class Fake implements DjiCloud2Bridge.Driver {
        boolean started, closed, listed, badData, timeout;
        String status = "PENDING\tNONE";
        public void start(String h, int p, List<HeaderKeyValue> http, List<HeaderKeyValue> ws) throws DjiCloud2Bridge.BridgeError {
            if (!h.equals("10.1.2.3") || p != 443 || !http.get(0).getValue().equals(SECRET) || !ws.get(0).getValue().equals("ws-secret")) throw new DjiCloud2Bridge.BridgeError("PROTOCOL");
            started = true;
        }
        public List<DjiCloud2Bridge.DeviceView> list() throws DjiCloud2Bridge.BridgeError {
            listed = true;
            if (timeout) throw new DjiCloud2Bridge.BridgeError("TIMEOUT");
            if (badData) return List.of(new DjiCloud2Bridge.DeviceView("x".repeat(257), "", "", true));
            return List.of(new DjiCloud2Bridge.DeviceView("device-01", "机场", "dock", true));
        }
        public String wsStatus() { return status; }
        public void close() { closed = true; }
    }
    static String run(String input, Fake d) {
        ByteArrayOutputStream out = new ByteArrayOutputStream();
        DjiCloud2Bridge.run(new ByteArrayInputStream(input.getBytes(StandardCharsets.US_ASCII)), out, d);
        return out.toString(StandardCharsets.US_ASCII);
    }
    static void check(boolean b) { if (!b) throw new AssertionError(); }
    public static void main(String[] args) {
        Fake d = new Fake();
        String out = run(init(SECRET) + "LIST\t" + RID + "\nCLOSE\n", d);
        check(d.started && d.closed && d.listed && out.equals("READY\t1\nRESULT\t" + RID + "\t1\t" + b64("device-01") + "\t" + b64("机场") + "\t" + b64("dock") + "\t1\n"));
        check(!out.contains(SECRET) && !out.contains(b64(SECRET)) && !out.contains("ws-secret"));
        d = new Fake(); d.timeout = true;
        check(run(init(SECRET) + "LIST\t" + RID + "\nCLOSE\n", d).equals("READY\t1\nERROR\t" + RID + "\tTIMEOUT\n"));
        d = new Fake(); d.badData = true;
        check(run(init(SECRET) + "LIST\t" + RID + "\nCLOSE\n", d).equals("READY\t1\nERROR\t" + RID + "\tUPSTREAM\n"));
        d = new Fake();
        check(run(init("bad;value"), d).equals("FATAL\tPROTOCOL\n") && !d.started && d.closed);
        d = new Fake();
        check(run(init(SECRET) + "INIT\t1\n", d).equals("READY\t1\nFATAL\tPROTOCOL\n") && d.closed);
        d = new Fake();
        check(run("A".repeat(16385) + "\n", d).equals("FATAL\tPROTOCOL\n") && !d.started && d.closed);
        d = new Fake();
        check(run(init(SECRET) + "LIST\tbad\n", d).equals("READY\t1\nFATAL\tPROTOCOL\n") && !d.listed);
        d = new Fake();
        check(run(init(SECRET).replace(b64("x-http-auth"), b64("Host")), d).equals("FATAL\tPROTOCOL\n") && !d.started);
        d = new Fake();
        check(run(init(SECRET).replace(b64("x-http-auth"), b64("Sec-WebSocket-Protocol")), d).equals("FATAL\tPROTOCOL\n") && !d.started);
        d = new Fake();
        check(run(init(SECRET).replace(b64("x-http-auth"), b64("auth_keys")), d).equals("FATAL\tPROTOCOL\n") && !d.started);
        for (String status : List.of("PENDING\tNONE", "CONNECTED\tNONE", "DISCONNECTED\tNONE", "DISCONNECTED\tAUTH_401", "DISCONNECTED\tAUTH_403")) {
            d = new Fake(); d.status = status;
            out = run(init(SECRET) + "WS_STATUS\t" + RID + "\nCLOSE\n", d);
            check(out.equals("READY\t1\nWS_STATUS\t" + RID + "\t" + status + "\n"));
            check(!out.contains(SECRET) && !out.contains("ws-secret"));
        }
        d = new Fake(); d.status = "unknown-server-text";
        check(run(init(SECRET) + "WS_STATUS\t" + RID + "\nCLOSE\n", d).equals("READY\t1\nERROR\t" + RID + "\tSDK_UNSUPPORTED\n"));
        d = new Fake(); d.status = "CONNECTED\tAUTH_401";
        check(run(init(SECRET) + "WS_STATUS\t" + RID + "\nCLOSE\n", d).equals("READY\t1\nERROR\t" + RID + "\tSDK_UNSUPPORTED\n"));
        d = new Fake();
        check(run(init(SECRET) + "WS_STATUS\tbad\n", d).equals("READY\t1\nFATAL\tPROTOCOL\n"));
        System.out.println("DJI bridge protocol: 18 tests passed");
    }
}
