"""Read-only, no broker connection, historical FX setup audit.

Uses the uploaded scanner.py unchanged. Each simulated morning checks the 22:00
Europe/London night snapshot strictly before the morning candle closes, then
rechecks Daily / H4 / H1 and the EMA bounce using available completed candles.
A bar's timestamp is its availability marker (next bar opening), not swing wick date.
This does NOT calculate profitability or simulate fills.
"""
import argparse
from pathlib import Path
import pandas as pd
from scanner import load_candles, analyse_structure, ema_bounce


def historical_scan(d1, h4, h1, start_hour=7, end_hour=12):
    """Return per-1H-candle audit, with hours specified in Europe/London [start,end)."""
    for name, frame in (("Daily", d1), ("4H", h4), ("1H", h1)):
        if len(frame) < 200:
            raise ValueError(f"{name}: needs at least 200 completed candles")
    # Cache state at every completed D1/H4 timestamp; H1 states computed on each chosen morning.
    d_cache, h4_cache = {}, {}
    out = []
    days = d1.timestamp
    fours = h4.timestamp
    for idx in range(199, len(h1)):
        instant = h1.timestamp.iloc[idx]
        local = instant.tz_convert("Europe/London")
        if not (start_hour <= local.hour < end_hour and local.weekday() < 5):
            continue
        # Latest night at 22:00 local, before this candle's availability instant.
        night = local.normalize() + pd.Timedelta(hours=22)
        if night >= local:
            night -= pd.Timedelta(days=1)
        # Reconstruct using local date, robust across daylight-saving transitions.
        night = pd.Timestamp(night.date(), tz="Europe/London") + pd.Timedelta(hours=22)
        if night >= local:
            yesterday = (local - pd.Timedelta(days=1)).date()
            night = pd.Timestamp(yesterday, tz="Europe/London") + pd.Timedelta(hours=22)
        night_utc = night.tz_convert("UTC")
        di_n = int(days.searchsorted(night_utc, side="right"))
        di_now = int(days.searchsorted(instant, side="right"))
        hi = int(fours.searchsorted(instant, side="right"))
        if di_n < 10 or di_now < 10 or hi < 10:
            continue
        def get(cache, frame, n):
            if n not in cache:
                cache[n] = analyse_structure(frame.iloc[:n])
            return cache[n]
        night_daily = get(d_cache, d1, di_n)
        now_daily = get(d_cache, d1, di_now)
        four = get(h4_cache, h4, hi)
        one = analyse_structure(h1.iloc[:idx+1])
        night_ok = night_daily.trend in ("up", "down") and night_daily.phase == "phase_1"
        daily_ok = now_daily.trend == night_daily.trend and now_daily.phase == "phase_1" and night_ok
        aligned = daily_ok and four.trend == now_daily.trend and one.trend == now_daily.trend
        ema = ema_bounce(h1.iloc[:idx+1], now_daily.trend) if aligned else {}
        if not night_ok:
            outcome, reason = "NOT_SHORTLISTED", "Night Daily not in confirmed Phase 1"
        elif not daily_ok:
            outcome, reason = "DAILY_FAIL", "Morning Daily trend/phase changed"
        elif not aligned:
            outcome, reason = "TREND_MISMATCH", "4H/1H trend not matching Daily"
        elif not ema.get("stacked"):
            outcome, reason = "EMA_STACK_FAIL", "1H EMA stack incorrect"
        elif ema.get("bounce"):
            outcome, reason = "SETUP", "Completed 1H bounce and all screening conditions"
        else:
            outcome, reason = "WATCHLIST", "Trend and EMA stack aligned; waiting for completed 1H bounce"
        out.append({
            "morning_close_utc": instant.isoformat(),
            "morning_close_uk": local.isoformat(),
            "night_check_uk": night.isoformat(),
            "night_daily_bar_available_utc": d1.timestamp.iloc[di_n-1].isoformat(),
            "night_daily_trend": night_daily.trend,
            "night_daily_phase": night_daily.phase,
            "morning_daily_trend": now_daily.trend,
            "morning_daily_phase": now_daily.phase,
            "h4_trend": four.trend, "h1_trend": one.trend,
            "ema20": ema.get("ema20"), "ema50": ema.get("ema50"), "ema200": ema.get("ema200"),
            "stacked": ema.get("stacked", False), "bounce": ema.get("bounce", False),
            "outcome": outcome, "reason": reason,
        })
    return pd.DataFrame(out)


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--daily', default='data/GBPUSD_D1.csv')
    p.add_argument('--h4', default='data/GBPUSD_H4.csv')
    p.add_argument('--h1', default='data/GBPUSD_H1.csv')
    p.add_argument('--start-hour',type=int,default=7,help='UK hour inclusive (default 7)')
    p.add_argument('--end-hour',type=int,default=12,help='UK hour exclusive (default 12)')
    p.add_argument('--output',default='reports/GBPUSD_historical_audit.csv')
    args=p.parse_args()
    if not 0 <= args.start_hour < args.end_hour <= 24:
        p.error('Hours must satisfy 0 <= start-hour < end-hour <= 24')
    frames=[load_candles(x) for x in (args.daily,args.h4,args.h1)]
    result=historical_scan(*frames,args.start_hour,args.end_hour)
    Path(args.output).parent.mkdir(parents=True,exist_ok=True)
    result.to_csv(args.output,index=False)
    print('HISTORICAL SETUP AUDIT - NO ORDERS PLACED')
    print('This is a signal-detection audit; it does not calculate trading returns.')
    print(f'Morning completed 1H candles assessed: {len(result)}')
    if not result.empty:
        print(result.outcome.value_counts().to_string())
        print(f'Date range: {result.morning_close_uk.iloc[0]} to {result.morning_close_uk.iloc[-1]}')
        print('\nMost recent potential setups:')
        rows=result[result.outcome=='SETUP'].tail(10)
        print(rows[['morning_close_uk','night_daily_trend','h4_trend','h1_trend','outcome']].to_string(index=False) if len(rows) else 'None within supplied history')
    print(f'Full audit: {args.output}')

if __name__ == '__main__':
    main()
