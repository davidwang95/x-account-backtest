"""Check the local package's Python syntax and relative documentation links."""
from __future__ import annotations
import ast
from pathlib import Path
import re

def main():
    root=Path(__file__).resolve().parents[1]; failures=[]; scripts=0; links=0
    for path in root.rglob('*.py'):
        if '__pycache__' in path.parts: continue
        scripts+=1
        try: ast.parse(path.read_text(encoding='utf-8'),filename=str(path))
        except SyntaxError: failures.append('Invalid Python: '+str(path.relative_to(root)))
    for path in root.rglob('*.md'):
        for raw in re.findall(r'\[[^\]]*\]\(([^)]+)\)',path.read_text(encoding='utf-8')):
            target=raw.strip().strip('<>').split('#',1)[0]
            if not target or re.match(r'^[a-zA-Z][a-zA-Z0-9+.-]*:',target): continue
            links+=1
            if not (path.parent/target).is_file(): failures.append('Missing relative link in '+str(path.relative_to(root))+': '+target)
    if failures: raise SystemExit('\n'.join(failures))
    print(f'{scripts} Python sources parsed; {links} relative links resolve.')

if __name__=='__main__': main()
