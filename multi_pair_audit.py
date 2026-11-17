"""Read-only multi-pair historical scanner audit, based on scanner.py strategy rules.

Usage (from the folder containing scanner.py):
    py -3.12 multi_pair_audit.py --data-dir data --pairs EURUSD USDJPY AUDUSD

Generates per-pair historical audit CSVs and summary; NO orders or broker calls.
"""
import argparse
from pathlib import Path
import pandas as pd
from scanner import load_candles, MarketState, _advance_candidate, _new_candidate, _record_swing


def precompute_states(frame):
    """One-pass copy of the analyse_structure event order; snapshots after each closed bar."""
    s = MarketState()
    out = []
    for r in frame.itertuples(index=False):
        if s.trend == 'up' and s.protected_price is not None and r.low < s.protected_price:
            s.clear_after_invalidation(f'Low {r.low} breached protected HL {s.protected_price}')
        elif s.trend == 'down' and s.protected_price is not None and r.high > s.protected_price:
            s.clear_after_invalidation(f'High {r.high} breached protected LH {s.protected_price}')
        if s.seek in ('both','high'):
            s.candidate_high, got_high = _advance_candidate('high', s.candidate_high, r)
        else: got_high = False
        if s.seek in ('both','low'):
            s.candidate_low, got_low = _advance_candidate('low', s.candidate_low, r)
        else: got_low = False
        if got_high and got_low:
            pass
        elif got_high:
            accepted = _record_swing(s,'high',s.candidate_high,r.timestamp.isoformat())
            if accepted:
                s.seek, s.candidate_high, s.candidate_low = 'low',None,_new_candidate('low',r)
        elif got_low:
            accepted = _record_swing(s,'low',s.candidate_low,r.timestamp.isoformat())
            if accepted:
                s.seek, s.candidate_low, s.candidate_high = 'high',None,_new_candidate('high',r)
        out.append((s.trend,s.phase,s.protected_price))
    return out


def night_for(local):
    """Match historical_scan.py's 22:00 Europe/London cutoff."""
    night = local.normalize() + pd.Timedelta(hours=22)
    if night >= local:
        night -= pd.Timedelta(days=1)
    night = pd.Timestamp(night.date(),tz='Europe/London') + pd.Timedelta(hours=22)
    if night >= local:
        yesterday = (local-pd.Timedelta(days=1)).date()
        night = pd.Timestamp(yesterday,tz='Europe/London') + pd.Timedelta(hours=22)
    return night


