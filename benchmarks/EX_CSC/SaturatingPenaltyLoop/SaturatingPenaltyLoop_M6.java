public class SaturatingPenaltyLoop {
    public static int applyPenalty(int score, int penalty, boolean strict) {
        if (score < 0 || score > 120) {
            return -1;
        }
        if (penalty < 0 || penalty > 18) {
            return -1;
        }

        int value = score;
        int remaining = penalty;
        while (remaining > 0) {
            if (strict) {
                value = value - 3;
            } else {
                value = value - 2;
            }
            remaining = remaining - 1;
        }

        if (value < 0) {
            return 0;
        }
        return value + 1;
    }
}
