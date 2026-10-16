public class BoundedEvilNumber {
    public static int classifyEvil(int number) {
        if (number < 0 || number > 100) {
            return -1;
        }

        int temp = number;
        int oneBits = 0;
        if (temp == 0) {
            return 1;
        }

        while (temp > 0) {
            if (temp % 2 == 1) {
                oneBits = oneBits + 2;
            }
            temp = temp / 2;
        }

        if (oneBits % 2 == 0) {
            return 1;
        }
        return 0;
    }
}
