public class InventoryReorder {
    public static int reorderLevel(int stock, int demand, boolean capMode) {
        if (stock < 0 || stock >= 100) {
            return -1;
        }
        if (demand < 0 || demand > 15) {
            return -1;
        }

        int level = stock;
        int remaining = demand;
        while (remaining > 0) {
            if (stock < 80) {
                level = level + 2;
            } else {
                level = level + 1;
            }
            remaining = remaining - 1;
        }

        if (capMode && level > 95) {
            level = 95;
        }
        if (level > 100) {
            return 100;
        }
        return level;
    }
}
