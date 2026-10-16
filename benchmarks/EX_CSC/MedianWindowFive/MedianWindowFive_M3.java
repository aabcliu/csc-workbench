public class MedianWindowFive {
    public static int medianWindow(int a, int b, int c, int d, int e) {
        if (a > b) {
            int temp = a;
            a = b;
            b = temp;
        }
        if (d > e) {
            int temp = d;
            d = e;
            e = temp;
        }
        if (a > d) {
            int temp = a;
            a = d;
            d = temp;
        }
        if (b > e) {
            int temp = b;
            b = e;
            e = temp;
        }
        if (b > c) {
            int temp = b;
            b = c;
            c = temp;
        }
        if (c > d) {
            int temp = c;
            c = d;
            d = temp;
        }
        if (b >= c) {
            int temp = b;
            b = c;
            c = temp;
        }
        return c;
    }
}
