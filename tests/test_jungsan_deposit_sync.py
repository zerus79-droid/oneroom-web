"""최초보증금 동기화는 명시적 월정산 저장에서만. 관리실 기준액은 보존한다."""
import os
import re
import unittest
from datetime import date
from unittest.mock import MagicMock, patch

from flask import session
from app_instance import app
import jungsan
from tests.test_jungsan_saved_view import snapshot_data


class DepositSyncTests(unittest.TestCase):
    def setUp(self):
        ctx = app.test_request_context('/jungsan/save', method='POST')
        ctx.push()
        self.addCleanup(ctx.pop)
        session.update(sabun='10001', grade='A')
        self.addCleanup(patch.stopall)
        patch('jungsan.db.execute', side_effect=AssertionError('separate write')).start()
        patch('jungsan.db.query', side_effect=AssertionError('unexpected read')).start()
        patch('jungsan.db.query_one', side_effect=AssertionError('unexpected read')).start()
        self.data = snapshot_data()
        self.data['building']['first_amt'] = 4000000
        self.cur = MagicMock()
        self.current = dict(self.data['building'])
        self.cur.fetchone.side_effect = [self.current, {'latest_dt': date(2026, 4, 30)}]

    def sync(self, as_of=date(2026, 5, 31)):
        return jungsan._jungsan_sync_first_amt(self.cur, '1139', '0004', as_of, self.data)

    def updates(self):
        return [c for c in self.cur.execute.call_args_list
                if c.args[0].lstrip().startswith('UPDATE bd01')]

    def test_owner_updates_master_and_records_before_after(self):
        self.sync()
        self.assertEqual(self.updates()[0].args[1], (5000000, '10001', '1139', '0004'))
        info = self.data['summary']['first_amt_sync']
        self.assertEqual((info['status'], info['before'], info['after']),
                         ('updated', 4000000, 5000000))
        self.assertIn('FOR UPDATE', self.cur.execute.call_args_list[0].args[0])
        # 저장 당시 원래 건물 기준값도 snapshot에 남긴다.
        self.assertEqual(self.data['building']['first_amt'], 4000000)

    def test_same_amount_is_noop(self):
        self.current['first_amt'] = self.data['building']['first_amt'] = 5000000
        self.sync()
        self.assertFalse(self.updates())
        self.assertEqual(self.data['summary']['first_amt_sync']['status'], 'unchanged')

    def test_zero_after_all_tenants_exit_is_saved_not_ignored(self):
        self.data['rows'][0]['is_exit'] = True
        self.data['summary'].update(bojung_tot=0, first_amt=0)
        self.sync()
        self.assertEqual(self.updates()[0].args[1][0], 0)

    def test_older_month_cannot_revert_master(self):
        self.cur.fetchone.side_effect = [self.current, {'latest_dt': date(2026, 6, 30)}]
        self.sync()
        self.assertFalse(self.updates())
        self.assertEqual(self.data['summary']['first_amt_sync']['status'], 'historical')

    def test_same_month_resave_is_allowed(self):
        self.cur.fetchone.side_effect = [self.current, {'latest_dt': date(2026, 5, 31)}]
        self.sync()
        self.assertEqual(len(self.updates()), 1)

    def test_first_report_without_previous_header_can_sync(self):
        self.cur.fetchone.side_effect = [self.current, {'latest_dt': None}]
        self.sync()
        self.assertEqual(len(self.updates()), 1)

    def test_future_month_does_not_change_current_master(self):
        self.sync(date(2099, 5, 31))
        self.assertFalse(self.updates())
        self.assertEqual(self.data['summary']['first_amt_sync']['status'], 'future')

    def test_future_reports_do_not_block_sync_of_latest_actual_month(self):
        self.sync()
        latest_query = next(c for c in self.cur.execute.call_args_list if 'MAX(jungsan_dt)' in c.args[0])
        self.assertIn('jungsan_dt < %s', latest_query.args[0])
        self.assertGreater(latest_query.args[1][2], date.today())
        self.assertEqual(latest_query.args[1][2].day, 1)

    def test_deposit_account_not_rent_account_decides_sync(self):
        for rent_account, deposit_account in (('O', 'M'), ('M', 'O')):
            with self.subTest(rent=rent_account, deposit=deposit_account):
                self.cur.reset_mock()
                self.data['building'].update(sukum_rent_acct_gb=rent_account,
                                             sukum_bojung_acct_gb=deposit_account)
                self.current = dict(self.data['building'])
                self.cur.fetchone.side_effect = [self.current, {'latest_dt': None}]
                self.sync()
                self.assertEqual(bool(self.updates()), deposit_account == 'O')

    def test_changed_master_or_account_rejects_stale_calculation(self):
        for change in ({'first_amt': 1234567}, {'sukum_bojung_acct_gb': 'M'}):
            with self.subTest(change=change):
                self.cur.reset_mock()
                self.cur.fetchone.side_effect = [dict(self.current, **change)]
                with self.assertRaises(jungsan._JungsanSaveError):
                    self.sync()
                self.assertFalse(self.updates())

    def test_missing_building_or_unknown_deposit_account_rejects_save(self):
        for current in (None, dict(self.current, sukum_acct_gb='')):
            with self.subTest(current=current):
                self.cur.fetchone.side_effect = [current]
                with self.assertRaises(jungsan._JungsanSaveError):
                    self.sync()
                self.assertFalse(self.updates())

    def test_owner_total_must_match_rows_and_report_first_amount(self):
        for field in ('bojung_tot', 'first_amt'):
            with self.subTest(field=field):
                original = self.data['summary'][field]
                self.data['summary'][field] = 9999
                self.cur.fetchone.side_effect = [self.current, {'latest_dt': None}]
                with self.assertRaises(jungsan._JungsanSaveError):
                    self.sync()
                self.assertFalse(self.updates())
                self.data['summary'][field] = original

    def test_saved_view_cannot_sync_master(self):
        self.data['source'] = 'saved'
        with self.assertRaises(jungsan._JungsanSaveError):
            self.sync()
        self.assertFalse(self.updates())


