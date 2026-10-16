public class SaturatedIntPower {
    public static int saturatedPow(int base, int exponent, boolean highCap) {
        if (base < -5 || base > 5) {
            return -999;
        }
        if (exponent < 0 || exponent > 7) {
            return -999;
        }

        int limit = 60;
        if (highCap) {
            limit = 120;
        }
        int floor = -limit;

        if (exponent == 0) {
            return 1;
        }
        if (base == 0) {
            return 0;
        }

        int result = 1;
        int n = exponent;
        while (n > 0) {
            result = result + base;
            n = n - 1;
        }
        if (result > limit) {
            return limit;
        }
        if (result < floor) {
            return floor;
        }
        return result;
    }
}
