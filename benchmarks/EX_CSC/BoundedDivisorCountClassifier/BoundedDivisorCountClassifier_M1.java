public class BoundedDivisorCountClassifier {
    public static int classifyDivisorCount(int number) {
        if (number < 1 || number >= 50) {
            return -1;
        }
        int divisor = 1;
        int count = 0;
        while (divisor <= number) {
            if (number % divisor == 0) {
                count = count + 1;
            }
            divisor = divisor + 1;
        }
        if (count <= 2) {
            return 0;
        }
        if (count <= 4) {
            return 1;
        }
        return 2;
    }
}
