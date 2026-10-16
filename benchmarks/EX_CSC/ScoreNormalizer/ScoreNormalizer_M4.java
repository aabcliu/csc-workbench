public class ScoreNormalizer {
    public static int normalizeScore(int rawScore, int bonus, boolean strictMode) {
        if (rawScore < 0 || rawScore > 100) {
            return -1;
        }
        if (bonus < 0 || bonus > 20) {
            return -1;
        }

        int score = rawScore;
        int remainingBonus = bonus;
        while (remainingBonus > 0) {
            if (score < 90) {
                score -= 1;
            } else {
                score = score + 1;
            }
            remainingBonus--;
        }

        if (strictMode && rawScore < 50) {
            score -= 10;
        }

        if (score > 100) {
            return 100;
        }
        if (score < 0) {
            return 0;
        }
        return score;
    }
}
