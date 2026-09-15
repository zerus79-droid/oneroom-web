"""월정산 저장 중 실패·동시 저장·비트랜잭션 테이블에 대한 회귀 테스트."""
import os
import re
import unittest
from datetime import date, datetime
from unittest.mock import MagicMock, patch

from flask import session

from app_instance import app
import jungsan
from tests.test_jungsan_saved_view import snapshot_data


class SnapshotSaveTests(unittest.TestCase):
    def setUp(self):
        self.ctx = app.test_request_context("/jungsan/save", method="POST")
        self.ctx.push()
        self.addCleanup(self.ctx.pop)
        session["sabun"] = "10001"
        self.conn = MagicMock()
        self.cur = self.conn.cursor.return_value.__enter__.return_value
        self.cur.fetchall.return_value = [
            {"table_name": "jungsan_m", "engine": "InnoDB"},
            {"table_name": "jungsan_det", "engine": "InnoDB"},
        ]
        self.cur.fetchone.side_effect = [
            {"Field": "snapshot_json"},
            {"acquired": 1},
            {"jungsan_dt": datetime(2026, 5, 31), "jungsan_seq": "01"},
        ]
        self.addCleanup(patch.stopall)
        patch("jungsan.db.get_conn", return_value=self.conn).start()
        # 저장 처리가 별도 연결의 자동 커밋으로 돌아가면 즉시 실패시킨다.
        patch("jungsan.db.execute", side_effect=AssertionError("separate connection")).start()
        patch("jungsan.db.query_one", side_effect=AssertionError("separate connection")).start()
        self.data = snapshot_data()

    def save(self):
        jungsan._jungsan_write_snapshot("1139", "0004", date(2026, 5, 31), self.data)

    def statements(self):
        return [" ".join(c.args[0].split()) for c in self.cur.execute.call_args_list]

    def test_replace_commits_only_after_all_details_on_same_connection(self):
        def check_commit():
            self.assertEqual(sum(s.startswith("INSERT INTO jungsan_det") for s in self.statements()), 2)
        self.conn.commit.side_effect = check_commit
        self.save()
        self.conn.begin.assert_called_once()
        self.conn.commit.assert_called_once()
        self.conn.rollback.assert_not_called()
        sql = self.statements()
        self.assertTrue(any(s.startswith("UPDATE jungsan_m") for s in sql))
        self.assertTrue(any(s.startswith("DELETE FROM jungsan_det") for s in sql))
        self.assertIn("RELEASE_LOCK", sql[-1])
        self.conn.close.assert_called_once()

    def test_failed_second_detail_rolls_back_whole_replacement(self):
        inserts = 0
        def fail_second_insert(sql, args=None):
            nonlocal inserts
            if sql.lstrip().startswith("INSERT INTO jungsan_det"):
                inserts += 1
                if inserts == 2:
                    raise RuntimeError("simulated detail failure")
        self.cur.execute.side_effect = fail_second_insert
        with self.assertRaisesRegex(RuntimeError, "detail failure"):
            self.save()
        self.assertEqual(inserts, 2)
        self.conn.commit.assert_not_called()
        self.conn.rollback.assert_called_once()
        self.assertIn("RELEASE_LOCK", self.statements()[-1])
        self.conn.close.assert_called_once()

    def test_failed_new_snapshot_rolls_back_header_too(self):
        self.cur.fetchone.side_effect = [{"Field": "snapshot_json"}, {"acquired": 1}, None]
        def fail_insert(sql, args=None):
            if sql.lstrip().startswith("INSERT INTO jungsan_det"):
                raise RuntimeError("new snapshot failed")
        self.cur.execute.side_effect = fail_insert
        with self.assertRaisesRegex(RuntimeError, "new snapshot failed"):
            self.save()
        self.assertTrue(any(s.startswith("INSERT INTO jungsan_m") for s in self.statements()))
        self.conn.commit.assert_not_called()
        self.conn.rollback.assert_called_once()

    def test_myisam_or_missing_table_prevents_any_data_change(self):
        for engines in ([], [
            {"table_name": "jungsan_m", "engine": "MyISAM"},
            {"table_name": "jungsan_det", "engine": "MyISAM"},
        ], [
            {"table_name": "jungsan_m", "engine": "InnoDB"},
            {"table_name": "jungsan_det", "engine": "MyISAM"},
        ]):
            with self.subTest(engines=engines):
                self.cur.execute.reset_mock()
                self.cur.fetchall.return_value = engines
                with self.assertRaises(jungsan._JungsanSaveError):
                    self.save()
                self.conn.begin.assert_not_called()
                self.conn.commit.assert_not_called()
                self.assertEqual(len(self.statements()), 1)

    def test_busy_month_prevents_any_data_change(self):
        self.cur.fetchone.side_effect = [{"Field": "snapshot_json"}, {"acquired": 0}]
        with self.assertRaises(jungsan._JungsanSaveError):
            self.save()
        self.conn.begin.assert_not_called()
        self.conn.commit.assert_not_called()
        self.assertFalse(any(s.startswith(("UPDATE", "DELETE", "INSERT")) for s in self.statements()))
        self.assertFalse(any("RELEASE_LOCK" in s for s in self.statements()))
        self.conn.close.assert_called_once()

    def test_failed_commit_attempts_rollback_and_releases_lock(self):
        self.conn.commit.side_effect = RuntimeError("commit failed")
        with self.assertRaisesRegex(RuntimeError, "commit failed"):
            self.save()
        self.conn.rollback.assert_called_once()
        self.assertIn("RELEASE_LOCK", self.statements()[-1])
        self.conn.close.assert_called_once()

    def test_missing_snapshot_column_prevents_any_data_change(self):
        self.cur.fetchone.side_effect = [None]
        with self.assertRaisesRegex(jungsan._JungsanSaveError, "005_jungsan_snapshot_json.sql"):
            self.save()
        self.conn.begin.assert_not_called()
        self.assertFalse(any(s.startswith(("UPDATE", "INSERT", "DELETE")) for s in self.statements()))

    def test_json_is_part_of_same_header_write_and_preserves_full_management_note(self):
        self.data["summary"]["jisi_text"] = "가" * 200
        self.save()
        update = next(c for c in self.cur.execute.call_args_list if c.args[0].lstrip().startswith("UPDATE jungsan_m"))
        self.assertIn("snapshot_json=%s", update.args[0])
        self.assertIn("가" * 50, update.args[1])  # 옛 헤더 컬럼은 varchar(50)
        payload = next(v for v in update.args[1] if isinstance(v, str) and v.startswith('{"version":'))
        data = jungsan._jungsan_decode_snapshot("1139", "0004", date(2026, 5, 31), payload)
        self.assertEqual(data["summary"]["jisi_text"], "가" * 200)

    @patch("jungsan._jungsan_build_preview")
    @patch("jungsan._jungsan_write_snapshot")
    def test_route_does_not_report_saved_when_write_fails(self, writer, preview):
        preview.return_value = {"building": {"bunji1": "1139", "bunji2": "0004"}}
        writer.side_effect = jungsan._JungsanSaveError("저장 불가")
        with app.test_request_context("/jungsan/save", method="POST", data={
            "bunji1": "1139", "bunji2": "4", "as_of": "2026-05-31",
        }):
            session.update(sabun="10001", grade="A")
            response = jungsan.jungsan_save()
            self.assertEqual(response.status_code, 302)
            self.assertIn("src=live", response.location)
            self.assertNotIn("saved=1", response.location)
            self.assertIn(("error", "저장 불가"), session["_flashes"])


