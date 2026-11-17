"""Read-only STARTRADER MT5 morning FX scan.

Uses the nightly report as a symbol list and comparison point, but rechecks
every pair's latest completed Daily candle independently. A pair that was
ineligible at 10 pm may become eligible after the overnight Daily close.

Checks completed D1/H4/H1 trends, forming-candle wick breaches,
H1 EMA stacking and EMA20 bounce. Equal swing levels invalidate only
after the swing's normal two-close confirmation, never from a touch alone.
Never submits, modifies, or cancels orders.
"""
import argparse
import csv
from datetime import datetime, timezone, timedelta
from pathlib import Path

import MetaTrader5 as mt5
from mt5_readonly import read_completed_candles, resolve_symbol
from scanner import analyse_structure, ema_bounce
from night_scan import DEFAULT_MT5_PATH

HEADERS = ['symbol', 'night_eligible', 'night_d1_bar_end', 'current_d1_bar_end',
           'daily_trend', 'daily_phase', 'h4_trend', 'h1_trend', 'ema_stack',
           'ema_bounce', 'latest_h1_bar_end', 'status', 'reason']

TIMEFRAMES = {'D1': mt5.TIMEFRAME_D1, 'H4': mt5.TIMEFRAME_H4,
              'H1': mt5.TIMEFRAME_H1}


def load_night_report(path):
    with path.open(newline='', encoding='utf-8-sig') as f:
        reader = csv.DictReader(f)
        needed = {'symbol', 'eligible', 'last_completed_d1_utc'}
        if not reader.fieldnames or not needed.issubset(reader.fieldnames):
            raise RuntimeError(f'Night report missing columns {sorted(needed)}')
        rows = list(reader)
    if not rows:
        raise RuntimeError('Night report has no symbols; run night_scan.py first')
    return rows


def iso_date(value):
    return datetime.fromisoformat(value.replace('Z', '+00:00')).astimezone(timezone.utc)


def read_forming_wick(symbol, timeframe, last_completed):
    """Read the high/low reached so far by MT5's *current* broker candle.

    The last completed candle's timestamp is the next candle's opening time.
    Matching these prevents a rollover between separate MT5 reads from being
    misinterpreted as one coherent snapshot.
    """
    bars = mt5.copy_rates_from_pos(symbol, TIMEFRAMES[timeframe], 0, 1)
    if bars is None or len(bars) != 1:
        raise RuntimeError(f'{symbol} {timeframe}: current candle unavailable: {mt5.last_error()}')
    current = bars[0]
    current_open = datetime.fromtimestamp(int(current['time']), tz=timezone.utc)
    expected_open = last_completed.timestamp.iloc[-1].to_pydatetime()
    if current_open != expected_open:
        raise RuntimeError(f'{symbol} {timeframe}: completed and forming candles are not aligned')
    high, low = float(current['high']), float(current['low'])
    if high < low:
        raise RuntimeError(f'{symbol} {timeframe}: current candle has inconsistent high/low')
    return {'high': high, 'low': low}


def apply_live_wick(state, wick, timeframe):
    """Invalidate on a *strict* protected-level breach, not an equal touch.

    Equal highs/lows still require a normally confirmed swing. The state is
    recalculated from history each scan, so a breach cannot be forgotten: while
    its candle forms the high/low retains the breach; after close, the ordinary
    completed-candle structure analysis detects that wick.
    """
    protected = state.protected_price
    if state.trend == 'up' and protected is not None and wick['low'] < protected:
        reason = f'{timeframe} forming wick low {wick["low"]} broke protected HL {protected}'
    elif state.trend == 'down' and protected is not None and wick['high'] > protected:
        reason = f'{timeframe} forming wick high {wick["high"]} broke protected LH {protected}'
    else:
        return ''
    state.clear_after_invalidation(reason)
    return reason


