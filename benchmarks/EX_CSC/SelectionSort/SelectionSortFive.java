public class SelectionSortFive {

    public static boolean selectionSort5(int a, int b, int c, int d, int e) {
        int min = a;
        if (b < min){
            min = b;
        }
        if (c < min){
            min = c;
        }
        if (d < min){
            min = d;
        }
        if (e < min){
            min = e;
        }

        if (min == b) {
            int temp = a;
            a = b;
            b = temp;
        } else if (min == c) {
            int temp = a;
            a = c;
            c = temp;
        } else if (min == d) {
            int temp = a;
            a = d;
            d = temp;
        } else if (min == e) {
            int temp = a;
            a = e;
            e = temp;
        }

        min = b;
        if (c < min){
            min = c;
        }
        if (d < min){
            min = d;
        }
        if (e < min){
            min = e;
        }

        if (min == c) {
            int temp = b;
            b = c;
            c = temp;
        } else if (min == d) {
            int temp = b;
            b = d;
            d = temp;
        } else if (min == e) {
            int temp = b;
            b = e;
            e = temp;
        }

        min = c;
        if (d < min){
            min = d;
        }
        if (e < min){
            min = e;
        }


        if (min == d) {
            int temp = c;
            c = d;
            d = temp;
        } else if (min == e) {
            int temp = c;
            c = e;
            e = temp;
        }

        if (d > e) {
            int temp = d;
            d = e;
            e = temp;
        }

        return a <= b && b <= c && c <= d && d <= e;
    }
}