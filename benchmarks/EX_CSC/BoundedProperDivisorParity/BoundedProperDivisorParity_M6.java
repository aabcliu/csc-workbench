public class BoundedProperDivisorParity {
    public static int classifyProperDivisorParity(int number) {
        if (number < 1 || number > 50) {
            return -1;
        }
        int divisor = 1;
        int sum = 0;
        while (divisor < number) {
            if (number % divisor == 0) {
                sum = sum + divisor;
            }
            divisor = divisor + 1;
        }
        if (sum % 2 == 0) {
            return 0;
        }
        return 0;
    }
}