def historical_scan_fast(d1,h4,h1,start_hour=7,end_hour=12):
    for name,frame in [('Daily',d1),('4H',h4),('1H',h1)]:
        if len(frame)<200: raise ValueError(f'{name}: needs at least 200 completed candles')
    daily = precompute_states(d1)
    four = precompute_states(h4)
    one = precompute_states(h1)
    close = h1.close
    em20 = close.ewm(span=20,adjust=False).mean()
    em50 = close.ewm(span=50,adjust=False).mean()
    em200 = close.ewm(span=200,adjust=False).mean()
    days=d1.timestamp
    fours=h4.timestamp
    out=[]
    for idx in range(199,len(h1)):
        instant=h1.timestamp.iloc[idx]
        local=instant.tz_convert('Europe/London')
        if not (start_hour<=local.hour<end_hour and local.weekday()<5): continue
        night=night_for(local)
        night_utc=night.tz_convert('UTC')
        di_n=int(days.searchsorted(night_utc,side='right'))
        di_now=int(days.searchsorted(instant,side='right'))
        hi=int(fours.searchsorted(instant,side='right'))
        if di_n<10 or di_now<10 or hi<10: continue
        nd,np,_=daily[di_n-1]
        dd,dp,_=daily[di_now-1]
        ft,_,_=four[hi-1]
        ot,_,_=one[idx]
        night_ok = nd in ('up','down') and np=='phase_1'
        daily_ok=dd==nd and dp=='phase_1' and night_ok
        aligned=daily_ok and ft==dd and ot==dd
        ema={}
        if aligned:
            a,b,c=map(float,(em20.iloc[idx],em50.iloc[idx],em200.iloc[idx]))
            r=h1.iloc[idx]
            if dd=='up':
                stacked=bool(a>b>c)
                bounce=bool(r.open>a and r.low<=a and r.close>a)
            else:
                stacked=bool(a<b<c)
                bounce=bool(r.open<a and r.high>=a and r.close<a)
            ema={'ema20':round(a,6),'ema50':round(b,6),'ema200':round(c,6),'stacked':stacked,'bounce':bounce}
        if not night_ok: outcome,reason='NOT_SHORTLISTED','Night Daily not in confirmed Phase 1'
        elif not daily_ok: outcome,reason='DAILY_FAIL','Morning Daily trend/phase changed'
        elif not aligned: outcome,reason='TREND_MISMATCH','4H/1H trend not matching Daily'
        elif not ema.get('stacked'): outcome,reason='EMA_STACK_FAIL','1H EMA stack incorrect'
        elif ema.get('bounce'): outcome,reason='SETUP','Completed 1H bounce and all screening conditions'
        else: outcome,reason='WATCHLIST','Trend and EMA stack aligned; waiting for completed 1H bounce'
        out.append({'morning_close_utc':instant.isoformat(),
                    'morning_close_uk':local.isoformat(),
                    'night_check_uk':night.isoformat(),
                    'night_daily_bar_available_utc':d1.timestamp.iloc[di_n-1].isoformat(),
                    'night_daily_trend':nd,'night_daily_phase':np,
                    'morning_daily_trend':dd,'morning_daily_phase':dp,
                    'h4_trend':ft,'h1_trend':ot,
                    'ema20':ema.get('ema20'),'ema50':ema.get('ema50'),'ema200':ema.get('ema200'),
                    'stacked':ema.get('stacked',False),'bounce':ema.get('bounce',False),
                    'outcome':outcome,'reason':reason})
    return pd.DataFrame(out)


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--data-dir',default='data')
    p.add_argument('--output-dir',default='reports')
    p.add_argument('--pairs',nargs='+',default=['EURUSD','USDJPY','AUDUSD'])
    p.add_argument('--start-hour',type=int,default=7)
    p.add_argument('--end-hour',type=int,default=12)
    args=p.parse_args()
    output=Path(args.output_dir)
    output.mkdir(parents=True,exist_ok=True)
    summaries=[]
    for pair in args.pairs:
        paths=[Path(args.data_dir)/f'{pair}_{tf}.csv' for tf in ('D1','H4','H1')]
        frames=[load_candles(x) for x in paths]
        audit=historical_scan_fast(*frames,args.start_hour,args.end_hour)
        audit.to_csv(output/f'{pair}_historical_audit.csv',index=False)
        counts=audit.outcome.value_counts().to_dict()
        print(f'\n{pair}: {len(audit):,} historical morning checks; NO ORDERS PLACED')
        print(audit.outcome.value_counts().to_string())
        if 'SETUP' in counts:
            print('SETUP times:',audit.loc[audit.outcome=='SETUP','morning_close_uk'].to_list())
        row={'pair':pair,'first_morning_uk':audit.morning_close_uk.iloc[0],
             'last_morning_uk':audit.morning_close_uk.iloc[-1],'morning_checks':len(audit),
             **{k:counts.get(k,0) for k in ['NOT_SHORTLISTED','DAILY_FAIL','TREND_MISMATCH','EMA_STACK_FAIL','WATCHLIST','SETUP']}}
        summaries.append(row)
    summary=pd.DataFrame(summaries)
    summary.to_csv(output/'multi_pair_summary.csv',index=False)
    print('\nSUMMARY')
    print(summary.to_string(index=False))

if __name__=='__main__': main()
