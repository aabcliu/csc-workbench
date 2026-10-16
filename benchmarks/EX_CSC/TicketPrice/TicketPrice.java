public class TicketPrice {
    public static int ticketPrice(int base, int distance, boolean express) {
        if (base < -100 || base > 100) {
            return -1;
        }
        if (distance < -50 || distance > 50) {
            return -1;
        }

        int price = base;
        int unit = 1;
        if (express) {
            unit = 2;
        }

        if (distance > 0) {
            int n = distance;
            while (n > 0) {
                price = price + unit;
                n = n - 1;
            }
        } else {
            int n = -distance;
            while (n > 0) {
                price = price - unit;
                n = n - 1;
            }
        }
        return price;
    }
}
