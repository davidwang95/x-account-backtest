"""Run deterministic scoring on a frozen, fully source-validated call book."""
from __future__ import annotations
import argparse
import csv
import importlib.metadata
import platform
from pathlib import Path
from scoring import load_series_directory, score_calls, write_outcomes
from statistical_summary import build_summary, write_summary
from crypto_scoring import score_crypto, write_crypto
from study_utils import read_json, safe_csv_cell, sha256, write_json
from verify_run import reconcile

def run(data_dir, series_dir, crypto_dir=None, calendar_file=None):
    data_dir,series_dir=Path(data_dir),Path(series_dir)
    study=read_json(data_dir/'study.json'); receipt=read_json(data_dir/'classification-manifest.json')
    if not crypto_dir and (data_dir/'crypto-outcomes.json').exists():
        raise ValueError('Existing crypto outputs require --crypto-dir for reconciliation, or a new study directory.')
    for field,name in [('callsHashSHA256','calls.json'),('sourceHashSHA256','posts.json'),('reviewsHashSHA256','post-reviews.json'),('studyHashSHA256','study.json')]:
        if receipt.get(field)!=sha256(data_dir/name): raise ValueError('Frozen source/classification changed: '+name+'. Revalidate source-only before scoring.')
    calls=read_json(data_dir/'calls.json')
    if isinstance(calls,dict): calls=calls['calls']
    calendar=read_json(calendar_file) if calendar_file else None
    sessions=calendar if isinstance(calendar,list) else calendar.get('sessions') if calendar else None
    calendar_source=calendar.get('source') if isinstance(calendar,dict) else None
    result=score_calls(calls,load_series_directory(series_dir),cutoff=study['cutoff'],horizons=study['horizons'],expected_sessions=sessions,calendar_source=calendar_source)
    write_outcomes(result,data_dir)
    summary=build_summary(result,primary=study['primary'],iterations=study.get('bootstrapIterations',2000),seed=study['seed'],episode_spacing=study['primary'])
    write_summary(summary,data_dir)
    crypto_index={}
    if crypto_dir:
        crypto=score_crypto(calls,load_series_directory(crypto_dir),cutoff=study['cutoff'],horizons=study['cryptoHorizons'],primary=study['cryptoPrimary'])
        write_crypto(crypto,data_dir)
        from verify_crypto_run import reconcile_crypto
        crypto_verification=reconcile_crypto(data_dir,crypto_dir)
        write_json(data_dir/'crypto-verification.json',crypto_verification)
        if not crypto_verification['passed']: raise ValueError('Independent crypto reconciliation failed. Do not publish this study.')
        crypto_index={r['callId']:r for r in crypto['outcomes'] if r['horizonCalendarDays']==study['cryptoPrimary']}
    outcome_index={(r['callId'],r['horizon']):r for r in result['outcomes']}
    fields=['callId','publishedAt','symbol','instrument','assetClass','direction','kind','callType','thesis','measurementHorizon','horizonUnit','status','reason','entryDate','exitDate','directionalReturnPct','relativeStatus','relativeReturnPct','postUrl']
    with (data_dir/'all-calls.csv').open('w',encoding='utf-8-sig',newline='') as stream:
        writer=csv.DictWriter(stream,fieldnames=fields); writer.writeheader()
        for call in calls:
            row={**outcome_index[(call['id'],study['primary'])],'measurementHorizon':study['primary'],'horizonUnit':'trading_sessions'}
            if call['id'] in crypto_index:
                row={**crypto_index[call['id']],'measurementHorizon':study['cryptoPrimary'],'horizonUnit':'calendar_days',
                     'relativeStatus':'not_applicable','relativeReturnPct':None}
            writer.writerow({key:safe_csv_cell(row.get(key)) for key in fields})
    verification=reconcile(data_dir,series_dir); write_json(data_dir/'verification.json',verification)
    if not verification['passed']: raise ValueError('Independent return reconciliation failed. Do not publish this study.')
    artifacts=['study.json','posts.json','post-reviews.json','calls.json','classification-manifest.json','outcomes.json','summary.json','all-calls.csv','verification.json']
    artifacts += [name for name in ('archive-coverage.json','price-manifest.json','crypto-outcomes.json','crypto-price-manifest.json','crypto-verification.json') if (data_dir/name).exists()]
    versions={'python':platform.python_version()}
    for name in ('numpy','exchange-calendars'):
        try: versions[name]=importlib.metadata.version(name)
        except importlib.metadata.PackageNotFoundError: pass
    manifest={'schemaVersion':'1.0','account':study['account'],'publicationWindow':[study['startDate'],study['endDate']],
               'cutoff':study['cutoff'],'primary':study['primary'],'horizons':study['horizons'],'seed':study['seed'],'toolVersions':versions,
               'dataHashes':{name:sha256(data_dir/name) for name in artifacts},'priceCacheHashes':{file.name:sha256(file) for file in sorted(series_dir.glob('*.json'))},
               'verificationPassed':True,'classificationUsesCurrentAgent':True,'separateAIProviderRequired':False}
    if crypto_dir:
        manifest['cryptoCacheHashes']={file.name:sha256(file) for file in sorted(Path(crypto_dir).glob('*.json'))}
    write_json(data_dir/'run-manifest.json',manifest)
    return summary

def main():
    p=argparse.ArgumentParser(description=__doc__); p.add_argument('--data-dir',type=Path,required=True); p.add_argument('--series-dir',type=Path,required=True)
    p.add_argument('--crypto-dir',type=Path); p.add_argument('--calendar-file',type=Path); a=p.parse_args()
    result=run(a.data_dir,a.series_dir,a.crypto_dir,a.calendar_file)['primary']
    print(f"{result['scored']} scored calls; directional accuracy {result['hitRatePct']}; relative accuracy {result['relativeHitRatePct']}.")

if __name__=='__main__': main()
