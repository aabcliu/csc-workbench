public class AddLoop {
    public static int AddLoop(int x, int y) {
        if(x < -100 || x > 100){

            return -1;

        }
        if(y < -100 || y > 100){

            return -1;

        }
        int sum = x;
        if (y > 0) {

            int n = y;

            while (n > 0) {

                sum = sum + 1;
                n = n - 1;

            }
        }
        else {

            int n = -y;

            while (n > 0) {

                sum = sum - 1;
                n = n - 1;

            }

        }
        return sum;
    }


}