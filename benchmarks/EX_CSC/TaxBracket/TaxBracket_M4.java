public class TaxBracket {
    public static int computeTax(int income, int deduction, boolean resident) {
        if (income < 0 || income > 80) {
            return -1;
        }
        if (deduction < 0 || deduction > 80) {
            return -1;
        }

        int taxable = income;
        int used = 0;
        if (income >= deduction) {
            int n = deduction;
            while (n > 0) {
                taxable = taxable - 2;
                used = used + 1;
                n = n - 1;
            }
        } else {
            int n = income;
            while (n > 0) {
                taxable = taxable - 1;
                used = used + 1;
                n = n - 1;
            }
        }

        int tax = taxable;
        if (resident) {
            tax = tax + used;
        }
        return tax;
    }
}
