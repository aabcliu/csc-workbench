package csc.bridge;

import java.io.BufferedWriter;
import java.io.File;
import java.io.FileWriter;
import java.io.IOException;
import java.io.PrintWriter;

/**
 * Transparent runtime probes for structured CSC execution traces.
 *
 * Every probe records a JSONL event and returns the original value unchanged.
 * If -Dcsc.trace.file=/path/to/trace.jsonl is supplied, events are written to
 * that file. Otherwise events are written to stdout as a compatibility fallback.
 */
public final class CSCTrace {

    private static final Object LOCK = new Object();
    private static PrintWriter writer;
    private static boolean closeWriter;

    private CSCTrace() {
    }

    public static boolean cond(String probeId, int line, String kind, int order, String expr, boolean value) {
        write("{\"type\":\"COND\",\"probeId\":\"" + escape(probeId) + "\""
                + ",\"line\":" + line
                + ",\"kind\":\"" + escape(kind) + "\""
                + ",\"order\":" + order
                + ",\"expr\":\"" + escape(expr) + "\""
                + ",\"value\":" + value + "}");
        return value;
    }

    /** Compatibility overload for source instrumented before coverage probe IDs. */
    public static boolean cond(int line, String kind, int order, String expr, boolean value) {
        return cond("", line, kind, order, expr, value);
    }

    public static boolean branch(String probeId, int line, String kind, String expr, boolean value) {
        write("{\"type\":\"BRANCH\",\"probeId\":\"" + escape(probeId) + "\""
                + ",\"line\":" + line
                + ",\"kind\":\"" + escape(kind) + "\""
                + ",\"expr\":\"" + escape(expr) + "\""
                + ",\"value\":" + value + "}");
        return value;
    }

    public static void input(int line, String name, String javaType, Object value) {
        write("{\"type\":\"INPUT\",\"line\":" + line
                + ",\"name\":\"" + escape(name) + "\""
                + ",\"javaType\":\"" + escape(javaType) + "\""
                + ",\"value\":\"" + escapeValue(value) + "\"}");
    }

    public static int assignInt(int line, String kind, String target, String rhs, int value) {
        writeAssign(line, kind, target, rhs, value);
        return value;
    }

    public static long assignLong(int line, String kind, String target, String rhs, long value) {
        writeAssign(line, kind, target, rhs, value);
        return value;
    }

    public static short assignShort(int line, String kind, String target, String rhs, short value) {
        writeAssign(line, kind, target, rhs, value);
        return value;
    }

    public static byte assignByte(int line, String kind, String target, String rhs, byte value) {
        writeAssign(line, kind, target, rhs, value);
        return value;
    }

    public static char assignChar(int line, String kind, String target, String rhs, char value) {
        writeAssign(line, kind, target, rhs, value);
        return value;
    }

    public static double assignDouble(int line, String kind, String target, String rhs, double value) {
        writeAssign(line, kind, target, rhs, value);
        return value;
    }

    public static float assignFloat(int line, String kind, String target, String rhs, float value) {
        writeAssign(line, kind, target, rhs, value);
        return value;
    }

    public static boolean assignBoolean(int line, String kind, String target, String rhs, boolean value) {
        writeAssign(line, kind, target, rhs, value);
        return value;
    }

    public static <T> T assignObject(int line, String kind, String target, String rhs, T value) {
        writeAssign(line, kind, target, rhs, value);
        return value;
    }

    public static int retInt(int line, String target, String rhs, int value) {
        writeReturn(line, target, rhs, value);
        return value;
    }

    public static long retLong(int line, String target, String rhs, long value) {
        writeReturn(line, target, rhs, value);
        return value;
    }

    public static short retShort(int line, String target, String rhs, short value) {
        writeReturn(line, target, rhs, value);
        return value;
    }

    public static byte retByte(int line, String target, String rhs, byte value) {
        writeReturn(line, target, rhs, value);
        return value;
    }

    public static char retChar(int line, String target, String rhs, char value) {
        writeReturn(line, target, rhs, value);
        return value;
    }

    public static double retDouble(int line, String target, String rhs, double value) {
        writeReturn(line, target, rhs, value);
        return value;
    }

    public static float retFloat(int line, String target, String rhs, float value) {
        writeReturn(line, target, rhs, value);
        return value;
    }

    public static boolean retBoolean(int line, String target, String rhs, boolean value) {
        writeReturn(line, target, rhs, value);
        return value;
    }

    public static <T> T retObject(int line, String target, String rhs, T value) {
        writeReturn(line, target, rhs, value);
        return value;
    }

    private static void writeAssign(int line, String kind, String target, String rhs, Object value) {
        write("{\"type\":\"ASSIGN\",\"line\":" + line
                + ",\"kind\":\"" + escape(kind) + "\""
                + ",\"target\":\"" + escape(target) + "\""
                + ",\"rhs\":\"" + escape(rhs) + "\""
                + ",\"value\":\"" + escapeValue(value) + "\"}");
    }

    private static void writeReturn(int line, String target, String rhs, Object value) {
        write("{\"type\":\"RETURN\",\"line\":" + line
                + ",\"target\":\"" + escape(target) + "\""
                + ",\"rhs\":\"" + escape(rhs) + "\""
                + ",\"value\":\"" + escapeValue(value) + "\"}");
    }

    private static void write(String json) {
        synchronized (LOCK) {
            PrintWriter out = getWriter();
            out.println(json);
            out.flush();
        }
    }

    private static PrintWriter getWriter() {
        if (writer != null) {
            return writer;
        }

        String tracePath = System.getProperty("csc.trace.file");
        if (tracePath == null || tracePath.isBlank()) {
            writer = new PrintWriter(System.out, true);
            closeWriter = false;
            return writer;
        }

        try {
            File traceFile = new File(tracePath);
            File parent = traceFile.getParentFile();
            if (parent != null) {
                parent.mkdirs();
            }
            writer = new PrintWriter(new BufferedWriter(new FileWriter(traceFile, false)));
            closeWriter = true;
            Runtime.getRuntime().addShutdownHook(new Thread(CSCTrace::close));
            return writer;
        } catch (IOException e) {
            throw new RuntimeException("Unable to open CSC trace file: " + tracePath, e);
        }
    }

    private static void close() {
        synchronized (LOCK) {
            if (writer != null && closeWriter) {
                writer.flush();
                writer.close();
            }
        }
    }

    private static String escapeValue(Object value) {
        return escape(String.valueOf(value));
    }

    private static String escape(String value) {
        if (value == null) {
            return "";
        }
        StringBuilder sb = new StringBuilder(value.length() + 16);
        for (int i = 0; i < value.length(); i++) {
            char ch = value.charAt(i);
            switch (ch) {
                case '\\':
                    sb.append("\\\\");
                    break;
                case '"':
                    sb.append("\\\"");
                    break;
                case '\b':
                    sb.append("\\b");
                    break;
                case '\f':
                    sb.append("\\f");
                    break;
                case '\n':
                    sb.append("\\n");
                    break;
                case '\r':
                    sb.append("\\r");
                    break;
                case '\t':
                    sb.append("\\t");
                    break;
                default:
                    if (ch < 0x20) {
                        sb.append(String.format("\\u%04x", (int) ch));
                    } else {
                        sb.append(ch);
                    }
            }
        }
        return sb.toString();
    }
}
