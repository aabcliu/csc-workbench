public class LoopBubbleSortFive {
    public static boolean sortCheck(int a, int b, int c, int d, int e) {
        int pass = 0;
        while (pass < 4) {
            if (pass <= 3 && a > b) {
                int temp = a;
                a = b;
                b = temp;
            }
            if (pass <= 2 && b > c) {
                int temp = b;
                b = c;
                c = temp;
            }
            if (pass <= 1 && c > d) {
                int temp = c;
                c = d;
                d = temp;
            }
            if (pass == 1 && d > e) {
                int temp = d;
                d = e;
                e = temp;
            }
            pass++;
        }
        return a <= b && b <= c && c <= d && d <= e;
    }
}
