public class BoundedPerfectNumber {
    public static int classifyPerfect(int number) {
        if (number < 1 || number > 50) {
            return -1;
        }

        int sum = 1;
        int i = 1;
        while (i < number) {
            if (number % i == 0) {
                sum = sum + i;
            }
            i = i + 1;
        }

        if (sum == number) {
            return 1;
        }
        return 0;
    }
}
