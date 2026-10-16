public class TwoBucketLoop {
    public static int twoBucket(int amount, int count, boolean premium) {
        if (amount < 0 || amount > 100) {
            return -1;
        }
        if (count < 0 || count > 20) {
            return -1;
        }

        int value = amount;
        int remaining = count;
        while (remaining > 0) {
            if (amount < 50) {
                value = value + 3;
            } else {
                value = value + 1;
            }
            remaining = remaining - 1;
        }

        if (premium) {
            value = value + 5;
        }
        if (value > 120) {
            return 120;
        }
        return value + 1;
    }
}
