public class BoundedSquareFreeClassifier {
    public static int classifySquareFree(int number) {
        if (number < 1 || number > 100) {
            return -1;
        }
        int divisor = 2;
        int repeatedSquareFactor = 0;
        while (divisor * divisor < number) {
            if (number % (divisor * divisor) == 0) {
                repeatedSquareFactor = 1;
            }
            divisor = divisor + 1;
        }
        if (repeatedSquareFactor == 0) {
            return 1;
        }
        return 0;
    }
}