class DepositDisplayTests(unittest.TestCase):
    def test_manager_difference_positive_negative_and_zero_survives_render_and_snapshot(self):
        with patch('db.execute', return_value=0):
            import app as app_routes
        del app_routes
        for first, expected in ((4000000, 1000000), (6000000, -1000000), (5000000, 0)):
            with self.subTest(first=first), app.test_request_context('/jungsan'):
                session.update(sabun='10001', grade='A')
                with patch('jungsan.db.execute', side_effect=AssertionError('preview write')), \
                     patch('jungsan.db.query', side_effect=AssertionError('unexpected read')), \
                     patch('jungsan.db.query_one', side_effect=AssertionError('unexpected read')):
                    data = jungsan._jungsan_build_preview(
                        '1139', '0004', date(2026, 5, 31), list_mode=True, prefer_saved=False,
                        preload={
                            'building': {'bunji1': '1139', 'bunji2': '0004', 'first_amt': first,
                                         'sukum_acct_gb': 'M', 'mgmt_gb': 'R'},
                            'skip_ensure_cols': True, 'skip_saved': True, 'skip_adjustments': True,
                            'rooms': [{'hosu': '101', 'ipju_nm': '테스트', 'ipju_seq': '01',
                                       'ipju_dt': date(2025, 1, 1), 'bojung_amt': 5000000}],
                            'pay_map': {}, 'sil_paid_map': {}, 'terms_map': {},
                            'owner_suri': 0, 'jungke_cost': 0,
                        },
                    )
                    self.assertEqual(data['summary']['first_amt'], first)
                    self.assertEqual(data['summary']['bojung_dache'], expected)
                    payload = jungsan._jungsan_encode_snapshot('1139', '0004', date(2026, 5, 31), data)
                    frozen = jungsan._jungsan_decode_snapshot('1139', '0004', date(2026, 5, 31), payload)
                    self.assertEqual(frozen['summary']['bojung_dache'], expected)
                    for template in ('jungsan.html', 'jungsan_print.html'):
                        html = jungsan.render_template(template, data=data, ran=True, building_label='',
                            filters={'bunji1': '1139', 'bunji2': '0004', 'as_of': '2026-05-31', 'src': 'live'})
                        self.assertTrue('보증금대체' in html, template + ': 보증금대체 표시 누락')
                        self.assertTrue(re.search(r'보증금대체</span>\s*<span[^>]*>'
                            + re.escape(format(expected, ',')) + r'</span>', html),
                            template + ': 보증금대체 부호/금액 누락')


