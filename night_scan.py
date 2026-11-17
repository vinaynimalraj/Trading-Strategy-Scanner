"""Read-only STARTRADER night scan: confirmed D1 trend and phase.

Derives swings from completed broker D1 candles, then checks the currently
forming D1 candle's full high/low for a protected-level wick break. Equal
levels alone do not invalidate: equal swings need their normal two-close
confirmation. Does not submit, modify, or close orders.
"""
import argparse
from datetime import datetime, timezone
from pathlib import Path
import csv
import MetaTrader5 as mt5
from scanner import analyse_structure
from mt5_readonly import resolve_symbol, read_completed_candles

DEFAULT_MT5_PATH = r''


def read_forming_d1_wick(symbol, completed):
    """Return the broker's current D1 high and low, rejecting rollover races."""
    candles = mt5.copy_rates_from_pos(symbol, mt5.TIMEFRAME_D1, 0, 1)
    if candles is None or len(candles) != 1:
        raise RuntimeError(f'{symbol} D1: current candle unavailable: {mt5.last_error()}')
    current = candles[0]
    current_start = datetime.fromtimestamp(int(current['time']), timezone.utc)
    expected_start = completed.timestamp.iloc[-1].to_pydatetime()
    if current_start != expected_start:
        raise RuntimeError(f'{symbol} D1: completed and forming candles are not aligned')
    high, low = float(current['high']), float(current['low'])
    if high < low:
        raise RuntimeError(f'{symbol} D1: current candle has inconsistent high/low')
    return {'high': high, 'low': low}


def invalidate_on_protected_wick(state, wick, context='D1 forming'):
    """An actual *breach* invalidates immediately, an equal touch does not."""
    protected = state.protected_price
    if state.trend == 'up' and protected is not None and wick['low'] < protected:
        reason = f'{context} wick low {wick["low"]} broke protected HL {protected}'
    elif state.trend == 'down' and protected is not None and wick['high'] > protected:
        reason = f'{context} wick high {wick["high"]} broke protected LH {protected}'
    else:
        return ''
    state.clear_after_invalidation(reason)
    return reason


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--symbols', nargs='+', default=['GBPUSD', 'EURUSD', 'USDJPY', 'AUDUSD'])
    parser.add_argument('--mt5-path', default=DEFAULT_MT5_PATH)
    parser.add_argument('--bars', type=int, default=1500)
    args = parser.parse_args()
    if args.bars < 250:
        parser.error('--bars must be at least 250')
    if not (mt5.initialize(path=args.mt5_path) if args.mt5_path else mt5.initialize()):
        raise SystemExit(f'Could not connect to STARTRADER MT5: {mt5.last_error()}')
    rows = []
    try:
        terminal = mt5.terminal_info()
        if not terminal or not terminal.connected:
            raise RuntimeError('MT5 terminal is not connected to STARTRADER')
        if not terminal.tradeapi_disabled:
            raise RuntimeError('STOP: Disable algorithmic trading via external Python API in MT5 settings before proceeding')
        print('NIGHT SCAN — D1 STRUCTURE AND LIVE WICK CHECK — NO ORDERS PLACED')
        print('At 10pm UK, the broker D1 candle may still be forming; the morning scan rechecks after its close.\n')
        for requested in args.symbols:
            try:
                symbol = resolve_symbol(requested)
                if not mt5.symbol_select(symbol, True):
                    raise RuntimeError(f'Cannot select symbol: {mt5.last_error()}')
                bars = read_completed_candles(symbol, 'D1', args.bars)
                state = analyse_structure(bars)
                forming = read_forming_d1_wick(symbol, bars)
                invalidate_on_protected_wick(state, forming)
                # The forming candle records an earlier breach even if price
                # recovers before this scan. Check the latest bid as well to
                # reduce the race between fetching bars and current prices.
                tick = mt5.symbol_info_tick(symbol)
                if tick is None:
                    raise RuntimeError(f'{symbol} D1: live quote unavailable for wick check')
                invalidate_on_protected_wick(state, {'high': float(tick.bid),
                                                      'low': float(tick.bid)}, 'D1 quote')
                valid = state.trend in ('up','down') and state.phase == 'phase_1' and not state.invalidated
                row = dict(symbol=symbol, daily_trend=state.trend, daily_phase=state.phase,
                           eligible='YES' if valid else 'NO', last_completed_d1_utc=bars.timestamp.iloc[-1].isoformat(),
                           reason=state.invalidation_reason if state.invalidated else ('Phase 1 trend confirmed' if valid else 'Not a valid daily Phase 1 trend'))
            except Exception as ex:
                row = dict(symbol=requested, daily_trend='ERROR', daily_phase='-', eligible='NO', last_completed_d1_utc='', reason=str(ex))
            rows.append(row)
            print(f"{row['symbol']:12} trend={row['daily_trend']:5} phase={row['daily_phase']:13} shortlist={row['eligible']:3}  {row['reason']}")
        output_dir = Path('reports')
        output_dir.mkdir(exist_ok=True)
        output_path = output_dir / 'night_scan_latest.csv'
        with output_path.open('w', newline='', encoding='utf-8') as f:
            writer = csv.DictWriter(f, fieldnames=list(rows[0]))
            writer.writeheader()
            writer.writerows(rows)
        print(f'\nSaved report: {output_path.resolve()}')
        print('Read-only analysis finished. NO ORDERS PLACED.')
    finally:
        mt5.shutdown()

if __name__ == '__main__':
    main()
