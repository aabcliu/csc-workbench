public class MarginAdjustLoop {
    public static int adjustMargin(int start, int count, boolean aggressive) {
        if (start < -80 || start > 80) {
            return -1;
        }
        if (count < 0 || count >= 30) {
            return -1;
        }

        int value = start;
        int unit = 2;
        if (aggressive) {
            unit = 4;
        }

        int remaining = count;
        while (remaining > 0) {
            if (start < 0) {
                value = value + unit;
            } else {
                value = value - unit;
            }
            remaining = remaining - 1;
        }
        return value;
    }
}