def evaluate(night, d1, h4=None, h1=None, now=None, tick=None, forming=None):
    """Evaluate a pair with fresh completed-candle data; never trades."""
    now = now or datetime.now(timezone.utc)
    symbol = night['symbol']
    out = {k: '' for k in HEADERS}
    out.update(symbol=symbol, night_eligible=night['eligible'],
               night_d1_bar_end=night['last_completed_d1_utc'],
               current_d1_bar_end=d1.timestamp.iloc[-1].isoformat())

    def stop(status, reason):
        out['status'], out['reason'] = status, reason
        return out

    # Do not use last night's eligibility as a gate: a new D1 candle may have
    # completed after the 10 pm scan. Still validate the nightly report so an
    # old or corrupted file cannot silently produce a trading setup.
    if night['eligible'].upper() not in ('YES', 'NO'):
        return stop('DATA_ERROR', 'Unrecognised night eligibility value')
    try:
        last_night = iso_date(night['last_completed_d1_utc'])
        current_d1 = iso_date(out['current_d1_bar_end'])
    except (ValueError, TypeError, AttributeError) as exc:
        return stop('DATA_ERROR', f'Invalid night or D1 timestamp: {exc}')
    if last_night > current_d1 or last_night > now:
        return stop('DATA_ERROR', 'Night scan date later than current completed Daily data')
    if current_d1 - last_night > timedelta(days=3):
        return stop('STALE_NIGHT_REPORT', 'Night report too old; run a fresh night scan')
    if current_d1 > now:
        return stop('DATA_ERROR', 'Completed D1 bar end appears in the future')

    d = analyse_structure(d1)
    if d.trend in ('up', 'down'):
        if not forming or 'D1' not in forming:
            return stop('DATA_ERROR', 'Current forming D1 candle missing; cannot validate wick breaches')
        live_break = apply_live_wick(d, forming['D1'], 'D1')
        if live_break:
            out.update(daily_trend=d.trend, daily_phase=d.phase)
            return stop('DAILY_INVALIDATED_LIVE', live_break)
    out.update(daily_trend=d.trend, daily_phase=d.phase)
    if d.trend not in ('up', 'down') or d.phase != 'phase_1' or d.invalidated:
        return stop('DAILY_REJECT', d.invalidation_reason or 'Daily not in confirmed Phase 1')

    if h4 is None or h1 is None:
        return stop('DATA_ERROR', 'H4/H1 data missing')
    four = analyse_structure(h4)
    one = analyse_structure(h1)
    out.update(h4_trend=four.trend, h1_trend=one.trend,
               latest_h1_bar_end=h1.timestamp.iloc[-1].isoformat())
    for timeframe, state in (('H4', four), ('H1', one)):
        if state.trend in ('up', 'down'):
            if not forming or timeframe not in forming:
                return stop('DATA_ERROR', f'Current forming {timeframe} candle missing')
            live_break = apply_live_wick(state, forming[timeframe], timeframe)
            if live_break:
                out.update(h4_trend=four.trend, h1_trend=one.trend)
                return stop('TIMEFRAME_INVALIDATED_LIVE', live_break)
    last_h1 = iso_date(out['latest_h1_bar_end'])
    if last_h1 > now or now - last_h1 > timedelta(hours=4):
        return stop('STALE_H1', 'Latest completed H1 candle is not recent; no setup alert')
    if four.trend != d.trend or one.trend != d.trend:
        return stop('MISALIGNED', 'Daily, H4 and H1 overall trends do not match')

    # Recheck the newest quote to reduce the race between reading the forming
    # candles and reading the current price. The candle high/lows above also
    # catch earlier wick breaches that recovered before this scan.
    if tick is None:
        return stop('DATA_ERROR', 'Live quote unavailable for protected-level check')
    for timeframe, state in (('D1', d), ('H4', four), ('H1', one)):
        if state.protected_price is None:
            return stop('UNPROTECTED', f'No confirmed protected HL/LH for {timeframe}')
        spot = {'high': float(tick.bid), 'low': float(tick.bid)}
        live_break = apply_live_wick(state, spot, timeframe)
        if live_break:
            out.update(daily_trend=d.trend, daily_phase=d.phase,
                       h4_trend=four.trend, h1_trend=one.trend)
            return stop('DAILY_INVALIDATED_LIVE' if timeframe == 'D1'
                        else 'TIMEFRAME_INVALIDATED_LIVE', live_break)

    e = ema_bounce(h1, d.trend)
    out['ema_stack'] = 'YES' if e['stacked'] else 'NO'
    out['ema_bounce'] = 'YES' if e['bounce'] else 'NO'
    status = 'SETUP_REVIEW' if e['stacked'] and e['bounce'] else 'WATCHLIST' if e['stacked'] else 'EMA_REJECT'
    return stop(status, e['reason'])


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--night-report', type=Path, default=Path('reports/night_scan_latest.csv'))
    p.add_argument('--mt5-path', default=DEFAULT_MT5_PATH)
    p.add_argument('--bars', type=int, default=1500)
    p.add_argument('--out', type=Path, default=Path('reports/morning_scan_latest.csv'))
    args = p.parse_args()
    if args.bars < 250:
        p.error('--bars must be at least 250')
    night_rows = load_night_report(args.night_report)
    if not mt5.initialize(path=args.mt5_path):
        raise SystemExit(f'MT5 connection failed: {mt5.last_error()}')

    outputs = []
    try:
        terminal = mt5.terminal_info()
        if not terminal or not terminal.connected:
            raise RuntimeError('MT5 is not connected to STARTRADER')
        if not terminal.tradeapi_disabled:
            raise RuntimeError('STOP: Enable Disable algorithmic trading via external Python API in MT5')
        print('MORNING SCAN — MANUAL REVIEW ONLY — NO ORDERS PLACED')
        for night in night_rows:
            symbol = night['symbol']
            try:
                symbol = resolve_symbol(symbol)
                if not mt5.symbol_select(symbol, True):
                    raise RuntimeError(f'Unable to select symbol: {mt5.last_error()}')
                # Re-fetch ALL pairs, including those marked NO overnight.
                d1 = read_completed_candles(symbol, 'D1', args.bars)
                h4 = read_completed_candles(symbol, 'H4', args.bars)
                h1 = read_completed_candles(symbol, 'H1', args.bars)
                forming = {name: read_forming_wick(symbol, name, history)
                           for name, history in (('D1', d1), ('H4', h4), ('H1', h1))}
                tick = mt5.symbol_info_tick(symbol)
                if tick is None:
                    raise RuntimeError('Live quote unavailable; cannot check protected level')
                result = evaluate(night, d1, h4, h1, tick=tick, forming=forming)
            except Exception as ex:
                result = {k: '' for k in HEADERS}
                result.update(symbol=symbol, night_eligible=night['eligible'],
                              night_d1_bar_end=night['last_completed_d1_utc'],
                              status='ERROR', reason=str(ex))
            outputs.append(result)
            print(f"{symbol:<12} {result['status']:<20} {result['reason']}")
        args.out.parent.mkdir(parents=True, exist_ok=True)
        with args.out.open('w', newline='', encoding='utf-8') as f:
            writer = csv.DictWriter(f, fieldnames=HEADERS)
            writer.writeheader()
            writer.writerows(outputs)
        print(f'\nSaved: {args.out.resolve()}')
        print('NO ORDERS PLACED. All market classifications are unvalidated research output.')
    finally:
        mt5.shutdown()


if __name__ == '__main__':
    main()
