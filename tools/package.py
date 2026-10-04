"""Create a source/example ZIP, excluding runtime data and credentials."""
from __future__ import annotations
import argparse
import hashlib
import json
from pathlib import Path
from zipfile import ZIP_DEFLATED, ZipFile

def main():
    p=argparse.ArgumentParser(description=__doc__); p.add_argument('--output',type=Path,required=True); a=p.parse_args()
    root=Path(__file__).resolve().parents[1]; files=[]
    for path in sorted(root.rglob('*')):
        if not path.is_file(): continue
        relative=path.relative_to(root)
        if any(part in {'__pycache__','.git','.venv','node_modules','studies','private','prices','crypto-prices','archive-source'} for part in relative.parts): continue
        if path.name.startswith('.env') or path.suffix in {'.pyc','.pyo','.sqlite3','.gz','.log','.aux','.out'}: continue
        if relative.parts[0] not in {'skills','tools','docs','examples','README.md','VALIDATION.md','.gitignore'}: continue
        if path.is_symlink(): raise SystemExit('Refusing to package a symbolic-link file.')
        files.append((path,relative))
    required=[root/'README.md',root/'skills/x-account-backtest/SKILL.md',root/'skills/x-account-backtest/agents/openai.yaml']
    if not all(path.is_file() for path in required): raise SystemExit('Required skill entrypoints are missing.')
    destination=a.output.expanduser().resolve(); destination.parent.mkdir(parents=True,exist_ok=True)
    if destination==root or destination.is_relative_to(root): raise SystemExit('Write the ZIP outside the source repository.')
    manifest={'package':'x-account-backtest','containsLiveCorpus':False,'containsCredentials':False,
              'files':{str(relative).replace('\\','/'):hashlib.sha256(path.read_bytes()).hexdigest() for path,relative in files}}
    with ZipFile(destination,'w',ZIP_DEFLATED) as package:
        for path,relative in files: package.write(path,'x-account-backtest/'+relative.as_posix())
        package.writestr('x-account-backtest/package-manifest.json',json.dumps(manifest,indent=2)+'\n')
    with ZipFile(destination) as package:
        if package.testzip() is not None: raise SystemExit('ZIP integrity check failed.')
    print(json.dumps({'files':len(files),'zipBytes':destination.stat().st_size,'sha256':hashlib.sha256(destination.read_bytes()).hexdigest(),'integrityPassed':True}))

if __name__=='__main__': main()
