public class BoundedBinaryPalindromeClassifier {
    public static int classifyBinaryPalindrome(int number) {
        if (number <= 0 || number > 127) {
            return -1;
        }
        int left = 64;
        int right = 1;
        int mismatch = 0;
        while (left > right) {
            int leftBit = 0;
            int rightBit = 0;
            if (number / left % 2 == 1) {
                leftBit = 1;
            }
            if (number / right % 2 == 1) {
                rightBit = 1;
            }
            if (leftBit != rightBit) {
                mismatch = 1;
            }
            left = left / 2;
            right = right * 2;
        }
        if (mismatch == 0) {
            return 1;
        }
        return 0;
    }
}
