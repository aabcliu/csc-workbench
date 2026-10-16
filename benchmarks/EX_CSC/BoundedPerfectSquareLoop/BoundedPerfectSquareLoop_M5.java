public class BoundedPerfectSquareLoop {
    public static int classifyPerfectSquare(int number) {
        if (number < 0 || number > 100) {
            return -1;
        }
        int i = 0;
        while (i <= number) {
            if (i * i == number) {
                return 1;
            }
            i = i + 2;
        }
        return 0;
    }
}
