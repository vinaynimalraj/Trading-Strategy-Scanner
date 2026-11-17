FX SCANNER MULTI-PAIR HISTORICAL AUDIT (NO TRADES)
=================================================
Pairs: EURUSD, USDJPY, AUDUSD.
Uploaded candle files: D1, H4, H1; timestamp represents the candle availability time (not candle opening time).
Historical scanner evaluates 22:00 Europe/London night shortlist and completed 1H candles at 07:00–11:00 Europe/London weekdays.
Earliest evaluated morning: 2024-12-17; latest: 2026-11-10.

SUMMARY
EURUSD: 2459 checks; 2374 NOT_SHORTLISTED; 15 DAILY_FAIL; 70 TREND_MISMATCH; 0 EMA checks; 0 setups.
USDJPY: 2459 checks; 2409 NOT_SHORTLISTED; 15 DAILY_FAIL; 35 TREND_MISMATCH; 0 EMA checks; 0 setups.
AUDUSD: 2459 checks; 2284 NOT_SHORTLISTED; 25 DAILY_FAIL; 150 TREND_MISMATCH; 0 EMA checks; 0 setups.

INVESTIGATION
All three pairs produced 0 all-hour combinations of matching D1 (Phase 1), H4 and H1 trends in the provided H1 history, even when night shortlist and morning-window constraints were ignored. EMA calculations are separately capable of detecting stacked-bounce patterns, but no observed period passed the prior three-trend gate.

AUDUSD EXAMPLE
2025-06-05 12:00 UTC: H1 uptrend established.
2025-06-06 06:00 UTC: H1 candle low 0.64923 broke latest protected HL 0.65003, invalidating that uptrend under the wick rule.
2025-06-10 to 2025-06-12 UK mornings: D1 up Phase 1 and H4 up but H1 none.
See AUDUSD_H1_May_June_2025_swing_diagnostic.csv for swing events.

VALIDATION
The accelerated historical audit uses the event ordering and functions from scanner.py; structure snapshots were checked against analyse_structure on 63 pair/timeframe cutoffs, and selected morning audit slices were checked against historical_scan.py. No MT5 broker connection is used.

NEXT
Check at least one historical 1H swing/invalidation sequence against an annotated MT5 chart before concluding the rule set or implementation is correct. Do not loosen rules to manufacture trade signals. This is not a profitability study; no fills, slippage, transaction costs or return simulation.

RE-RUN ON WINDOWS
Place multi_pair_audit.py next to existing scanner.py. In your scanner folder, after exporting D1/H4/H1 data into data/:
py -3.12 multi_pair_audit.py --data-dir data --output-dir reports --pairs EURUSD USDJPY AUDUSD
The script reads local CSVs only and NEVER places orders.
