"""Read-only Daily trend diagnostics for the existing FX scanner.

Put beside scanner.py and run: py -3.12 daily_diagnostic.py
No MT5 connection, account access, order placement, or trading actions.
"""
from pathlib import Path
import argparse
from collections import Counter
import pandas as pd
import scanner as sc


def diagnose(frame):
    state = sc.MarketState()
    events = []
    counts = Counter()
    live_bars = Counter()
    for r in frame.itertuples(index=False):
        when = r.timestamp.isoformat()
        prior_trend = state.trend
        if state.trend == 'up' and state.protected_price is not None and r.low < state.protected_price:
            reason = f'Low {r.low:.5f} breached protected HL {state.protected_price:.5f}'
            state.clear_after_invalidation(reason)
            events.append(dict(date=when, event='INVALIDATION_WICK', trend_before='up', trend_after='none', swing='', price='', reason=reason))
            counts['invalidations_wick'] += 1
        elif state.trend == 'down' and state.protected_price is not None and r.high > state.protected_price:
            reason = f'High {r.high:.5f} breached protected LH {state.protected_price:.5f}'
            state.clear_after_invalidation(reason)
            events.append(dict(date=when, event='INVALIDATION_WICK', trend_before='down', trend_after='none', swing='', price='', reason=reason))
            counts['invalidations_wick'] += 1
        if state.seek in ('both','high'):
            state.candidate_high, got_high = sc._advance_candidate('high',state.candidate_high,r)
        else:
            got_high = False
        if state.seek in ('both','low'):
            state.candidate_low, got_low = sc._advance_candidate('low',state.candidate_low,r)
        else:
            got_low = False
        if got_high and got_low:
            counts['ambiguous_confirmations'] += 1
            live_bars[state.trend] += 1
            continue
        if got_high or got_low:
            kind = 'high' if got_high else 'low'
            candidate = state.candidate_high if got_high else state.candidate_low
            price = float(candidate['price'])
            prev = state.last_high if kind=='high' else state.last_low
            label = ('' if prev is None else
                     ('HH' if price>prev else 'LH' if price<prev else 'EH') if kind=='high' else
                     ('HL' if price>prev else 'LL' if price<prev else 'EL'))
            was = state.trend
            accepted = sc._record_swing(state,kind,candidate,when)
            counts['confirmed_swings'] += 1
            counts[f'label_{label or "initial"}'] += 1
            events.append(dict(date=when,event='CONFIRMED_SWING',trend_before=was,trend_after=state.trend,swing=label or kind,price=round(price,5),reason=''))
            if was != state.trend:
                name = 'TREND_ESTABLISHED' if state.trend!='none' else 'INVALIDATION_SWING'
                counts[name.lower()] += 1
                events.append(dict(date=when,event=name,trend_before=was,trend_after=state.trend,swing=label,price=round(price,5),reason=state.invalidation_reason))
            if accepted:
                if got_high:
                    state.seek, state.candidate_high, state.candidate_low = 'low',None,sc._new_candidate('low',r)
                else:
                    state.seek, state.candidate_low, state.candidate_high = 'high',None,sc._new_candidate('high',r)
        live_bars[state.trend] += 1
    return state,pd.DataFrame(events,columns=['date','event','trend_before','trend_after','swing','price','reason']),counts,live_bars


def main():
    p=argparse.ArgumentParser()
    p.add_argument('--daily',default='data/GBPUSD_D1.csv')
    p.add_argument('--output',default='reports/GBPUSD_daily_diagnostic.csv')
    args=p.parse_args()
    df=sc.load_candles(args.daily)
    state,events,counts,bars=diagnose(df)
    check=sc.analyse_structure(df)
    assert (state.trend,state.phase,state.invalidation_reason)==(check.trend,check.phase,check.invalidation_reason), 'Diagnostic diverged from scanner; do not rely on this output'
    out=Path(args.output)
    out.parent.mkdir(parents=True,exist_ok=True)
    events.to_csv(out,index=False)
    print('DAILY SCANNER DIAGNOSTIC — NO TRADES OR BROKER CONNECTION')
    print('Candles:',len(df),'from',df.timestamp.iloc[0], 'to',df.timestamp.iloc[-1])
    print('Confirmed swing labels:',{key.removeprefix('label_'):val for key,val in counts.items() if key.startswith('label_')})
    print('Trend established:',counts['trend_established'],'Wick invalidations:',counts['invalidations_wick'],'Swing invalidations:',counts['invalidation_swing'])
    print('Bars by trend:',dict(bars))
    print('Final:',state.trend,'/',state.phase)
    print('Recent trend changes:')
    changes=events[events.event.isin(['TREND_ESTABLISHED','INVALIDATION_WICK','INVALIDATION_SWING'])]
    if changes.empty: print('No trend transitions found')
    else: print(changes.tail(12)[['date','event','trend_before','trend_after','reason']].to_string(index=False))
    print('Saved:',out)

if __name__=='__main__':
    main()
