"""Research-only FX multi-timeframe scanner. No broker connections or order execution.

Input: CSV files with timestamp,open,high,low,close; timestamps must be
UTC ISO-8601, all candles completed, and 4H / daily candle boundaries must
match the user's trading platform. CSV paths: one file each for D1,H4,H1.

Assumptions to validate on annotated charts:
- Swing detection starts at first bar, watches for first confirmed extreme,
  then alternates high/low candidates. New extreme restarts its close count.
- Confirmed trend requires chronological alternating HH,HL,HH,HL (or reverse),
  where each label compares with the prior swing of the same kind. After an
  invalidation, all anchors and counts restart from scratch.
- Confirmation and EMA evaluation use CLOSED candles only. Protected HL/LH
  invalidation uses a wick and can be processed intrabar if live feed is added.
- EMA is calculated from closing prices using pandas ewm(adjust=False), with
  at least 200 observations before accepting a stack.
- The first confirmed trend is assigned a phase from its last swing type.
- Scanner reports latest completed 1H bounce only; historical signals are
  deliberately not counted as current.
"""
from __future__ import annotations

import argparse
from dataclasses import dataclass, field
from pathlib import Path
import json
import pandas as pd

REQUIRED = ["timestamp", "open", "high", "low", "close"]


def load_candles(path: str | Path) -> pd.DataFrame:
    df = pd.read_csv(path)
    missing = set(REQUIRED).difference(df.columns)
    if missing:
        raise ValueError(f"{path}: missing columns {sorted(missing)}")
    df = df[REQUIRED].copy()
    df["timestamp"] = pd.to_datetime(df["timestamp"], utc=True)
    if df["timestamp"].isna().any() or df["timestamp"].duplicated().any():
        raise ValueError(f"{path}: invalid or duplicate timestamps")
    df = df.sort_values("timestamp").reset_index(drop=True)
    for name in ["open", "high", "low", "close"]:
        df[name] = pd.to_numeric(df[name], errors="raise")
    if df.empty or df[["open", "high", "low", "close"]].isna().any().any():
        raise ValueError(f"{path}: missing candle values")
    if ((df.high < df[["open", "low", "close"]].max(axis=1)) |
        (df.low > df[["open", "high", "close"]].min(axis=1))).any():
        raise ValueError(f"{path}: inconsistent OHLC values")
    return df


@dataclass
class Swing:
    kind: str  # high or low
    price: float  # actual extreme wick
    reference_time: str
    confirmed_time: str
    label: str = ""  # HH/LH/HL/LL/EH/EL (first of kind is unlabelled)


@dataclass
class MarketState:
    trend: str = "none"
    phase: str = "undetermined"
    invalidated: bool = False
    invalidation_reason: str = ""
    swings: list[Swing] = field(default_factory=list)
    protected_price: float | None = None
    protected_kind: str | None = None
    last_high: float | None = None
    last_low: float | None = None
    labels: list[str] = field(default_factory=list)
    seek: str = "both"
    candidate_high: dict | None = None
    candidate_low: dict | None = None

    def clear_after_invalidation(self, reason: str):
        self.trend = "none"
        self.phase = "undetermined"
        self.invalidated = True
        self.invalidation_reason = reason
        self.swings.clear()
        self.labels.clear()
        self.last_high = self.last_low = None
        self.protected_price = None
        self.protected_kind = None
        self.seek = "both"
        self.candidate_high = self.candidate_low = None


def _new_candidate(kind: str, r) -> dict:
    return {"price": float(r.high if kind == "high" else r.low),
            "threshold": float(r.low if kind == "high" else r.high),
            "time": r.timestamp.isoformat(), "streak": 0}


def _advance_candidate(kind: str, candidate: dict | None, r) -> tuple[dict, bool]:
    if candidate is None:
        return _new_candidate(kind, r), False
    extreme = float(r.high if kind == "high" else r.low)
    more_extreme = extreme > candidate["price"] if kind == "high" else extreme < candidate["price"]
    if more_extreme:
        return _new_candidate(kind, r), False
    close = float(r.close)
    beyond = close < candidate["threshold"] if kind == "high" else close > candidate["threshold"]
    candidate["streak"] = candidate["streak"] + 1 if beyond else 0
    return candidate, candidate["streak"] >= 2


