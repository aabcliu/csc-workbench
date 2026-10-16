public class BoundedAliquotClassifier {
    public static int classifyAliquot(int number) {
        if (number < 1 || number > 50) {
            return -1;
        }

        int sum = 0;
        int divisor = 1;
        while (divisor < number) {
            if (number % divisor == 0) {
                sum = sum + divisor;
            }
            divisor = divisor + 2;
        }

        if (sum == number) {
            return 1;
        }
        if (sum > number) {
            return 2;
        }
        return 0;
    }
}
