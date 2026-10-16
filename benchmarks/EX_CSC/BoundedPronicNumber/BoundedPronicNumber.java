public class BoundedPronicNumber {
    public static int classifyPronic(int number) {
        if (number < 0 || number > 50) {
            return -1;
        }
        if (number == 0) {
            return 1;
        }

        int i = 0;
        while (i <= number) {
            if (i * (i + 1) == number && i != number) {
                return 1;
            }
            i = i + 1;
        }
        return 0;
    }
}