def _record_swing(s: MarketState, kind: str, candidate: dict, confirmed_time: str) -> bool:
    price = candidate["price"]
    prior = s.last_high if kind == "high" else s.last_low
    if prior is None:
        label = ""
    elif kind == "high":
        label = "HH" if price > prior else "LH" if price < prior else "EH"
    else:
        label = "HL" if price > prior else "LL" if price < prior else "EL"
    swing = Swing(kind, price, candidate["time"], confirmed_time, label)
    s.swings.append(swing)
    if kind == "high": s.last_high = price
    else: s.last_low = price
    if label:
        s.labels.append(label)

    if s.trend == "up" and label in {"LH", "LL", "EH", "EL"}:
        s.clear_after_invalidation(f"Confirmed {label} violated uptrend")
        return False
    if s.trend == "down" and label in {"HH", "HL", "EH", "EL"}:
        s.clear_after_invalidation(f"Confirmed {label} violated downtrend")
        return False

    if s.trend == "none":
        # Because swing highs and lows must alternate, this also implies
        # chronological HH-HL-HH-HL or HL-HH-HL-HH.
        last = s.labels[-4:]
        if len(last) == 4 and last.count("HH") == 2 and last.count("HL") == 2:
            s.trend = "up"
            s.invalidated = False
            s.invalidation_reason = ""
        elif len(last) == 4 and last.count("LL") == 2 and last.count("LH") == 2:
            s.trend = "down"
            s.invalidated = False
            s.invalidation_reason = ""

    if s.trend == "up":
        s.phase = "phase_2" if kind == "high" else "phase_1"
    elif s.trend == "down":
        s.phase = "phase_2" if kind == "low" else "phase_1"

    # The latest confirmed HL or LH protects the active trend.
    if s.trend == "up" and label == "HL":
        s.protected_price, s.protected_kind = price, "HL"
    elif s.trend == "down" and label == "LH":
        s.protected_price, s.protected_kind = price, "LH"
    elif s.trend == "up" and s.protected_price is None:
        last_hl = next((v.price for v in reversed(s.swings) if v.label == "HL"), None)
        s.protected_price, s.protected_kind = last_hl, "HL"
    elif s.trend == "down" and s.protected_price is None:
        last_lh = next((v.price for v in reversed(s.swings) if v.label == "LH"), None)
        s.protected_price, s.protected_kind = last_lh, "LH"


    return True


def analyse_structure(df: pd.DataFrame) -> MarketState:
    s = MarketState()
    for r in df.itertuples(index=False):
        # Wick invalidation has priority over swing confirmation.
        if s.trend == "up" and s.protected_price is not None and r.low < s.protected_price:
            s.clear_after_invalidation(f"Low {r.low} breached protected HL {s.protected_price}")
        elif s.trend == "down" and s.protected_price is not None and r.high > s.protected_price:
            s.clear_after_invalidation(f"High {r.high} breached protected LH {s.protected_price}")

        if s.seek in ("both", "high"):
            s.candidate_high, got_high = _advance_candidate("high", s.candidate_high, r)
        else:
            got_high = False
        if s.seek in ("both", "low"):
            s.candidate_low, got_low = _advance_candidate("low", s.candidate_low, r)
        else:
            got_low = False
        if got_high and got_low:
            # In ambiguous first-data case neither is safe to declare first.
            continue
        if got_high:
            accepted = _record_swing(s, "high", s.candidate_high, r.timestamp.isoformat())
            if accepted:
                s.seek, s.candidate_high, s.candidate_low = "low", None, _new_candidate("low", r)
        elif got_low:
            accepted = _record_swing(s, "low", s.candidate_low, r.timestamp.isoformat())
            if accepted:
                s.seek, s.candidate_low, s.candidate_high = "high", None, _new_candidate("high", r)
    return s


def ema_bounce(df: pd.DataFrame, direction: str) -> dict:
    if len(df) < 200:
        return {"stacked": False, "bounce": False, "reason": "Need at least 200 completed 1H candles"}
    frame = df.copy()
    for n in (20, 50, 200):
        frame[f"ema{n}"] = frame.close.ewm(span=n, adjust=False).mean()
    r = frame.iloc[-1]
    ema = float(r.ema20)
    if direction == "up":
        stacked = bool(r.ema20 > r.ema50 > r.ema200)
        bounce = bool(r.open > ema and r.low <= ema and r.close > ema)
    elif direction == "down":
        stacked = bool(r.ema20 < r.ema50 < r.ema200)
        bounce = bool(r.open < ema and r.high >= ema and r.close < ema)
    else:
        return {"stacked": False, "bounce": False, "reason": "No valid daily direction"}
    return {"stacked": stacked, "bounce": bounce,
            "ema20": round(ema, 6), "ema50": round(float(r.ema50), 6),
            "ema200": round(float(r.ema200), 6),
            "bounce_candle_time": r.timestamp.isoformat(),
            "reason": "All EMA conditions met" if stacked and bounce else
                      "EMA stack failed" if not stacked else "No completed bounce on latest 1H candle"}


def scan(daily: pd.DataFrame, h4: pd.DataFrame, h1: pd.DataFrame) -> dict:
    # Assume caller has filtered inputs to the same as-of timestamp.
    d, four, one = [analyse_structure(x) for x in (daily, h4, h1)]
    result = {"daily": {"trend": d.trend, "phase": d.phase,
                        "protected": d.protected_price, "invalidated": d.invalidated,
                        "reason": d.invalidation_reason,
                        "recent_swings": [vars(x) for x in d.swings[-8:]]},
              "h4_trend": four.trend, "h1_trend": one.trend,
              "asof": h1.timestamp.iloc[-1].isoformat()}
    if d.trend == "none":
        status, reason = "NO_TRADE", "No confirmed daily trend"
    elif d.phase != "phase_1":
        status, reason = "NO_TRADE", "Daily is not Phase 1"
    elif four.trend != d.trend or one.trend != d.trend:
        status, reason = "NO_TRADE", "4H / 1H trend misalignment"
    else:
        e = ema_bounce(h1, d.trend)
        result["ema"] = e
        status = "SETUP" if e["stacked"] and e["bounce"] else "WATCHLIST" if e["stacked"] else "NO_TRADE"
        reason = e["reason"]
    result.update({"status": status, "reason": reason})
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--daily", required=True)
    parser.add_argument("--h4", required=True)
    parser.add_argument("--h1", required=True)
    args = parser.parse_args()
    print(json.dumps(scan(load_candles(args.daily), load_candles(args.h4), load_candles(args.h1)), indent=2))


if __name__ == "__main__":
    main()
