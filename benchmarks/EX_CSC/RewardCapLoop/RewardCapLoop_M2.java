public class RewardCapLoop {
    public static int rewardCap(int points, int rounds, boolean fast) {
        if (points < 0 || points > 110) {
            return -1;
        }
        if (rounds < 0 || rounds >= 16) {
            return -1;
        }

        int value = points;
        int bonus = 1;
        if (fast) {
            bonus = 4;
        }

        int remaining = rounds;
        while (remaining > 0) {
            if (points < 70) {
                value = value + bonus;
            } else {
                value = value + 1;
            }
            remaining = remaining - 1;
        }

        if (value > 120) {
            return 120;
        }
        return value;
    }
}
