"""저장본은 최신 수금/계약/계산식과 분리. 업무 DB 쓰기 없이 확인한다."""
import copy
import json
import unittest
from datetime import date, datetime
from decimal import Decimal
from unittest.mock import patch

from flask import session
from app_instance import app
import jungsan


def snapshot_data():
    """실제 현재계산 엔진을 사전적재 자료로 호출해 저장용 fixture 생성."""
    data = jungsan._jungsan_build_preview(
        "1139", "0004", date(2026, 5, 31), list_mode=True, prefer_saved=False,
        preload={
            "skip_ensure_cols": True, "skip_saved": True, "skip_adjustments": True,
            "building": {"bunji1": "1139", "bunji2": "0004", "juso": "저장 당시 건물",
                         "mgmt_gb": "R", "sukum_acct_gb": "O", "man_cost": Decimal("30000")},
            "rooms": [{"hosu": "308", "ipju_nm": "보존테스트", "ipju_seq": "07",
                       "ipju_dt": date(2016, 11, 1), "rent_amt": 200000, "manage_amt": 80000,
                       "bojung_amt": 5000000, "napbu_gb": "B"},
                      {"hosu": "309", "ipju_nm": ""}],
            "pay_map": {("308", "07"): {"01": {"sil": 130700, "dache": 0}}},
            "sil_paid_map": {("308", "07"): 999999999}, "terms_map": {},
            "owner_suri": 12345, "jungke_cost": 0,
        },
    )
    data["summary"].update(paid_suri=9876, jisi_text="저장 당시 지시")
    data["suri_detail"] = [{"dt": "05-02", "amt": 12345, "amt_disp": "12,345", "desc": "저장 수리"}]
    return data


