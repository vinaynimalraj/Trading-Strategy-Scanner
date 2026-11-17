# Trading Strategy Scanner

A read-only Python scanner for GBPUSD, EURUSD, USDJPY and AUDUSD. It shortlists daily trends, checks alignment across D1, H4 and H1, and flags completed H1 candles that meet the EMA rules for manual review.

The GitHub Pages site is in `docs/`. It displays the 17 November 2026 scan snapshot and links to the two CSVs. The scanner itself runs locally with MetaTrader 5.

## Run on Windows

1. Open STARTRADER MetaTrader 5 and connect to your broker in the terminal. In **Tools → Options → Expert Advisors**, tick **Disable automatic trading through the external Python API**, as required by the scan scripts.
2. Open PowerShell in this folder with 64-bit Python installed and run `py -3.12 -m pip install -r requirements.txt`.
3. Run the night scan around 22:00 UK time with `py -3.12 night_scan.py`. The latest completed D1 candle establishes structure. The script also checks the forming D1 candle and live quote for a breach.
4. Run the morning scan with `py -3.12 morning_scan.py`. It reads the night report, rechecks D1 and looks for H4 and H1 alignment, an H1 EMA stack and an EMA20 bounce.
5. Open `reports/night_scan_latest.csv` and `reports/morning_scan_latest.csv`. If you have multiple MT5 installations, pass `--mt5-path "C:\path\to\terminal64.exe"` to select the STARTRADER terminal.

## Files

| File | Purpose |
| --- | --- |
| `scanner.py` | Swing, trend, phase and EMA calculations |
| `night_scan.py`, `morning_scan.py` | Live read-only scan entry points |
| `mt5_readonly.py` | MT5 symbol lookup and completed-candle reader |
| `mt5_export_v2.py` | Export historical D1, H4 and H1 candles |
| `historical_scan.py`, `multi_pair_audit.py` | Replay the strategy over candle CSVs |
| `daily_diagnostic.py` | Inspect daily swing confirmations and invalidations |
| `validate_fx_scanner.py` | Existing strategy checks |
| `docs/`, `examples/audit/`, `examples/candles/` | Pages site, scan snapshots, historical audit outputs and candle exports |

Run the existing strategy checks with `py -3.12 -m unittest validate_fx_scanner -v`.

Reproduce the included multi-pair summary with `py -3.12 multi_pair_audit.py --data-dir examples/candles`.

To run a historical scan against fresh data, export GBPUSD candles with `py -3.12 mt5_export_v2.py --symbol GBPUSD`, then run `py -3.12 historical_scan.py`. For broker-suffixed filenames, pass the exported filenames explicitly with `--daily`, `--h4` and `--h1`. Historical scan results count rule matches and outcomes of the filters. The scripts do not calculate P&L.

## Publish the site

Upload the repository contents to GitHub. In **Settings → Pages**, choose **Deploy from a branch**, select **main** and **/docs**, then save. Once the deployment completes, open the address shown in Pages. The site is a static snapshot. To refresh it after future scans, update the report copies and the displayed table and date in `docs/`, then commit the changes.
