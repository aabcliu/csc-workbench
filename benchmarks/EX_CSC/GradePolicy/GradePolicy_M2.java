public class GradePolicy {
    public static int gradePolicy(int exam, int penalty, boolean strictMode) {
        if (exam < 0 || exam > 100) {
            return -1;
        }
        if (penalty < 0 || penalty >= 20) {
            return -1;
        }

        int score = exam;
        int remaining = penalty;
        while (remaining > 0) {
            if (exam >= 60) {
                score = score - 1;
            } else {
                score = score - 2;
            }
            remaining = remaining - 1;
        }

        if (strictMode && exam < 50) {
            score = score - 5;
        }
        if (score < 0) {
            return 0;
        }
        return score;
    }
}
