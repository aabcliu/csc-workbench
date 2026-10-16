public class WeightedAddLoop {
    public static int weightedAdd(int base, int steps, boolean heavy) {
        if (base < -90 || base > 90) {
            return -1;
        }
        if (steps < -40 || steps > 40) {
            return -1;
        }

        int result = base;
        int unit = 2;
        if (heavy) {
            unit = 3;
        }

        if (steps > 0) {
            int n = steps;
            while (n > 0) {
                result = result + unit;
                n = n - 1;
            }
        } else {
            int n = -steps;
            while (n > 0) {
                result = result - unit - 1;
                n = n - 1;
            }
        }
        return result;
    }
}
