public class BoundedBitTransitionClassifier {
    public static int classifyTransitions(int number) {
        if (number < 0 || number > 127) {
            return -1;
        }
        int divisor = 1;
        int previousBit = number % 2;
        int transitions = 0;
        int index = 1;
        while (index < 7) {
            divisor = divisor * 2;
            int currentBit = (number / divisor) % 2;
            if (currentBit != previousBit) {
                transitions = transitions + 1;
            }
            previousBit = currentBit;
            index = index + 1;
        }
        if (transitions <= 1) {
            return 0;
        }
        if (transitions < 3) {
            return 1;
        }
        return 2;
    }
}
