"""Copy this local skill into Codex without overwriting an existing install."""
from __future__ import annotations
import argparse
import os
from pathlib import Path
import shutil

def main():
    p=argparse.ArgumentParser(description=__doc__)
    codex_root=Path(os.environ.get('CODEX_HOME',str(Path.home()/'.codex')))
    p.add_argument('--destination',type=Path,default=codex_root/'skills'/'x-account-backtest')
    a=p.parse_args(); source=Path(__file__).resolve().parents[1]/'skills'/'x-account-backtest'; target=a.destination.expanduser().resolve()
    if not (source/'SKILL.md').is_file(): raise SystemExit('Installable skill source is missing.')
    if target.exists(): raise SystemExit('Destination already exists; preserve it and review the existing install before replacing it.')
    target.parent.mkdir(parents=True,exist_ok=True)
    shutil.copytree(source,target,ignore=shutil.ignore_patterns('__pycache__','*.pyc','*.pyo','.env','.env.*'))
    print('Installed: '+str(target))
    print('Start a new Codex task and run $x-account-backtest @handle last 5 years.')

if __name__=='__main__': main()
