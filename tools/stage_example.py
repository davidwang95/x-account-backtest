"""Stage an already validated report without its post or price corpus."""
from __future__ import annotations
import argparse
from pathlib import Path
import re
import shutil
import fitz

def main():
    p=argparse.ArgumentParser(description=__doc__); p.add_argument('--study-dir',type=Path,required=True); p.add_argument('--output-dir',type=Path,required=True)
    p.add_argument('--name',default='example'); a=p.parse_args()
    if not re.fullmatch(r'[a-z0-9-]+',a.name): raise SystemExit('Example name must be a lowercase slug.')
    repo=Path(__file__).resolve().parents[1]; source=a.study_dir/'report'; destination=a.output_dir/'report'
    if not (source/'x-account-backtest.pdf').is_file(): raise SystemExit('Validated source PDF is missing.')
    destination.mkdir(parents=True,exist_ok=True)
    for name in ('x-account-backtest.pdf','x-account-backtest.tex','report-build-manifest.json'):
        if (source/name).is_file(): shutil.copy2(source/name,destination/name)
    figures=a.output_dir/'figures'; figures.mkdir(exist_ok=True)
    for path in (a.study_dir/'figures').glob('*'):
        if path.is_file() and (path.suffix in {'.pdf','.png','.csv'} or path.name=='chart-manifest.json'): shutil.copy2(path,figures/path.name)
    examples=repo/'examples'; examples.mkdir(exist_ok=True)
    shutil.copy2(source/'x-account-backtest.pdf',examples/(a.name+'-backtest-example.pdf'))
    docs=repo/'docs'; docs.mkdir(exist_ok=True)
    with fitz.open(source/'x-account-backtest.pdf') as pdf:
        pdf[0].get_pixmap(matrix=fitz.Matrix(1.6,1.6),alpha=False).save(docs/'report-cover.png')
    print('Staged report, editable source, charts and package preview. Source corpus and price caches were not copied.')

if __name__=='__main__': main()
