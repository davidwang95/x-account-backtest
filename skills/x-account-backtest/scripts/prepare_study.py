"""Freeze the publication window and scoring plan before viewing outcomes."""
from __future__ import annotations
import argparse
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from study_utils import NY, account_handle, date_window, write_json

def prepare(account, start, end, cutoff, primary=21, horizons=(1,5,21,63,126,252), seed=20261002):
    handle = account_handle(account)
    begin, finish = date_window(start, end)
    date.fromisoformat(cutoff)
    if primary not in horizons or any(type(h) is not int or h < 1 for h in horizons):
        raise ValueError('Primary horizon must be one of the positive session horizons.')
    return {'schemaVersion':'1.0', 'account':{'handle':handle,'displayName':'@'+handle,'profileUrl':'https://x.com/'+handle},
            'startDate':start,'endDate':end,'timezone':'America/New_York','startUTC':begin,'endExclusiveUTC':finish,
            'cutoff':cutoff,'primary':primary,'horizons':sorted(set(horizons)), 'cryptoPrimary':30,'cryptoHorizons':[1,7,30,90,365],
            'seed':seed,'bootstrapIterations':2000,'benchmark':'SPY', 'reportDate':datetime.now(NY).date().isoformat(),
            'frozenAtUTC':datetime.now(timezone.utc).isoformat(), 'outcomesInspectedBeforePlan':False,
            'entryRule':'First trading session strictly after the New York publication date; daily open proxy.',
            'priceBasis':'Split-adjusted price returns; dividends, costs and borrow excluded.',
            'sourceText':'Recoverable public authored X text; replies and authored quote comments included. Native retweets, third-party quotes, video speech and linked articles excluded.',
            'archiveSource':'Configured connector, SocialData search or supplied archive; actual coverage reported in archive-coverage.json.',
            'priceSource':'Verified historical daily bars; actual provenance reported in price-manifest.json.',
            'brand':'Dave Wang Automation', 'automationDisclosure':'AI-generated backtest produced using the x-account-backtest skill. This is automated output, not research authored by Dave Wang.',
            'resourceUrl':'https://www.davewang.ai'}

def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--account',required=True); p.add_argument('--output-dir',type=Path,required=True)
    p.add_argument('--start'); p.add_argument('--end'); p.add_argument('--years',type=int,default=5); p.add_argument('--cutoff')
    p.add_argument('--primary',type=int,default=21); p.add_argument('--horizons',default='1,5,21,63,126,252'); p.add_argument('--seed',type=int,default=20261002)
    a=p.parse_args()
    end=date.fromisoformat(a.end) if a.end else datetime.now(NY).date()-timedelta(days=1)
    if a.years<1 or a.years>100: p.error('--years must be between 1 and 100.')
    try: default_start=end.replace(year=end.year-a.years)
    except ValueError: default_start=end.replace(year=end.year-a.years,day=28)
    data=prepare(a.account,a.start or default_start.isoformat(),end.isoformat(),a.cutoff or end.isoformat(),a.primary,tuple(int(v) for v in a.horizons.split(',')),a.seed)
    destination=a.output_dir/'study.json'
    if destination.exists():
        raise SystemExit('study.json already exists. Reuse the frozen plan or choose a new study directory.')
    write_json(destination,data)
    print('Frozen study for @'+data['account']['handle']+': '+data['startDate']+' through '+data['endDate'])

if __name__=='__main__': main()
