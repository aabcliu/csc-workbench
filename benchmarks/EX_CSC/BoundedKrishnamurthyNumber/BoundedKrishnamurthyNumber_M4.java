public class BoundedKrishnamurthyNumber {
    public static int classifyKrishnamurthy(int number) {
        if (number < 1 || number > 200) {
            return -1;
        }

        int original = number;
        int sum = 0;
        while (number > 0) {
            int digit = number % 10;
            int factorial = 0;
            int factor = 2;
            while (factor <= digit) {
                factorial = factorial * factor;
                factor = factor + 1;
            }
            sum = sum + factorial;
            number = number / 10;
        }

        if (sum == original) {
            return 1;
        }
        return 0;
    }
}
