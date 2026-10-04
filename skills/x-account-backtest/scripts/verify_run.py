"""Independently reconcile measured returns and published primary counts."""
from __future__ import annotations
import argparse
import math
import statistics
from collections import Counter
from decimal import Decimal
from pathlib import Path
from study_utils import publication_day, read_json, sha256, write_json

def reconcile(data_dir, series_dir):
    data_dir, series_dir=Path(data_dir),Path(series_dir)
    payload=read_json(data_dir/'outcomes.json'); summary=read_json(data_dir/'summary.json'); calls=read_json(data_dir/'calls.json')
    if isinstance(calls,dict): calls=calls['calls']
    source={v['id']:v for v in calls}; rows=payload['outcomes']; horizons=payload['metadata']['horizons']
    errors=[]; checks=0
    def check(ok, message):
        nonlocal checks
        checks+=1
        if not ok: errors.append(message)
    expected={(cid,h) for cid in source for h in horizons}
    actual=[(r.get('callId'),r.get('horizon')) for r in rows]
    check(len(actual)==len(set(actual)),'Duplicate call/horizon rows')
    check(set(actual)==expected,'Call/horizon coverage mismatch')
    prices={}
    for file in series_dir.glob('*.json'):
        value=read_json(file); bars=value if isinstance(value,list) else value.get('rows',value.get('bars',[]))
        if isinstance(bars,list) and all(isinstance(v,dict) and 'date' in v and 'open' in v for v in bars):
            prices[file.stem.upper()]={v['date']:Decimal(str(v['open'])) for v in bars}
    sessions=payload['metadata']['sessionDates']; positions={day:i for i,day in enumerate(sessions)}
    for row in rows:
        call=source.get(row.get('callId'))
        check(call is not None,'Unknown call')
        if call is None: continue
        for field in ('direction','symbol','assetClass','kind','publishedAt','evidence','postUrl'):
            check(row.get(field)==call.get(field),'Changed source field: '+field)
        check(row.get('publicationDateET')==publication_day(call['publishedAt']),'Changed publication date')
        if row['status'] not in {'hit','miss','neutral'}: continue
        check(call['issuerMappingValidated'] is True and call['assetClass'] in {'equity','etf'} and call['kind'] in {'explicit','inferred'},'Scored ineligible call')
        try:
            entry=row['entryDate']; exit_day=row['exitDate']; ticker=prices[row['symbol']]; spy=prices['SPY']
            raw=(ticker[exit_day]/ticker[entry]-1)*100; bench=(spy[exit_day]/spy[entry]-1)*100
            sign=Decimal(1 if row['direction']=='bullish' else -1); signed=sign*raw; relative=sign*(raw-bench)
            check(positions[exit_day]-positions[entry]==row['horizon'],'Exit interval mismatch')
            check(entry>row['publicationDateET'],'Entry does not follow publication date')
            check(positions[entry]==next(i for i,d in enumerate(sessions) if d>row['publicationDateET']),'Entry delayed from first required session')
            for field,value in [('rawReturnPct',raw),('spyReturnPct',bench),('directionalReturnPct',signed),('relativeReturnPct',relative)]:
                check(abs(float(value)-row[field])<1e-10,'Return mismatch: '+field)
            for field,value in [('status',signed),('relativeStatus',relative)]:
                check(row[field]==('hit' if value>0 else 'miss' if value<0 else 'neutral'),'Status mismatch: '+field)
        except (KeyError,StopIteration,ArithmeticError,TypeError):
            check(False,'Scored row has incomplete exact-price evidence')
    primary=summary['metadata']['primaryHorizon']; selected=[r for r in rows if r['horizon']==primary]; counts=Counter(r['status'] for r in selected); metric=summary['primary']
    check(metric['calls']==len(selected),'Primary call count mismatch')
    for key in ('hits','misses','neutral','pending','unscored'):
        status={'hits':'hit','misses':'miss'}.get(key,key); check(metric[key]==counts[status],'Primary '+key+' mismatch')
    n=counts['hit']+counts['miss']; check(metric['binaryDenominator']==n,'Binary denominator mismatch')
    check(math.isclose(metric['hitRatePct'],100*counts['hit']/n,abs_tol=1e-10,rel_tol=0) if n else metric['hitRatePct'] is None,'Primary hit rate mismatch')
    measured=[r for r in selected if r['status'] in {'hit','miss','neutral'}]
    relative_counts=Counter(r['relativeStatus'] for r in measured); rel_n=relative_counts['hit']+relative_counts['miss']
    for key,status in [('relativeHits','hit'),('relativeMisses','miss'),('relativeNeutral','neutral')]: check(metric[key]==relative_counts[status],'Primary '+key+' mismatch')
    check(metric['relativeBinaryDenominator']==rel_n,'Relative denominator mismatch')
    check(math.isclose(metric['relativeHitRatePct'],100*relative_counts['hit']/rel_n,abs_tol=1e-10,rel_tol=0) if rel_n else metric['relativeHitRatePct'] is None,'Relative hit rate mismatch')
    for field,column,aggregate in [('meanDirectionalReturnPct','directionalReturnPct',statistics.mean),('medianDirectionalReturnPct','directionalReturnPct',statistics.median),
                                   ('meanRelativeReturnPct','relativeReturnPct',statistics.mean),('medianRelativeReturnPct','relativeReturnPct',statistics.median)]:
        values=[r[column] for r in measured]
        check(math.isclose(metric[field],aggregate(values),abs_tol=1e-10,rel_tol=0) if values else metric[field] is None,'Primary return aggregate mismatch: '+field)
    return {'passed':not errors,'checks':checks,'errors':errors,'method':'Separate Decimal recalculation from exact cached entry/exit bars; full call-horizon coverage and primary counts.',
            'outcomesSHA256':sha256(data_dir/'outcomes.json'),'summarySHA256':sha256(data_dir/'summary.json')}

def main():
    p=argparse.ArgumentParser(description=__doc__); p.add_argument('--data-dir',type=Path,required=True); p.add_argument('--series-dir',type=Path,required=True); a=p.parse_args()
    result=reconcile(a.data_dir,a.series_dir); write_json(a.data_dir/'verification.json',result)
    print(f"{result['checks']} checks; {len(result['errors'])} mismatches.")
    if not result['passed']: raise SystemExit(1)

if __name__=='__main__': main()
