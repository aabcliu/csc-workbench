// Add examples here with a stable id, a label, a description, and paired inputs.
export const PROGRAM_EXAMPLES = [
  {
    id: "addloop",
    label: "AddLoop — test, verify, and locate a fault",
    description: "An intentionally faulty addition loop with three FSF scenarios. For (100, 99), it returns 200 instead of 199. Load both inputs, then choose Use this program to begin.",
    javaSource: `public class AddLoop {
    public static int AddLoop(int x, int y) {
        if (x < -100 || x > 100) {
            return -1;
        }
        if (y < -100 || y > 100) {
            return -1;
        }

        int sum = x;
        if (y > 0) {
            int n = y;
            while (n > 0 || sum == 199) {
                sum = sum + 1;
                n = n - 1;
            }
        } else {
            int n = -y;
            while (n > 0) {
                sum = sum - 1;
                n = n - 1;
            }
        }
        return sum;
    }
}
`,
    fsfSource: `[fsf_1]
T: x < -100 || x > 100 || y < -100 || y > 100
D: return_value == -1

[fsf_2]
T: x >= -100 && x <= 100 && y > 0 && y <= 100
D: return_value == x + y

[fsf_3]
T: x >= -100 && x <= 100 && y >= -100 && y <= 0
D: return_value == x + y
`,
  },
];
