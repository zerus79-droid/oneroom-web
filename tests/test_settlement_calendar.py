import unittest
from datetime import date
from unittest.mock import patch

from utils import calc_contract_period_charge, months_elapsed
from jungsan import (
    _apply_month_adjustments,
    _is_manager_account,
    _is_item_manager_account,
    _jungsan_decorate_rows,
)
from jungsan_engine import (
    _cap_dache_to_rent_shortfall,
    _jungsan_calendar_misu_amt,
    _rent_ipkum_for_pay,
)
from building import _normalize_sukum_acct_gb
from checkout import _checkout_tenant_adjustment_total, _period_mm_dd


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

    def test_checkout_period_includes_start_and_checkout_dates(self):
        self.assertEqual(
            _period_mm_dd(date(2025, 8, 17), date(2026, 8, 31)),
            (12, 15),
        )
        self.assertEqual(
            _period_mm_dd(date(2026, 8, 31), date(2026, 8, 31)),
            (0, 1),
        )

    @patch("utils.db.execute")
    @patch("utils.db.query")
    def test_contract_charge_uses_30_day_basis_only_for_residual_days(self, query, _execute):
        query.return_value = []
        half_month = calc_contract_period_charge(
            "1139", "0004", "304", "15",
            date(2026, 8, 17), date(2026, 8, 31), 300000, 100000,
        )
        full_february_cycle = calc_contract_period_charge(
            "1139", "0004", "304", "15",
            date(2025, 1, 31), date(2025, 2, 27), 300000, 100000,
        )
        self.assertEqual(half_month, 200000)
        self.assertEqual(full_february_cycle, 400000)

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
    def test_contract_charge_splits_at_rate_change_including_checkout_day(self, query, _execute):
        query.return_value = [
            {"effective_dt": date(2025, 1, 31), "rent_amt": 300000, "manage_amt": 0},
            {"effective_dt": date(2025, 2, 28), "rent_amt": 200000, "manage_amt": 0},
        ]
        amt = calc_contract_period_charge(
            "1139", "0004", "308", "07",
            date(2025, 1, 31), date(2025, 4, 30), 200000, 0,
        )
        self.assertEqual(amt, 706700)


if __name__ == "__main__":
    unittest.main()


class AccountSubjectAndCalendarMisuTests(unittest.TestCase):
    def test_item_manager_account_uses_per_item_flag(self):
        b = {
            "sukum_acct_gb": "O",
            "sukum_rent_acct_gb": "M",
            "sukum_bojung_acct_gb": "O",
            "sukum_manage_acct_gb": "M",
        }
        self.assertTrue(_is_item_manager_account(b, "rent"))
        self.assertFalse(_is_item_manager_account(b, "bojung"))
        self.assertTrue(_is_item_manager_account(b, "manage"))

    def test_dache_cap_limits_to_rent_shortfall(self):
        # 월세+관리비를 대체에 넣어도 월세 부족분까지만
        self.assertEqual(_cap_dache_to_rent_shortfall(300000, 0, 400000), 300000)
        self.assertEqual(_cap_dache_to_rent_shortfall(300000, 100000, 400000), 200000)
        self.assertEqual(_cap_dache_to_rent_shortfall(300000, 300000, 50000), 0)

    def test_calendar_misu_ignores_dache_and_carries(self):
        # 후불: 입주월 제외 → 3·4·5월 3개월 due. paid_sil=500k(2개월분) → 5월분 미수
        # dache는 paid_sil에 포함되지 않음
        misu = _jungsan_calendar_misu_amt(
            250000, 0, date(2026, 2, 10), date(2026, 5, 31), "B",
            paid_sil=500000,
        )
        self.assertEqual(misu, 250000)

    def test_calendar_misu_prepaid_includes_move_in_month(self):
        # 선불: 2·3·4월 3개월 due, paid_sil=500k → 4월분 미수 250k
        misu = _jungsan_calendar_misu_amt(
            200000, 50000, date(2026, 2, 5), date(2026, 4, 30), "A",
            paid_sil=500000,
        )
        self.assertEqual(misu, 250000)

    def test_calendar_misu_amount_running_balance(self):
        # 후불: 2 months due, paid_sil=250000 once → misu=250000
        misu = _jungsan_calendar_misu_amt(
            250000, 0, date(2026, 2, 1), date(2026, 4, 30), "B",
            paid_sil=250000,
        )
        self.assertEqual(misu, 250000)
        # 한 달에 2개월분을 넣어도 금액으로 차감 (달력월 이진 clear 아님)
        misu2 = _jungsan_calendar_misu_amt(
            250000, 0, date(2026, 2, 1), date(2026, 4, 30), "B",
            paid_sil=500000,
        )
        self.assertEqual(misu2, 0)
        # 부분 실입은 잔액 미수
        misu3 = _jungsan_calendar_misu_amt(
            250000, 0, date(2026, 3, 1), date(2026, 4, 30), "B",
            paid_sil=100000,
        )
        self.assertEqual(misu3, 150000)

    def test_calendar_misu_b04_fully_paid(self):
        # B04-style: 25 due months × 250k - 6,250,000 sil → 0
        # 후불: 입주 2024-07 → 청구 2024-08 ~ 2026-08 = 25개월
        misu = _jungsan_calendar_misu_amt(
            250000, 0, date(2024, 7, 1), date(2026, 8, 31), "B",
            paid_sil=6250000,
        )
        self.assertEqual(misu, 0)

    def test_rent_ipkum_counts_dache_like_sil(self):
        self.assertEqual(_rent_ipkum_for_pay(100000, 200000, 300000), 300000)
        self.assertEqual(_rent_ipkum_for_pay(0, 270000, 270000), 270000)

    @patch("jungsan._month_adjustment_map")
    def test_dache_capped_without_rent_adjustment(self, adjustment_map):
        adjustment_map.return_value = {}
        row = {"hosu": "B04", "ipju_seq": "01", "rent_calc": 250000,
               "rent_amt": 250000, "sil_amt": 0, "dache_amt": 350000,
               "dache_gb": "대체", "misu_amt": 0, "is_empty": False}
        _apply_month_adjustments([row], "0001", "0001", date(2026, 8, 1))
        self.assertEqual(row["dache_amt"], 250000)
        self.assertEqual(row["dache_gb"], "대체")