@unittest.skipUnless(os.environ.get("JUNGSAN_TXN_DB_TEST") == "1", "opt-in temporary-table DB test")
class SnapshotDatabaseTests(unittest.TestCase):
    def test_detail_failure_restores_header_and_details_in_innodb(self):
        """실제 스키마를 빈 임시 테이블에 복제해 복구를 검증. 업무 자료는 쓰지 않는다."""
        conn = jungsan.db.get_conn()
        tables = {
            "jungsan_m": "codex_test_jungsan_m_atomic",
            "jungsan_det": "codex_test_jungsan_det_atomic",
        }

        class TemporaryCursor:
            def __init__(self, raw, fail_second=False):
                self.raw = raw
                self.fail_second = fail_second
                self.inserts = 0

            def execute(self, sql, args=None):
                if sql.lstrip().startswith("INSERT INTO jungsan_det"):
                    self.inserts += 1
                    if self.fail_second and self.inserts == 2:
                        raise RuntimeError("simulated second detail failure")
                for original, temporary in tables.items():
                    sql = re.sub(r"\b" + original + r"\b", temporary, sql)
                return self.raw.execute(sql, args)

            def fetchone(self):
                return self.raw.fetchone()

        data = snapshot_data()
        try:
            with conn.cursor() as cur:
                for original, temporary in tables.items():
                    cur.execute(f"CREATE TEMPORARY TABLE {temporary} LIKE {original}")
                    cur.execute(f"ALTER TABLE {temporary} ENGINE=InnoDB")
                with app.test_request_context("/jungsan/save"):
                    session["sabun"] = "10001"
                    conn.begin()
                    jungsan._jungsan_write_snapshot_rows(
                        TemporaryCursor(cur), "1139", "0004", date(2026, 5, 31), data
                    )
                    conn.commit()
                    cur.execute("SELECT * FROM codex_test_jungsan_m_atomic")
                    old_header = cur.fetchall()
                    cur.execute("SELECT * FROM codex_test_jungsan_det_atomic ORDER BY hosu")
                    old_details = cur.fetchall()
                    self.assertEqual(len(old_header), 1)
                    self.assertEqual(len(old_details), 2)
                    stored = jungsan._jungsan_read_saved("1139", "0004", date(2026, 5, 31), old_header[0])
                    self.assertEqual(stored["summary"]["pay_amt"], data["summary"]["pay_amt"])
                    self.assertEqual(stored["rows"][0]["ipkum_amt"], data["rows"][0]["ipkum_amt"])
                    self.assertEqual(stored["suri_detail"], data["suri_detail"])
                    data["summary"]["pay_amt"] = 999999
                    conn.begin()
                    with self.assertRaisesRegex(RuntimeError, "second detail failure"):
                        jungsan._jungsan_write_snapshot_rows(
                            TemporaryCursor(cur, fail_second=True),
                            "1139", "0004", date(2026, 5, 31), data,
                        )
                    conn.rollback()
                    cur.execute("SELECT * FROM codex_test_jungsan_m_atomic")
                    self.assertEqual(cur.fetchall(), old_header)
                    cur.execute("SELECT * FROM codex_test_jungsan_det_atomic ORDER BY hosu")
                    self.assertEqual(cur.fetchall(), old_details)
                    # 정상 재저장 때만 같은 월의 저장본이 새 값으로 교체된다.
                    conn.begin()
                    jungsan._jungsan_write_snapshot_rows(
                        TemporaryCursor(cur), "1139", "0004", date(2026, 5, 31), data
                    )
                    conn.commit()
                    cur.execute("SELECT * FROM codex_test_jungsan_m_atomic")
                    replacement = cur.fetchall()
                    self.assertEqual(len(replacement), 1)
                    stored = jungsan._jungsan_read_saved("1139", "0004", date(2026, 5, 31), replacement[0])
                    self.assertEqual(stored["summary"]["pay_amt"], 999999)
        finally:
            try:
                conn.rollback()
                with conn.cursor() as cur:
                    for temporary in tables.values():
                        cur.execute(f"DROP TEMPORARY TABLE IF EXISTS {temporary}")
            finally:
                conn.close()


if __name__ == "__main__":
    unittest.main()
