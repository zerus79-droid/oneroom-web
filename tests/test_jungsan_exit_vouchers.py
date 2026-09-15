"""종류06의 존재 여부는 금액 부호/합계와 무관하다. 모든 DB 접근은 모의 처리."""
import unittest
from datetime import date, datetime
from unittest.mock import patch

from flask import session
from app_instance import app
import jungsan
import jungsan_engine as engine


class ExitVoucherTests(unittest.TestCase):
    def setUp(self):
        ctx = app.test_request_context('/jungsan')
        ctx.push()
        self.addCleanup(ctx.pop)
        session.update(sabun='10001', grade='U')
        self.addCleanup(patch.stopall)
        self.query = patch('jungsan.db.query', side_effect=AssertionError('unexpected DB read')).start()
        self.query_one = patch('jungsan.db.query_one', side_effect=AssertionError('unexpected DB read')).start()
        patch('jungsan.db.execute', side_effect=AssertionError('unexpected DB write')).start()
        self.fallback = patch('jungsan._jungsan_out_settle_amt', return_value=999999).start()
        self.owner_day = patch('jungsan._calc_checkout_day_amt', return_value=101400).start()
        self.tenant = {
            'hosu': '203', 'ipju_seq': '17', 'ipju_nm': '테스트',
            'ipju_dt': date(2025, 1, 1), 'out_dt': date(2026, 5, 7),
            'rent_amt': 310000, 'manage_amt': 70000, 'napbu_gb': 'A',
        }
        self.preload = {
            'building': {'bunji1': '0508', 'bunji2': '0088', 'mgmt_gb': 'R',
                         'sukum_acct_gb': 'M', 'sukum_bojung_acct_gb': 'O', 'man_cost': 0},
            'skip_ensure_cols': True, 'skip_saved': True, 'skip_adjustments': True,
            'rooms': [self.tenant], 'pay_map': {}, 'sil_paid_map': {},
            'terms_map': {}, 'owner_suri': 0, 'jungke_cost': 0,
        }
        self.key = ('203', '17')

    def preview(self, parts, *, saved=False):
        self.preload['pay_map'] = {self.key: parts}
        if saved:
            self.preload['skip_saved'] = False
            header = {'jungsan_dt': datetime(2026, 5, 31), 'jungsan_seq': '01', 'pay_amt': 100000}
            patch('jungsan._jungsan_saved_header', return_value=header).start()
            patch('jungsan._lifetime_sil01_map', return_value={}).start()
            patch('jungsan._month_sukum_sil_dache', return_value=(0, 0)).start()
            patch('jungsan._exit_settlement_misu', return_value=888888).start()

            def query(sql, args=None):
                if 'FROM jungsan_det' in sql:
                    return [dict(self.tenant)]
                if 'FROM sukum01' in sql and 'GROUP BY sukum_char' in sql:
                    return [dict(value, sukum_char=kind) for kind, value in parts.items()]
                raise AssertionError('unexpected saved-view query')

            def query_one(sql, args=None):
                if 'FROM bd03_det' in sql:
                    return dict(self.tenant)
                if 'COUNT(*) AS cnt' in sql and 'FROM sukum01' in sql:
                    return {'cnt': int('06' in parts), 'amt': parts.get('06', {}).get('sil', 0),
                            'manage_desc': ''}
                raise AssertionError('unexpected saved-view query_one')

            self.query.side_effect = query
            self.query_one.side_effect = query_one
        return jungsan._jungsan_build_preview(
            '0508', '0088', date(2026, 5, 31), list_mode=True,
            preload=self.preload, prefer_saved=saved,
        )

    def test_presence_matches_direct_query_for_negative_zero_positive_and_missing(self):
        for amount in (None, -206667, 0, 206667):
            with self.subTest(amount=amount):
                parts = {} if amount is None else {'06': {'sil': amount, 'dache': 0}}
                actual = engine._month_out_adjustment(
                    '0508', '0088', '203', '17', date(2026, 5, 1), '2026-05-31',
                    pay_map={self.key: parts},
                )
                self.query_one.side_effect = None
                self.query_one.return_value = {'cnt': int(amount is not None), 'amt': amount or 0,
                                              'manage_desc': ''}
                direct = engine._month_out_adjustment(
                    '0508', '0088', '203', '17', date(2026, 5, 1), '2026-05-31',
                )
                self.assertEqual(actual, direct)

    def test_bulk_loaders_keep_zero_voucher_key(self):
        self.query.side_effect = None
        self.query.return_value = [{'bunji1': '0508', 'bunji2': '0088', 'hosu': '203',
                                    'ipju_seq': '17', 'sukum_char': '06', 'sil': 0, 'dache': 0}]
        single = engine._month_sukum_breakdown_map('0508', '0088', date(2026, 5, 1), '2026-05-31')
        multiple = engine._month_sukum_breakdown_map_all(date(2026, 5, 1), '2026-05-31')
        self.assertEqual(single, multiple[('0508', '0088')])
        self.assertIn('06', single[self.key])
        self.assertEqual(single[self.key]['06']['sil'], 0)

    def test_live_manager_voucher_amount_is_preserved_without_fallback(self):
        for sil, dache in ((206667, 0), (-206667, 0), (0, 0), (-50000, 50000), (0, 30000)):
            with self.subTest(sil=sil, dache=dache):
                self.fallback.reset_mock()
                data = self.preview({'06': {'sil': sil, 'dache': dache}})
                self.assertEqual(data['rows'][0]['ipkum_amt'], sil + dache)
                self.assertEqual(data['summary']['ipkum_tot'], sil + dache)
                self.fallback.assert_not_called()
                self.owner_day.assert_not_called()

    def test_live_manager_missing_voucher_keeps_existing_fallback(self):
        data = self.preview({'01': {'sil': 0, 'dache': 0}})
        self.assertEqual(data['rows'][0]['ipkum_amt'], 999999)
        self.fallback.assert_called_once()

    def test_saved_manager_keeps_zero_net_voucher_not_raw_sil_or_balance(self):
        for sil, dache in ((0, 0), (-50000, 50000), (-206667, 0), (206667, 0)):
            with self.subTest(sil=sil, dache=dache):
                data = self.preview({'06': {'sil': sil, 'dache': dache}}, saved=True)
                self.assertEqual(data['source'], 'saved')
                self.assertEqual(data['rows'][0]['ipkum_amt'], sil + dache)
                self.assertEqual(data['summary']['ipkum_tot'], sil + dache)

    def test_owner_account_still_uses_existing_tenant_day_charge(self):
        self.preload['building']['sukum_acct_gb'] = 'O'
        data = self.preview({'06': {'sil': -206667, 'dache': 0}})
        self.assertEqual(data['rows'][0]['ipkum_amt'], 101400)
        self.owner_day.assert_called_once()
        self.fallback.assert_not_called()


if __name__ == '__main__':
    unittest.main()
