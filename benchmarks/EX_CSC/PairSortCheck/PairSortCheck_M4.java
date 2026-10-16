public class PairSortCheck {
    public static boolean pairSortCheck(int a, int b, int c, int d, int e) {
        if (a > b) {
            int temp = a;
            a = c;
            b = temp;
        }
        if (d > e) {
            int temp = d;
            d = e;
            e = temp;
        }
        if (c > e) {
            int temp = c;
            c = e;
            e = temp;
        }
        if (c > d) {
            int temp = c;
            c = d;
            d = temp;
        }
        if (a > d) {
            int temp = a;
            a = d;
            d = temp;
        }
        if (a > c) {
            int temp = a;
            a = c;
            c = temp;
        }
        if (b > e) {
            int temp = b;
            b = e;
            e = temp;
        }
        if (b > d) {
            int temp = b;
            b = d;
            d = temp;
        }
        if (b > c) {
            int temp = b;
            b = c;
            c = temp;
        }
        return a <= b && b <= c && c <= d && d <= e;
    }
}
