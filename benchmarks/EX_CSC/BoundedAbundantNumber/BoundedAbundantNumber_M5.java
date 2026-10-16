public class BoundedAbundantNumber {
    public static int classifyAbundant(int number) {
        if (number < 1 || number > 50) {
            return -1;
        }

        int sum = 1 + number;
        int i = 2;
        while (i <= number / 2) {
            if (number % i == 0) {
                sum = sum - i;
            }
            i = i + 1;
        }

        if (sum > 2 * number) {
            return 1;
        }
        return 0;
    }
}
