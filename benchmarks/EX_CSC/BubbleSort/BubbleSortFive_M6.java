public class BubbleSortFive {

    public static boolean bubbleSort5(int a, int b, int c, int d, int e) {
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
        if (c > d) {
            int temp = c;
            c = d;
            d = temp;
        }
        if (d > e) {
            int temp = d;
            d = e;
            e = temp;
        }

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
        if (c > d) {
            int temp = c;
            c = d;
            d = temp;
        }

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
            a = c;
            b = temp;
        }
        return a <= b && b <= c && c <= d && d <= e;
    }
}