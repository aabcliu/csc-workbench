public class BoundedPrimeClassifier {
    public static int classifyPrime(int number) {
        if (number < 2 || number > 50) {
            return -1;
        }
        int divisor = 1;
        int divisorCount = 0;
        while (divisor <= number) {
            if (number % divisor == 0) {
                divisorCount = divisorCount + 1;
            }
            divisor = divisor + 1;
        }
        if (divisorCount >= 2) {
            return 1;
        }
        return 0;
    }
}
