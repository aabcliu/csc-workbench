public class BoundaryStressMeter {
    public static int meteredCharge(int days, int limit, int surge) {
        if (days < 0 || days > 500) {
            return -1;
        }
        if (limit < 0 || limit > 500) {
            return -1;
        }
        if (surge < 0 || surge > 500) {
            return -1;
        }

        int charge = 0;
        int remaining = days;
        while (remaining > 0) {
            if (limit > 200) {
                charge = charge + 6;
            } else {
                charge = charge + 1;
            }

            if (days > 200) {
                charge = charge + 2;
            }

            if (surge > 200) {
                charge = charge + 4;
            }

            if (charge > 220) {
                return 220;
            }

            remaining = remaining - 3;
        }

        if (charge < 0) {
            return 0;
        }
        return charge;
    }
}
