"""Read-only regression checks for the FX Scanner's structure, EMA and gating logic.

Copy into the folder containing scanner.py and run:
    py -3.12 validate_fx_scanner.py

No MT5 import, broker connection, order sending, or writes to market-data files.
All candles in this test file are synthetic and NOT trading signals.
"""

import unittest
from unittest.mock import patch

import pandas as pd

import scanner


# Hand-constructed swings, alternating high/low:
# H 110, L 100, HH 113, HL 103, HH 116, HL 105.5.
# On the final bar, the second close above the last pullback candle high
# confirms the second HL and hence a valid bullish Phase 1 structure.
UP_CANDLES = [
    (106, 110, 105, 108),
    (108, 108.5, 102, 103),
    (103, 104, 100, 102),
    (102, 107, 101, 106),
    (106, 109, 105, 108),
    (108, 113, 107, 112),
    (112, 112, 105, 106),
    (106, 108, 103, 104),
    (104, 110, 104, 109),
    (109, 111, 108, 110),
    (110, 116, 109, 115),
    (115, 112, 108, 109),
    (109, 110, 106, 107),
    (107, 112, 107, 111),
    (111, 114, 110, 113),
    (113, 114, 110, 111),
    (111, 112, 106, 107),
    (107, 109, 105.5, 106.5),
    (106.5, 112, 106, 111),
    (111, 114, 110, 113),
]


def frame(rows):
    df = pd.DataFrame(rows, columns=["open", "high", "low", "close"])
    df.insert(0, "timestamp", pd.date_range("2025-01-01", periods=len(df), freq="h", tz="UTC"))
    return df


def down_rows():
    # Reflect prices to test genuinely symmetric HH/HL -> LL/LH logic.
    return [(220 - o, 220 - l, 220 - h, 220 - c) for o, h, l, c in UP_CANDLES]


def ema_example(direction, touch=True):
    direction = 1 if direction == "up" else -1
    closes = [1.2 + direction * 0.001 * i for i in range(250)]
    df = frame([(p, p + 0.003, p - 0.003, p) for p in closes])
    e = df.close.ewm(span=20, adjust=False).mean()
    e_prev, e_now = float(e.iloc[-2]), float(e.iloc[-1])
    if direction == 1:
        df.loc[df.index[-1], "open"] = e_now + 0.002
        # Between previous and current EMA20: tests same-candle EMA alignment.
        df.loc[df.index[-1], "low"] = (e_now + e_prev) / 2 if touch else e_now + 0.0005
        df.loc[df.index[-1], "high"] = float(df.close.iloc[-1]) + 0.003
    else:
        df.loc[df.index[-1], "open"] = e_now - 0.002
        df.loc[df.index[-1], "high"] = (e_now + e_prev) / 2 if touch else e_now - 0.0005
        df.loc[df.index[-1], "low"] = float(df.close.iloc[-1]) - 0.003
    return df


class TestMarketStructure(unittest.TestCase):
    def test_uptrend_requires_second_higher_low_confirmation(self):
        incomplete = scanner.analyse_structure(frame(UP_CANDLES[:-1]))
        self.assertEqual(incomplete.trend, "none")
        complete = scanner.analyse_structure(frame(UP_CANDLES))
        self.assertEqual(complete.trend, "up")
        self.assertEqual(complete.phase, "phase_1")
        self.assertEqual(complete.protected_price, 105.5)
        self.assertEqual([s.label for s in complete.swings[-4:]], ["HH", "HL", "HH", "HL"])

    def test_uptrend_invalidated_by_single_wick_break(self):
        s = scanner.analyse_structure(frame(UP_CANDLES + [(112, 114, 105.49, 112)]))
        self.assertEqual(s.trend, "none")
        self.assertTrue(s.invalidated)
        self.assertIn("protected HL", s.invalidation_reason)

    def test_equal_wick_is_not_a_break(self):
        s = scanner.analyse_structure(frame(UP_CANDLES + [(112, 114, 105.5, 112)]))
        self.assertEqual(s.trend, "up")

    def test_downtrend_and_protected_lh(self):
        s = scanner.analyse_structure(frame(down_rows()))
        self.assertEqual(s.trend, "down")
        self.assertEqual(s.phase, "phase_1")
        self.assertEqual(s.protected_price, 114.5)
        self.assertEqual([x.label for x in s.swings[-4:]], ["LL", "LH", "LL", "LH"])

    def test_downtrend_invalidated_by_single_wick_break(self):
        s = scanner.analyse_structure(frame(down_rows() + [(108, 114.51, 106, 108)]))
        self.assertEqual(s.trend, "none")
        self.assertTrue(s.invalidated)
        self.assertIn("protected LH", s.invalidation_reason)


