public class SubtractLoop {
    public static int subtractLoop(int x, int y) {
        if (x < -100 || x > 100) {
            return -1;
        }
        if (y < -100 || y > 100) {
            return -1;
        }

        int result = x;
        if (y > 0) {
            int n = y;
            while (n > 0) {
                result = result - 1;
                n = n - 1;
            }
        } else {
            int n = -y;
            while (n > 0) {
                result = result + 2;
                n = n - 1;
            }
        }
        return result;
    }
}
