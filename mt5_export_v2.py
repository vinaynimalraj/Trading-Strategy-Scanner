"""Read-only STARTRADER/MT5 candle exporter for the research FX scanner.

Never imports, calls, or provides any order-placement function.
Uses the account already logged into the local MT5 desktop terminal.
"""
from pathlib import Path
import argparse
import re
import pandas as pd
import MetaTrader5 as mt5

TIMEFRAMES = {"D1": mt5.TIMEFRAME_D1, "H4": mt5.TIMEFRAME_H4, "H1": mt5.TIMEFRAME_H1}


def resolve_symbol(requested: str) -> str:
    available = mt5.symbols_get()
    if available is None:
        raise RuntimeError(f"Could not retrieve symbols: {mt5.last_error()}")
    names = [item.name for item in available]
    matches = [n for n in names if n.casefold() == requested.casefold()]
    if len(matches) == 1:
        return matches[0]
    # Do not silently assume a broker's .r, .pro, etc suffix.
    candidates = [n for n in names if n.upper().startswith(requested.upper())]
    raise ValueError(f"Exact symbol '{requested}' not found. Check Market Watch. Possible names: {candidates[:12]}")


def read_completed_candles(symbol: str, timeframe: str, count: int) -> pd.DataFrame:
    # MT5 position 0 is the currently forming candle. We deliberately exclude it.
    bars = mt5.copy_rates_from_pos(symbol, TIMEFRAMES[timeframe], 0, count + 1)
    if bars is None:
        raise RuntimeError(f"{symbol} {timeframe}: {mt5.last_error()}")
    if len(bars) < 202:
        raise RuntimeError(f"{symbol} {timeframe}: insufficient history ({len(bars)} bars)")
    df = pd.DataFrame(bars).sort_values("time").reset_index(drop=True)
    # Use the opening timestamp of the NEXT bar as an observable bar-end marker.
    # On weekends this is later than the physical Friday market close; the exported
    # last completed bars remain causal and are not treated as signals on Saturday.
    df["bar_open_utc"] = pd.to_datetime(df["time"], unit="s", utc=True)
    # timestamp remains the next bar opening (a conservative availability marker).
    # Keeping this name maintains compatibility with the existing scanners.
    df["timestamp"] = pd.to_datetime(df["time"].shift(-1), unit="s", utc=True)
    df = df.iloc[:-1][["timestamp", "bar_open_utc", "open", "high", "low", "close"]].copy()
    if df["timestamp"].isna().any():
        raise RuntimeError("Failed to obtain completed candle end times")
    return df


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--symbol", default="GBPUSD", help="Exact Market Watch symbol, e.g. GBPUSD or GBPUSD.r")
    parser.add_argument("--bars", type=int, default=1500, help="Candles per timeframe, 250 minimum")
    parser.add_argument("--outdir", default="data")
    parser.add_argument("--mt5-path", default=r"")
    args = parser.parse_args()
    if args.bars < 250:
        parser.error("--bars must be >= 250")
    if not (mt5.initialize(path=args.mt5_path) if args.mt5_path else mt5.initialize()):
        raise SystemExit(f"Could not connect to the open MT5 terminal: {mt5.last_error()}")
    try:
        terminal = mt5.terminal_info()
        if terminal is None or not terminal.connected:
            raise RuntimeError("MT5 is not connected to its broker server")
        # Refuse to continue if the safety setting permits Python trade requests.
        if not terminal.tradeapi_disabled:
            raise RuntimeError("STOP: In MT5 Tools > Options > Expert Advisors, tick 'Disable automatic trading through the external Python API', then rerun.")
        symbol = resolve_symbol(args.symbol)
        if not mt5.symbol_select(symbol, True):
            raise RuntimeError(f"Unable to add {symbol} to Market Watch: {mt5.last_error()}")
        outdir = Path(args.outdir)
        outdir.mkdir(parents=True, exist_ok=True)
        for name in ("D1", "H4", "H1"):
            df = read_completed_candles(symbol, name, args.bars)
            file = outdir / f"{re.sub(r'[^A-Za-z0-9_.-]', '_', symbol)}_{name}.csv"
            df.to_csv(file, index=False)
            print(f"{name}: saved {len(df)} completed candles -> {file}")
            print(f"  Last candle opened: {df.bar_open_utc.iloc[-1]} | available by: {df.timestamp.iloc[-1]}")
        print("Export complete. NO ORDERS PLACED. Run scanner.py next.")
    finally:
        mt5.shutdown()


if __name__ == "__main__":
    main()
