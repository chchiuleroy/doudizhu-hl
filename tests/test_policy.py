import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import policy as P
from runner import evaluate, play_one


def fake_state(hand, actions, trace, me, landlord, left):
    return {'raw_obs': {'current_hand': hand, 'actions': actions, 'trace': trace,
                        'self': me, 'landlord': landlord, 'num_cards_left': left}}


class RuleTests(unittest.TestCase):
    def test_all_seats_always_legal(self):
        # play_one 內有 assert action in raw_legal_actions
        for s in range(300):
            play_one(s, [P.policy] * 3)

    def test_goes_out_when_possible(self):
        st = fake_state('QQ', ['Q', 'QQ', 'pass'], [(0, 'JJ')], 1, 0, [10, 2, 10])
        self.assertEqual(P.policy(st), 'QQ')

    def test_peasant_passes_on_teammate_play(self):
        st = fake_state('3459TKA', ['K', 'A', 'pass'], [(2, '8')], 1, 0, [10, 7, 10])
        self.assertEqual(P.policy(st), 'pass')

    def test_avoid_single_lead_when_enemy_has_one_card(self):
        st = fake_state('33489', ['3', '33', '4', '8', '9'], [], 0, 0, [5, 10, 1])
        self.assertEqual(P.policy(st), '33')

    def test_beats_with_minimal_card_from_enemy(self):
        st = fake_state('3K2', ['K', '2', 'pass'], [(0, 'T')], 1, 0, [10, 3, 10][::1])
        # 農民 1 面對地主 0 的 T：用最小能壓的 K，不浪費 2
        self.assertEqual(P.policy(st), 'K')


class EndgameSafeTests(unittest.TestCase):
    def test_beat_prob_exact_values(self):
        # 單張：k=1 時 = 牌池中更大牌數 / 牌池大小
        self.assertAlmostEqual(P._beat_prob('4', '45K', 1), 2 / 3)
        self.assertEqual(P._beat_prob('K', '45K', 1), 0.0)
        # 單張：k=2，1 - C(不更大, 2)/C(n, 2)
        self.assertAlmostEqual(P._beat_prob('4', '345K', 2), 1 - 1 / 6)   # 更大 5,K；不更大 3,4 → C(2,2)=1
        # 對子：只有牌池中更大的同點對子才壓得過（k=2）：44、55 兩種 / C(4,2)=6
        self.assertAlmostEqual(P._beat_prob('33', '4455', 2), 2 / 6)
        self.assertEqual(P._beat_prob('33', '4455', 1), 0.0)               # 剩 1 張湊不出對子
        # 多張牌型：剩 ≤2 張的對手壓不過（除非王炸）
        self.assertEqual(P._beat_prob('34567', '89TJQ', 2), 0.0)
        self.assertAlmostEqual(P._beat_prob('44', 'BR33', 2), 1 / 6)       # 對手恰好持有雙王 = 王炸
        self.assertEqual(P._beat_prob('4444', '55BR', 2), 0.0)             # 炸彈只輸給王炸之外的更大炸彈，剩 2 張不可能

    def _endgame_state(self):
        # 真局第 30 手情境：地主剩 2 張，牌池 = 地主 2 張 + 小o 7 張
        raw = {'current_hand': '34558TJJQA2B',
               'actions': ['3', '4', '5', '8', 'T', 'J', 'Q', 'A', '2', 'B', '55', 'JJ'],
               'trace': [(0, 'pass'), (1, 'pass')], 'self': 2, 'landlord': 0,
               'num_cards_left': [2, 7, 12], 'others_hand': '4567TJQ22'}
        return {'raw_obs': raw}

    def test_flag_changes_endgame_lead_and_default_is_off(self):
        st = self._endgame_state()
        self.assertEqual(P.policy(st), '3')                           # 預設關閉 = v2：領出最小單張 3
        a = P.policy(st, {'endgame_safe': True})
        self.assertNotEqual(a, '3')
        self.assertLess(P._beat_prob(a, st['raw_obs']['others_hand'], 2), 0.05)

    def test_no_effect_when_opponent_has_many_cards(self):
        st = self._endgame_state()
        st['raw_obs']['num_cards_left'] = [9, 7, 12]
        self.assertEqual(P.policy(st, {'endgame_safe': True}), P.policy(st))


class RegressionFloor(unittest.TestCase):
    """固定種子 dev 集；門檻設在確認有效的水準之下，用來擋退步而非精確重現。"""

    def test_beats_rule_baseline_on_dev_seeds(self):
        r = evaluate(P.policy, range(0, 600))
        self.assertGreater(r['landlord']['paired_diff'], 0.10, r['landlord'])
        self.assertGreater(r['peasant']['paired_diff'], 0.0, r['peasant'])


if __name__ == '__main__':
    unittest.main()
