public class BoundedPopcountDensity {
    public static int classifyDensity(int number) {
        if (number < 0 || number > 127) {
            return -1;
        }
        int temp = number;
        int bits = 0;
        while (temp > 0) {
            if (temp % 2 == 1) {
                bits = bits + 1;
            }
            temp = temp / 4;
        }
        if (bits <= 2) {
            return 0;
        }
        if (bits <= 4) {
            return 1;
        }
        return 2;
    }
}