@unittest.skipUnless(os.environ.get('JUNGSAN_TXN_DB_TEST') == '1', 'opt-in temporary-table DB test')
class DepositDatabaseTests(unittest.TestCase):
    def test_master_and_report_commit_and_rollback_together(self):
        """실제 MariaDB 임시 테이블에서 연동/실패 복구. 업무 테이블은 읽기만 한다."""
        conn = jungsan.db.get_conn()
        names = {'bd01': 'codex_test_deposit_bd01', 'jungsan_m': 'codex_test_deposit_m',
                 'jungsan_det': 'codex_test_deposit_det'}

        class TempCursor:
            def __init__(self, raw, fail_detail=False):
                self.raw, self.fail_detail = raw, fail_detail

            def execute(self, sql, args=None):
                if self.fail_detail and sql.lstrip().startswith('INSERT INTO jungsan_det'):
                    raise RuntimeError('detail failure')
                for original, temporary in names.items():
                    sql = re.sub(r'\b' + original + r'\b', temporary, sql)
                return self.raw.execute(sql, args)

            def fetchone(self):
                return self.raw.fetchone()

        try:
            with conn.cursor() as raw, app.test_request_context('/jungsan/save'):
                session.update(sabun='10001', grade='A')
                raw.execute('''CREATE TEMPORARY TABLE codex_test_deposit_bd01 (
                    bunji1 CHAR(4), bunji2 CHAR(4), first_amt DECIMAL(15,0),
                    sukum_acct_gb CHAR(1), sukum_bojung_acct_gb CHAR(1), uid VARCHAR(5), sys_dt DATETIME,
                    PRIMARY KEY(bunji1,bunji2)) ENGINE=InnoDB''')
                for original in ('jungsan_m', 'jungsan_det'):
                    raw.execute(f'CREATE TEMPORARY TABLE {names[original]} LIKE {original}')
                raw.execute("INSERT INTO codex_test_deposit_bd01 VALUES ('1139','0004',4000000,'O','O','10001',NOW())")
                data = snapshot_data()
                data['building'].update(first_amt=4000000, sukum_bojung_acct_gb='O')
                conn.begin()
                jungsan._jungsan_sync_first_amt(TempCursor(raw), '1139', '0004', date(2026, 5, 31), data)
                jungsan._jungsan_write_snapshot_rows(TempCursor(raw), '1139', '0004', date(2026, 5, 31), data)
                conn.commit()
                raw.execute('SELECT * FROM codex_test_deposit_bd01')
                master = raw.fetchone()
                self.assertEqual(master['first_amt'], 5000000)
                raw.execute('SELECT * FROM codex_test_deposit_m')
                header = raw.fetchone()
                raw.execute('SELECT * FROM codex_test_deposit_det ORDER BY hosu')
                details = raw.fetchall()
                frozen = jungsan._jungsan_read_saved('1139', '0004', date(2026, 5, 31), header)
                self.assertEqual(frozen['summary']['first_amt_sync']['before'], 4000000)
                # 새 합계 600만원 갱신 후 상세 저장 실패: 건물·헤더·상세 전부 이전 값 복원.
                data['building']['first_amt'] = 5000000
                data['rows'][0]['bojung_amt'] = 6000000
                data['summary'].update(first_amt=6000000, bojung_tot=6000000)
                conn.begin()
                jungsan._jungsan_sync_first_amt(TempCursor(raw), '1139', '0004', date(2026, 5, 31), data)
                with self.assertRaisesRegex(RuntimeError, 'detail failure'):
                    jungsan._jungsan_write_snapshot_rows(TempCursor(raw, True), '1139', '0004', date(2026, 5, 31), data)
                conn.rollback()
                for table, old in (('codex_test_deposit_bd01', [master]),
                                   ('codex_test_deposit_m', [header]), ('codex_test_deposit_det', details)):
                    raw.execute('SELECT * FROM ' + table + (' ORDER BY hosu' if table.endswith('_det') else ''))
                    self.assertEqual(raw.fetchall(), old)
                # 4월 재저장은 허용하지만 이미 5월에 맞춘 건물 최초보증금은 유지.
                data['as_of'], data['month_start'], data['month_end'] = '2026-04-30', '2026-04-01', '2026-04-30'
                conn.begin()
                jungsan._jungsan_sync_first_amt(TempCursor(raw), '1139', '0004', date(2026, 4, 30), data)
                self.assertEqual(data['summary']['first_amt_sync']['status'], 'historical')
                jungsan._jungsan_write_snapshot_rows(TempCursor(raw), '1139', '0004', date(2026, 4, 30), data)
                conn.commit()
                raw.execute('SELECT first_amt FROM codex_test_deposit_bd01')
                self.assertEqual(raw.fetchone()['first_amt'], 5000000)
                # 보증금 관리실 보관이면 합계가 달라도 기준액을 덮지 않고 차액을 저장한다.
                raw.execute("UPDATE codex_test_deposit_bd01 SET sukum_bojung_acct_gb='M'")
                data['building']['sukum_bojung_acct_gb'] = 'M'
                data['summary'].update(first_amt=5000000, bojung_dache=1000000)
                data['as_of'], data['month_start'], data['month_end'] = '2026-05-31', '2026-05-01', '2026-05-31'
                conn.begin()
                jungsan._jungsan_sync_first_amt(TempCursor(raw), '1139', '0004', date(2026, 5, 31), data)
                jungsan._jungsan_write_snapshot_rows(TempCursor(raw), '1139', '0004', date(2026, 5, 31), data)
                conn.commit()
                raw.execute('SELECT first_amt FROM codex_test_deposit_bd01')
                self.assertEqual(raw.fetchone()['first_amt'], 5000000)
                raw.execute("SELECT * FROM codex_test_deposit_m WHERE jungsan_dt='2026-05-31'")
                frozen = jungsan._jungsan_read_saved('1139', '0004', date(2026, 5, 31), raw.fetchone())
                self.assertEqual(frozen['summary']['bojung_dache'], 1000000)
                self.assertEqual(frozen['summary']['first_amt_sync']['status'], 'manager')
        finally:
            try:
                conn.rollback()
                with conn.cursor() as raw:
                    for temporary in names.values():
                        raw.execute('DROP TEMPORARY TABLE IF EXISTS ' + temporary)
            finally:
                conn.close()


if __name__ == '__main__':
    unittest.main()
