"""OUT-01: fictional candidates and offline sends in temporary state."""
from datetime import datetime
from unittest.mock import patch

from test_phase_a import Base
import hunt_config as HC
import hunt_state as HS
import outbound as O


class WindowQuotaWordingTests(Base):
    def setUp(self):
        super().setUp()
        self.cfg['sending'].update(daily_cap=18, grade_quota={'A': 13, 'B': 5},
                                   hidden_b_probe=0)
        clock = patch.object(HC, 'now_local', return_value=datetime(
            2026, 11, 2, 8, tzinfo=HC.cfg_tz(self.cfg)))
        clock.start()
        self.addCleanup(clock.stop)

    def send_window(self, include_b=True):
        jobs = [self.job('a' + str(i)) for i in range(3)]
        if include_b:
            jobs.append(self.job('b1', '示例品牌行业'))
        self.raw(jobs)
        st = HS.load_state(self.cfg)
        out = []

        def inspect(br, tab, job, cfg):
            return 'ready', ('门店运营' if job['encryptJobId'] == 'b1'
                             else '商户拓展开城')

        with patch.object(O, 'inspect_job', side_effect=inspect), \
             patch.object(O, 'send_one', return_value='sent'), \
             patch.object(O.time, 'sleep'):
            result = O.do_send(self.cfg, None, None, st, 8, out)
        return result, st, out

    def test_exhausted_a_window_quota_keeps_candidate_for_next_window(self):
        result, st, _ = self.send_window()
        self.assertEqual(result['quiet'].get('本跳A配额已满，A候选留到下跳'), 1)
        self.assertNotIn('A额度已满，跳过A候选', result['quiet'])
        self.assertNotIn('a2', st['sent'])
        self.assertNotIn('a2', st['checked_jobs'])

    def test_window_usage_is_reported_before_total(self):
        _, _, out = self.send_window()
        quota_line = '本跳配额 A 2/2 · B 1/1'
        self.assertIn(quota_line, out)
        self.assertLess(out.index(quota_line), out.index('本轮发出 3，今日累计 3/18'))

    def test_quotas_and_sends_are_unchanged(self):
        result, st, _ = self.send_window()
        self.assertEqual(result['quotas'], {'A': 2, 'B': 1})
        self.assertEqual(result['by_grade'], {'A': 2, 'B': 1})
        self.assertEqual(result['sent'], 3)
        self.assertEqual(result['day_count'], 3)
        self.assertEqual(result['inspects'], 3)
        self.assertEqual(result['stop_reason'], 'quota_exhausted')
        self.assertEqual([item['job_id'] for item in result['items']], ['a0', 'a1', 'b1'])
        self.assertEqual(st['day_by_grade'], {'A': 2, 'B': 1})
        self.assertEqual(set(st['sent']), {'a0', 'a1', 'b1'})

    def test_report_preserves_quota_order_and_defaults_unused_grade_to_zero(self):
        self.cfg['sending']['grade_quota'] = {'B': 5, 'A': 13}
        _, _, out = self.send_window(include_b=False)
        self.assertIn('本跳配额 B 0/1 · A 2/2', out)
