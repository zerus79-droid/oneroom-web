"""_search_tenants_by_name: contains must not be masked by exact tier."""
import importlib.util
import sys
import types
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch


def _load_payments_with_stubs():
    """Load payments.py without installing Flask/openpyxl (stubs only)."""
    root = Path(__file__).resolve().parents[1]
    # Stub heavy deps before import
    for name in (
        "flask",
        "openpyxl",
        "openpyxl.styles",
        "db",
        "app_instance",
        "utils",
    ):
        if name not in sys.modules:
            sys.modules[name] = MagicMock()
    # Minimal real-ish stubs for symbols payments imports from utils
    utils = sys.modules["utils"]
    for attr in (
        "buildings_and_rooms",
        "building_label",
        "clamp_date_str",
        "first_date_for_tenant",
        "fmt_bunji_pair",
        "fmt_date",
        "iso_min_date",
        "login_required",
        "lookup_current_tenant",
        "make_pager",
        "pad_ipju_seq",
        "to_int_amt",
        "paginate",
        "parse_bunji_src",
        "tenant_key",
    ):
        setattr(utils, attr, MagicMock(name=attr))
    sys.modules["app_instance"].app = MagicMock()
    path = root / "payments.py"
    spec = importlib.util.spec_from_file_location("payments_under_test", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


payments = _load_payments_with_stubs()


class TestSearchTenantsByName(unittest.TestCase):
    def test_single_like_query_returns_exact_and_parenthetical(self):
        """주미경 exact + 김나영(주미경) contains both; one query only."""
        rows = [
            {
                "bunji1": "0523",
                "bunji2": "0008",
                "hosu": "304",
                "ipju_seq": "1",
                "ipju_nm": "주미경",
                "ipju_dt": "2020-01-01",
                "out_dt": "0000-00-00",
                "juso": "연수동",
            },
            {
                "bunji1": "0508",
                "bunji2": "0088",
                "hosu": "302",
                "ipju_seq": "1",
                "ipju_nm": "김나영(주미경)",
                "ipju_dt": "2021-01-01",
                "out_dt": "0000-00-00",
                "juso": "풍요",
            },
        ]
        with patch.object(payments.db, "query", return_value=rows) as q:
            got = payments._search_tenants_by_name("주미경", "current")
        self.assertEqual(len(got), 2)
        names = {r["ipju_nm"] for r in got}
        self.assertEqual(names, {"주미경", "김나영(주미경)"})
        self.assertEqual(q.call_count, 1)
        sql, params = q.call_args[0][0], q.call_args[0][1]
        self.assertIn("LIKE %s", sql)
        self.assertIn("CASE", sql)
        self.assertIn("TRIM(d.ipju_nm)=%s", sql)
        self.assertEqual(params[0], "%주미경%")
        self.assertEqual(params[1], "주미경")
        self.assertEqual(params[2], "주미경%")
        self.assertIn("out_dt", sql)

    def test_no_early_return_tiers(self):
        """Must not issue separate exact/prefix queries that return early."""
        with patch.object(payments.db, "query", return_value=[]) as q:
            payments._search_tenants_by_name("홍길동", "all")
        self.assertEqual(q.call_count, 1)
        sql = q.call_args[0][0]
        self.assertIn("d.ipju_nm LIKE %s", sql)
        self.assertNotIn("WHERE TRIM(d.ipju_nm)=%s", sql)


if __name__ == "__main__":
    unittest.main()