class SavedViewTests(unittest.TestCase):
    def setUp(self):
        ctx = app.test_request_context("/jungsan")
        ctx.push()
        self.addCleanup(ctx.pop)
        session.update(sabun="10001", grade="A")
        self.addCleanup(patch.stopall)
        self.query = patch("jungsan.db.query", side_effect=AssertionError("unexpected current DB read")).start()
        self.query_one = patch("jungsan.db.query_one", side_effect=AssertionError("unexpected current DB read")).start()
        patch("jungsan.db.execute", side_effect=AssertionError("unexpected write")).start()
        self.data = snapshot_data()
        self.header = {"jungsan_dt": datetime(2026, 5, 31), "jungsan_seq": "01",
                       "snapshot_json": jungsan._jungsan_encode_snapshot("1139", "0004", date(2026, 5, 31), self.data)}

    def read(self, prefer_saved=True):
        with patch("jungsan._jungsan_saved_header", return_value=self.header):
            return jungsan._jungsan_build_preview("1139", "4", "2026-05-31", prefer_saved=prefer_saved)

    def test_saved_and_default_do_not_read_current_inputs_or_recalculate(self):
        with patch("jungsan._jungsan_decorate_rows", side_effect=AssertionError("recalculated")), \
             patch("jungsan._ensure_g_cost_cols", side_effect=AssertionError("schema write")):
            for preference in (True, None):
                data = self.read(preference)
                self.assertEqual(data["source"], "saved")
                self.assertEqual(data["rows"][0]["ipkum_amt"], 130700)
                self.assertEqual(data["rows"][0]["rent_amt"], 200000)
                self.assertEqual(data["rows"][0]["ipju_dt"], "2016-11-01")
                for field in ("totals", "suri_detail", "manage_detail", "jungke_detail"):
                    self.assertEqual(data[field], self.data[field])
                for field in ("pay_amt", "ipkum_tot", "misu_tot", "paid_suri", "jisi_text"):
                    self.assertEqual(data["summary"][field], self.data["summary"][field])
        self.query.assert_not_called()
        self.query_one.assert_not_called()

    def test_saved_values_change_only_after_explicit_snapshot_replacement(self):
        before = self.read()
        current = copy.deepcopy(self.data)
        current["summary"].update(pay_amt=-206667, ipkum_tot=-206667, jisi_text="새 관리지시")
        current["rows"][0].update(ipkum_amt=-206667, ipkum_disp="-206,667", rent_amt=999999)
        current["building"].update(sukum_acct_gb="M", juso="현재 바뀐 건물")
        self.assertEqual(self.read(), before)
        self.header["snapshot_json"] = jungsan._jungsan_encode_snapshot("1139", "0004", date(2026, 5, 31), current)
        after = self.read()
        self.assertEqual(after["rows"][0]["ipkum_amt"], -206667)
        self.assertEqual(after["summary"]["jisi_text"], "새 관리지시")
        self.assertEqual(after["building"]["juso"], "현재 바뀐 건물")

    def test_bad_snapshot_is_not_silently_replaced_by_current_or_legacy(self):
        valid = json.loads(self.header["snapshot_json"])
        wrong_address = copy.deepcopy(valid)
        wrong_address["data"]["building"]["bunji2"] = "0088"
        wrong_month = copy.deepcopy(valid)
        wrong_month["data"]["as_of"] = "2026-04-30"
        for payload in ("", "{broken", "null", json.dumps({"version": 2, "data": valid["data"]}),
                        json.dumps(wrong_address), json.dumps(wrong_month), '{"version":1,"data":{}}'):
            with self.subTest(payload=payload[:60]):
                self.header["snapshot_json"] = payload
                with self.assertRaisesRegex(ValueError, "자동 대체하지 않습니다"):
                    self.read()
        self.query.assert_not_called()

    def test_legacy_uses_stored_fields_and_leaves_missing_income_unknown(self):
        self.header.update(snapshot_json=None, pay_amt=-206667, ipkum_tot=400000, misu_tot=-1234,
                           rent_tot=300000, manage_tot=70000, bojung_tot=5000000, first_amt=5000000,
                           man_cost=50000, owner_suri=10000, jungke_cost=0, jungke_desc="옛 메모")
        self.query.side_effect = None
        self.query.return_value = [{"hosu": "308", "ipju_nm": "옛 이름", "ipju_seq": "07",
                                   "ipju_dt": date(2016, 11, 1), "bojung_amt": 5000000,
                                   "rent_amt": 300000, "manage_amt": 300000, "misu_amt": -1234,
                                   "manage_desc": "퇴실(05-14)", "dache_gb": ""}]
        data = self.read()
        self.assertTrue(data["snapshot_legacy"])
        self.assertIsNone(data["rows"][0]["ipkum_amt"])
        self.assertEqual(data["rows"][0]["misu_amt"], -1234)
        self.assertEqual(data["rows"][0]["manage_amt"], 300000)  # 옛 복사 오류도 최신 계약으로 보정 금지
        self.assertEqual(data["summary"]["pay_amt"], -206667)
        self.assertEqual(data["totals"]["ipkum_amt"], 400000)
        self.assertEqual(data["totals"]["manage_amt"], 70000)  # 상세와 달라도 저장 합계 보존
        self.assertEqual(data["summary"]["cost_sum"], 60000)
        self.assertIsNone(data["manager_account"])
        self.assertIsNone(data["summary"]["paid_suri"])
        self.query_one.assert_not_called()
        self.assertEqual(self.query.call_count, 1)
        self.assertIn("FROM jungsan_det", self.query.call_args.args[0])

    def test_snapshot_encoder_rejects_saved_or_incomplete_data(self):
        for data in ({"source": "saved"}, {"source": "live"}):
            with self.subTest(data=data), self.assertRaises((ValueError, jungsan._JungsanSaveError)):
                jungsan._jungsan_encode_snapshot("1139", "0004", date(2026, 5, 31), data)

    def test_screen_and_print_render_same_snapshot_and_edit_controls_only_in_live(self):
        # 앱 import 시의 기존 CREATE TABLE도 실제 DB에는 실행하지 않는다.
        with patch("db.execute", return_value=0):
            import app as app_routes  # 등록된 실제 필터/메뉴/권한 사용
        del app_routes
        for grade in ("A", "C"):
            for source in ("saved", "live"):
                data = self.read() if source == "saved" else self.data
                data["has_saved"] = True
                with app.test_request_context("/jungsan?src=" + source):
                    session.update(sabun="10001", grade=grade)
                    screen = jungsan.render_template(
                        "jungsan.html", data=data, ran=True, building_label="저장 당시 건물",
                        filters={"bunji1": "1139", "bunji2": "0004", "as_of": "2026-05-31", "src": source},
                    )
                    printed = jungsan.render_template("jungsan_print.html", data=data, filters={})
                self.assertIn("130,700", screen)
                self.assertIn("130,700", printed)
                self.assertIn("저장 당시 지시", screen)
                self.assertIn("저장 당시 지시", printed)
                self.assertIn('data-src="' + source + '"', screen)
                self.assertIn('q.set("src", btn.dataset.src)', screen)
                self.assertEqual('id="js-dache-form"' in screen, source == "live" and grade == "A")
                self.assertEqual('id="js-save-form"' in screen, grade == "A")

    @patch("jungsan._jungsan_write_snapshot")
    def test_save_route_explicitly_recalculates_and_redirects_to_saved(self, writer):
        with app.test_request_context("/jungsan/save", method="POST", data={
            "bunji1": "1139", "bunji2": "4", "as_of": "2026-05-31",
        }), patch("jungsan._jungsan_build_preview", return_value=self.data) as preview:
            session.update(sabun="10001", grade="A")
            response = jungsan.jungsan_save()
            self.assertEqual(response.status_code, 302)
            self.assertIn("src=saved", response.location)
            self.assertIn("saved=1", response.location)
            self.assertFalse(preview.call_args.kwargs["prefer_saved"])
            writer.assert_called_once_with("1139", "0004", date(2026, 5, 31), self.data)

    def test_list_preserves_saved_total_without_current_account_recalculation(self):
        self.query.side_effect = None
        self.query.return_value = None
        building = dict(self.data["building"])
        saved = {"bunji1": "1139", "bunji2": "0004", "jungsan_dt": date(2026, 5, 31),
                 "jungsan_seq": "01", "pay_amt": -206667, "man_cost": 12345}
        for account in ("M", "O"):
            building["sukum_acct_gb"] = account
            self.query.side_effect = [[building], [saved]]
            with app.test_request_context("/jungsan/list?q=1&year=2026&month=5"), \
                 patch("jungsan._ensure_g_cost_cols"), \
                 patch("jungsan._ensure_month_common_repairs_many") as repairs, \
                 patch("jungsan.render_template", side_effect=lambda template, **ctx: ctx):
                session.update(sabun="10001", grade="A")
                context = jungsan.jungsan_list()
                self.assertEqual(context["sum_pay"], -206667)
                self.assertEqual(context["results"][0]["pay_amt"], -206667)
                self.assertEqual(repairs.call_args.args[0], [])
        # 새 LONGTEXT를 목록 전체에 로드하지 않는다.
        self.assertNotIn("j.*", self.query.call_args.args[0])


if __name__ == "__main__":
    unittest.main()
