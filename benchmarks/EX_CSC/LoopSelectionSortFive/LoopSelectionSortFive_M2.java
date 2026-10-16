public class LoopSelectionSortFive {
    public static boolean selectionSortCheck(int a, int b, int c, int d, int e) {
        int position = 0;
        while (position < 3) {
            if (position == 0) {
                if (b < a) {
                    int temp = a;
                    a = b;
                    b = temp;
                }
                if (c < a) {
                    int temp = a;
                    a = c;
                    c = temp;
                }
                if (d < a) {
                    int temp = a;
                    a = d;
                    d = temp;
                }
                if (e < a) {
                    int temp = a;
                    a = e;
                    e = temp;
                }
            } else if (position == 1) {
                if (c < b) {
                    int temp = b;
                    b = c;
                    c = temp;
                }
                if (d < b) {
                    int temp = b;
                    b = d;
                    d = temp;
                }
                if (e < b) {
                    int temp = b;
                    b = e;
                    e = temp;
                }
            } else if (position == 2) {
                if (d < c) {
                    int temp = c;
                    c = d;
                    d = temp;
                }
                if (e < c) {
                    int temp = c;
                    c = e;
                    e = temp;
                }
            } else {
                if (e < d) {
                    int temp = d;
                    d = e;
                    e = temp;
                }
            }
            position++;
        }
        return a <= b && b <= c && c <= d && d <= e;
    }
}
