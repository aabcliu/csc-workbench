public class MedianOfSix {
    public static int medianOfSix(int a, int b, int c, int d, int e, int f) {
        if (a > b) {
            int temp = a;
            a = b;
            b = temp;
        }
        if (b > c) {
            int temp = b;
            b = c;
            c = temp;
        }
        if (a > b) {
            int temp = a;
            a = b;
            b = temp;
        }

        if (d > c) {
            int temp = c;
            c = d;
            d = temp;
            if (b > c) {
                temp = b;
                b = c;
                c = temp;
            }
            if (a > b) {
                temp = a;
                a = b;
                b = temp;
            }
        }

        if (e < c) {
            int temp = c;
            c = e;
            e = temp;
            if (b > c) {
                temp = b;
                b = c;
                c = temp;
            }
            if (a > b) {
                temp = a;
                a = b;
                b = temp;
            }
        }

        if (f < c) {
            int temp = c;
            c = f;
            f = temp;
            if (b > c) {
                temp = b;
                b = c;
                c = temp;
            }
            if (a > b) {
                temp = a;
                a = b;
                b = temp;
            }
        }

        return c;
    }
}