class TestEMA(unittest.TestCase):
    def test_bullish_stack_and_touch_of_current_ema(self):
        d = ema_example("up")
        last_ema = float(d.close.ewm(span=20, adjust=False).mean().iloc[-1])
        prev_ema = float(d.close.ewm(span=20, adjust=False).mean().iloc[-2])
        self.assertTrue(prev_ema < float(d.low.iloc[-1]) <= last_ema)
        result = scanner.ema_bounce(d, "up")
        self.assertTrue(result["stacked"])
        self.assertTrue(result["bounce"])

    def test_bearish_stack_and_touch_of_current_ema(self):
        d = ema_example("down")
        last_ema = float(d.close.ewm(span=20, adjust=False).mean().iloc[-1])
        prev_ema = float(d.close.ewm(span=20, adjust=False).mean().iloc[-2])
        self.assertTrue(last_ema <= float(d.high.iloc[-1]) < prev_ema)
        result = scanner.ema_bounce(d, "down")
        self.assertTrue(result["stacked"])
        self.assertTrue(result["bounce"])

    def test_no_bounce_without_ema_touch(self):
        for direction in ("up", "down"):
            with self.subTest(direction=direction):
                result = scanner.ema_bounce(ema_example(direction, touch=False), direction)
                self.assertTrue(result["stacked"])
                self.assertFalse(result["bounce"])

    def test_requires_at_least_200_candles(self):
        r = scanner.ema_bounce(ema_example("up").iloc[:199], "up")
        self.assertFalse(r["stacked"])
        self.assertFalse(r["bounce"])


class TestFullScannerGates(unittest.TestCase):
    def _run(self, daily, h4, h1, h1_candles=None):
        data = h1_candles if h1_candles is not None else ema_example("up")
        with patch("scanner.analyse_structure", side_effect=[daily, h4, h1]):
            return scanner.scan(data, data, data)

    def test_full_setup_with_all_conditions_true(self):
        up = scanner.MarketState(trend="up", phase="phase_1")
        result = self._run(up, up, up)
        self.assertEqual(result["status"], "SETUP")
        self.assertTrue(result["ema"]["stacked"] and result["ema"]["bounce"])

    def test_daily_phase_two_blocks_setup(self):
        up = scanner.MarketState(trend="up", phase="phase_1")
        phase_two = scanner.MarketState(trend="up", phase="phase_2")
        self.assertEqual(self._run(phase_two, up, up)["status"], "NO_TRADE")

    def test_lower_timeframe_mismatch_blocks_setup(self):
        up = scanner.MarketState(trend="up", phase="phase_1")
        none = scanner.MarketState(trend="none")
        self.assertEqual(self._run(up, none, up)["status"], "NO_TRADE")
        self.assertEqual(self._run(up, up, none)["status"], "NO_TRADE")

    def test_aligned_without_bounce_is_watchlist(self):
        up = scanner.MarketState(trend="up", phase="phase_1")
        r = self._run(up, up, up, ema_example("up", touch=False))
        self.assertEqual(r["status"], "WATCHLIST")


if __name__ == "__main__":
    print("READ-ONLY FX SCANNER SELF-TEST: synthetic candles; no MT5 or orders")
    unittest.main(verbosity=2)
