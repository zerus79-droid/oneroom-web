import unittest

from payments import _annotate_payment_amount_cols, _split_char01_payment


class PaymentPrintSplitTests(unittest.TestCase):
    def test_full_due_splits_rent_then_manage(self):
        self.assertEqual(_split_char01_payment(400000, 0, 340000, 60000), (340000, 60000))

    def test_partial_stays_in_rent(self):
        self.assertEqual(_split_char01_payment(300000, 0, 340000, 60000), (300000, 0))

    def test_small_dache_is_rent_only(self):
        self.assertEqual(_split_char01_payment(0, 40000, 340000, 60000), (40000, 0))

    def test_overpay_extra_goes_to_rent(self):
        self.assertEqual(_split_char01_payment(1000000, 0, 340000, 60000), (940000, 60000))


class PaymentAmountColTests(unittest.TestCase):
    def test_deposit_row_fills_deposit_not_rent(self):
        row = {"sukum_char": "03", "su_sil_amt": 400000, "su_dache_amt": 0}
        tot = _annotate_payment_amount_cols([row])
        self.assertEqual(row["deposit_disp"], 400000)
        self.assertEqual(row["rent_manage_disp"], 0)
        self.assertEqual(tot["total_deposit"], 400000)
        self.assertEqual(tot["total_sil"], 400000)

    def test_char01_keeps_full_amount_in_rent_manage(self):
        row = {
            "sukum_char": "01",
            "su_sil_amt": 800000,
            "su_dache_amt": 0,
            "rent_amt": 340000,
            "manage_amt": 60000,
        }
        tot = _annotate_payment_amount_cols([row])
        self.assertEqual(row["deposit_disp"], 0)
        self.assertEqual(row["rent_manage_disp"], 800000)
        self.assertEqual(tot["total_rent_manage"], 800000)
