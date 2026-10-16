public class OddEvenSortFive {
    public static boolean oddEvenSortCheck(int a, int b, int c, int d, int e) {
        int round = 0;
        while (round < 5) {
            if (round % 2 == 0) {
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
            } else {
                if (b > c) {
                    int temp = b;
                    b = d;
                    c = temp;
                }
                if (d > e) {
                    int temp = d;
                    d = e;
                    e = temp;
                }
            }
            round++;
        }
        return a <= b && b <= c && c <= d && d <= e;
    }
}
