public class TailRotateSortFive {
    public static boolean tailRotateSort(int a, int b, int c, int d, int e) {
        int pass = 0;
        while (pass < 2) {
            if (a > b) {
                int temp = a;
                a = b;
                b = temp;
            }
            if (c > d) {
                int temp = c;
                c = d;
                d = temp;
            }
            if (b > c) {
                int temp = b;
                b = c;
                c = temp;
            }
            if (d > e) {
                int temp = d;
                d = e;
                e = temp;
            }
            pass = pass + 1;
        }
        return a <= b && b <= c && c <= d && d <= e;
    }
}
