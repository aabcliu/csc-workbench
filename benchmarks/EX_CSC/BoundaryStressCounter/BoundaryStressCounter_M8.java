public class BoundaryStressCounter {
    public static int boundedScore(int steps, int quota, int boost) {
        if (steps < 0 || steps > 500) {
            return -1;
        }
        if (quota < 0 || quota > 500) {
            return -1;
        }
        if (boost < 0 || boost > 500) {
            return -1;
        }

        int score = 0;
        int n = steps;
        while (n > 0) {
            if (quota > 200) {
                score = score + 5 + quota / 500;
            } else {
                score = score + 1;
            }
            if (steps > 200) {
                score = score + 2;
            }
            if (boost > 200) {
                score = score + 4;
            }
            n = n - 3;
        }

        if (score > 220) {
            return 220;
        }
        return score;
    }
}
