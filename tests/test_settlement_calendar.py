import unittest
from datetime import date
from unittest.mock import patch

from utils import calc_contract_period_charge, months_elapsed
from jungsan import _apply_month_adjustments, _is_manager_account, _jungsan_decorate_rows
from building import _normalize_sukum_acct_gb
from checkout import _checkout_tenant_adjustment_total


class SettlementCalendarTests(unittest.TestCase):
    def test_account_owner_is_independent_from_management_type(self):
        self.assertFalse(_is_manager_account({"mgmt_gb": "R", "sukum_acct_gb": "O"}))
        self.assertTrue(_is_manager_account({"mgmt_gb": "G", "sukum_acct_gb": "M"}))
        self.assertEqual(_normalize_sukum_acct_gb("", "R"), "M")
        self.assertEqual(_normalize_sukum_acct_gb("", "G"), "O")

    @patch("checkout.db.query_one")
    def test_checkout_includes_saved_tenant_adjustments(self, query_one):
        query_one.return_value = {"amt": 90000}
        amt = _checkout_tenant_adjustment_total(
            "0508", "0088", "303", "16", date(2026, 5, 2), date(2026, 7, 31)
        )
        self.assertEqual(amt, 90000)
        self.assertIn("MANAGE_DISCOUNT", query_one.call_args.args[0])
        self.assertIn("MANAGE_WAIVE", query_one.call_args.args[0])
        args = query_one.call_args.args[1]
        self.assertEqual(args[-2:], ("2026-05-01", "2026-07-01"))

    def test_month_end_due_day_clamps_to_february(self):
        self.assertEqual(months_elapsed(date(2025, 1, 31), date(2025, 2, 27)), 0)
        self.assertEqual(months_elapsed(date(2025, 1, 31), date(2025, 2, 28)), 1)

    def test_month_end_due_day_handles_leap_year(self):
        self.assertEqual(months_elapsed(date(2024, 1, 31), date(2024, 2, 28)), 0)
        self.assertEqual(months_elapsed(date(2024, 1, 31), date(2024, 2, 29)), 1)

    def test_31st_clamps_to_30_day_month(self):
        self.assertEqual(months_elapsed(date(2025, 3, 31), date(2025, 4, 29)), 0)
        self.assertEqual(months_elapsed(date(2025, 3, 31), date(2025, 4, 30)), 1)

    def test_normal_due_day_still_uses_anniversary(self):
        self.assertEqual(months_elapsed(date(2025, 1, 15), date(2025, 2, 14)), 0)
        self.assertEqual(months_elapsed(date(2025, 1, 15), date(2025, 2, 15)), 1)

    def test_negative_checkout_adjustment_is_printed(self):
        row = {
            "hosu": "203", "ipju_nm": "세입자", "ipju_dt": date(2025, 1, 1),
            "napbu_gb": "A", "rent_amt": 310000, "manage_amt": 70000,
            "bojung_amt": 0, "ipkum_amt": -206667, "sil_amt": 0,
            "dache_amt": 0, "dache_gb": "", "rent_calc": 310000,
            "misu_amt": 0, "manage_desc": "퇴실(05-07)", "is_empty": False,
        }
        _jungsan_decorate_rows([row])
        self.assertEqual(row["ipkum_disp"], "-206,667")
        self.assertEqual(row["jisi_disp"], "퇴실(05-07)")

    @patch("jungsan._month_adjustment_map")
    def test_owner_rent_discount_reduces_misu_only(self, adjustment_map):
        adjustment_map.return_value = {("101", "01"): {
            "adj_kind": "RENT_DISCOUNT", "adj_amt": 100000,
            "burden_gb": "O", "reason": "한시 감면",
        }}
        row = {"hosu": "101", "ipju_seq": "01", "misu_amt": 300000,
               "manage_desc": "미납", "is_empty": False}
        _apply_month_adjustments([row], "1139", "0004", date(2026, 8, 1))
        self.assertEqual(row["misu_amt"], 200000)
        self.assertEqual(row["company_pay_amt"], 0)
        self.assertEqual(row["adjustment_items"][0]["adj_kind"], "RENT_DISCOUNT")

    @patch("jungsan._month_adjustment_map")
    def test_company_rent_discount_keeps_owner_payout(self, adjustment_map):
        adjustment_map.return_value = {("101", "01"): {
            "adj_kind": "RENT_DISCOUNT", "adj_amt": 100000,
            "burden_gb": "C", "reason": "",
        }}
        row = {"hosu": "101", "ipju_seq": "01", "misu_amt": 300000,
               "manage_desc": "미납", "is_empty": False}
        _apply_month_adjustments([row], "1139", "0004", date(2026, 8, 1))
        self.assertEqual(row["misu_amt"], 200000)
        self.assertEqual(row["company_pay_amt"], 100000)

    @patch("jungsan._month_adjustment_map")
    def test_rent_discount_caps_substitute_to_discounted_rent(self, adjustment_map):
        adjustment_map.return_value = {("303", "16"): {
            "adj_kind": "RENT_DISCOUNT", "adj_amt": 30000,
            "burden_gb": "O", "reason": "",
        }}
        row = {"hosu": "303", "ipju_seq": "16", "rent_calc": 300000,
               "rent_amt": 300000, "sil_amt": 0, "dache_amt": 300000,
               "dache_gb": "대체", "misu_amt": 700000,
               "manage_desc": "", "is_empty": False}
        _apply_month_adjustments([row], "0508", "0088", date(2026, 7, 1))
        self.assertEqual(row["dache_amt"], 270000)
        self.assertEqual(row["misu_amt"], 670000)
        self.assertEqual(row["dache_gb"], "대체")

    @patch("jungsan._month_adjustment_map")
    def test_rent_discount_without_payment_remains_unpaid(self, adjustment_map):
        adjustment_map.return_value = {("303", "16"): {
            "adj_kind": "RENT_DISCOUNT", "adj_amt": 30000,
            "burden_gb": "O", "reason": "",
        }}
        row = {"hosu": "303", "ipju_seq": "16", "rent_calc": 300000,
               "rent_amt": 300000, "sil_amt": 0, "dache_amt": 0,
               "dache_gb": "", "misu_amt": 300000,
               "manage_desc": "", "is_empty": False}
        _apply_month_adjustments([row], "0508", "0088", date(2026, 7, 1))
        self.assertEqual(row["misu_amt"], 270000)
        self.assertEqual(row["dache_amt"], 0)

    @patch("jungsan._month_adjustment_map")
    def test_split_burden_adjustments_are_added_together(self, adjustment_map):
        adjustment_map.return_value = {("303", "16"): [
            {"adj_id": 1, "adj_kind": "RENT_DISCOUNT", "adj_amt": 50000,
             "burden_gb": "O", "reason": "건물주 부담"},
            {"adj_id": 2, "adj_kind": "RENT_DISCOUNT", "adj_amt": 50000,
             "burden_gb": "C", "reason": "관리주체 부담"},
        ]}
        row = {"hosu": "303", "ipju_seq": "16", "rent_calc": 300000,
               "rent_amt": 300000, "sil_amt": 0, "dache_amt": 300000,
               "dache_gb": "대체", "misu_amt": 300000,
               "manage_desc": "", "is_empty": False}
        _apply_month_adjustments([row], "0508", "0088", date(2026, 7, 1))
        self.assertEqual(row["adjustment_amt"], 100000)
        self.assertEqual(row["misu_amt"], 200000)
        self.assertEqual(row["dache_amt"], 200000)
        self.assertEqual(row["company_pay_amt"], 50000)

    @patch("utils.db.execute")
    @patch("utils.db.query")
    def test_contract_charge_splits_at_rate_change(self, query, _execute):
        query.return_value = [
            {"effective_dt": date(2025, 1, 31), "rent_amt": 300000, "manage_amt": 0},
            {"effective_dt": date(2025, 2, 28), "rent_amt": 200000, "manage_amt": 0},
        ]
        amt = calc_contract_period_charge(
            "1139", "0004", "308", "07",
            date(2025, 1, 31), date(2025, 4, 30), 200000, 0,
        )
        self.assertEqual(amt, 700000)


if __name__ == "__main__":
    unittest.main()
