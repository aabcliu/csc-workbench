public class WaterBillCaculator {
    public static int calcWaterFee(int tons) {
        int n = tons;
        int fee = 0;
        if(n >= 30){
            fee = n * 5;
        }else{
            while (n > 0) {
                if (n <= 3) {
                    fee += 3;
                } else if (n < 10) {
                    fee += 4;
                } else {
                    fee += 5;
                }
                n--;
            }
        }
        return fee;
    }
}
