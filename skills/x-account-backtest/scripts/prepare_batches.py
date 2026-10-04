"""Produce source-only classification batches without any outcome prices."""
from __future__ import annotations
import argparse
from collections import defaultdict
from pathlib import Path
from study_utils import publication_day, read_json, sha256, write_json

def make_batches(posts, study, max_posts=12):
    if type(max_posts) is not int or max_posts<1: raise ValueError('Batch size must be positive.')
    groups=defaultdict(list); seen=set()
    for post in posts:
        if post['id'] in seen: raise ValueError('Duplicate post ID.')
        seen.add(post['id']); day=publication_day(post['publishedAt'])
        if not study['startDate']<=day<=study['endDate']: raise ValueError('Post outside publication window.')
        if not isinstance(post.get('text'),str) or not post['text']: raise ValueError('Missing authored source text.')
        groups[day].append({key:post[key] for key in ('id','publishedAt','text','url')})
    batches=[]
    for day, rows in sorted(groups.items()):
        rows.sort(key=lambda v:(v['publishedAt'],v['id']))
        for offset in range(0,len(rows),max_posts):
            batches.append({'batchId':f'{day}-{offset//max_posts:03d}','publicationDateET':day,'posts':rows[offset:offset+max_posts]})
    return batches

def main():
    p=argparse.ArgumentParser(description=__doc__); p.add_argument('--posts',type=Path,required=True); p.add_argument('--study',type=Path,required=True)
    p.add_argument('--output-dir',type=Path,required=True); p.add_argument('--max-posts',type=int,default=12); a=p.parse_args()
    data=read_json(a.posts); posts=data if isinstance(data,list) else data['posts']; batches=make_batches(posts,read_json(a.study),a.max_posts)
    if a.output_dir.exists() and any(a.output_dir.glob('*.json')): raise SystemExit('Batch directory already contains JSON; use the existing frozen batches or a new directory.')
    a.output_dir.mkdir(parents=True,exist_ok=True)
    for batch in batches: write_json(a.output_dir/(batch['batchId']+'.json'),batch)
    write_json(a.output_dir/'batch-manifest.json',{'sourcePosts':len(posts),'batchCount':len(batches),'sourceSHA256':sha256(a.posts),'batches':[b['batchId'] for b in batches],'containsPrices':False})
    print(f'{len(posts)} source posts in {len(batches)} source-only batches.')

if __name__=='__main__': main()
