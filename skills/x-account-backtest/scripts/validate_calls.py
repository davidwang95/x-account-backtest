"""Validate exhaustive source attribution, then freeze the reviewed call book."""
from __future__ import annotations
import argparse
import math
import re
from collections import Counter
from pathlib import Path
from study_utils import account_handle, publication_day, read_json, safe_https_url, sha256, write_json

REASONS={'directional','no-view','personal','promotion','factual-commentary','retrospective','conditional','unclear-context'}
KINDS={'explicit','inferred','conditional','exit','unclear'}
ASSETS={'equity','etf','crypto','macro','fx','commodity','rates','international-equity','unresolved'}
TYPES={'buy','add','hold','sell','trim','avoid','short','bullish','bearish'}
THESES={'fundamentals','valuation','momentum','sentiment','macro','catalyst','earnings','technical','positioning','other','unspecified'}

def mapping_proven(call, day):
    reference=call.get('issuerReference') or {}; proof=call.get('issuerMappingEvidence') or {}
    meta=reference.get('metadata') or {}
    stamp=reference.get('asOf') or proof.get('asOfDate')
    url=reference.get('sourceUrl') or proof.get('sourceUrl')
    market=meta.get('market') or proof.get('market'); locale=meta.get('locale') or proof.get('locale')
    kind=meta.get('type') or proof.get('type')
    identity=meta.get('composite_figi') or meta.get('share_class_figi') or (str(meta['cik'])+':'+meta['name'] if meta.get('cik') and meta.get('name') else None) or proof.get('securityId')
    valid=(stamp==day and safe_https_url(url) and market=='stocks' and locale=='us' and kind in {'CS','ETF','ADRC','ADRP','ADRR','ADRW','PFD','REIT','UNIT'} and bool(identity))
    if reference:
        valid=(valid and reference.get('status')=='ok' and meta.get('ticker')==call.get('symbol')
               and bool(re.fullmatch(r'[a-f0-9]{64}',str(reference.get('responseSha256','')))))
        if str(call.get('securityId','')).startswith('FIGI:'):
            valid=valid and call['securityId'][5:] in {meta.get('composite_figi'),meta.get('share_class_figi')}
    else:
        valid=valid and proof.get('securityId')==call.get('securityId') and bool(proof.get('issuerName'))
    return valid

def validate(calls, posts, reviews, study):
    if not isinstance(calls,list) or not isinstance(posts,list) or not isinstance(reviews,list): raise ValueError('Calls, posts and post reviews must be arrays.')
    source={p['id']:p for p in posts}
    if len(source)!=len(posts): raise ValueError('Duplicate source post ID.')
    account=study['account']; handle=account_handle(account['handle'] if isinstance(account,dict) else account)
    owners=set()
    for post in posts:
        day=publication_day(post['publishedAt'])
        if not study['startDate']<=day<=study['endDate']: raise ValueError('Source post outside frozen window.')
        if post.get('account','').lower()!=handle: raise ValueError('Source account mismatch.')
        if not isinstance(post.get('id'),str) or not re.fullmatch(r'[0-9]{1,25}',post['id']): raise ValueError('Source post ID must be a numeric string.')
        if not isinstance(post.get('authorId'),str) or not post['authorId'].isdigit(): raise ValueError('Source authorId must be a verified numeric string.')
        owners.add(post['authorId'])
        if isinstance(account,dict) and account.get('id') and post['authorId']!=account['id']: raise ValueError('Source author ID differs from frozen account identity.')
        if post.get('isNativeRetweet') or post.get('isRetweet'): raise ValueError('Native retweets are outside the authored-text corpus.')
        if post.get('url')!='https://x.com/'+handle+'/status/'+post['id']: raise ValueError('Source URL does not match account and post ID.')
        if not isinstance(post.get('text'),str) or not post['text']: raise ValueError('Missing source text.')
    if len(owners)>1: raise ValueError('Source posts contain more than one author identity.')
    covered={r.get('postId'):r for r in reviews}
    if len(covered)!=len(reviews) or set(covered)!=set(source): raise ValueError('Post reviews must cover every source ID exactly once, including no-call posts.')
    ids=set(); by_post=Counter()
    for call in calls:
        cid=call.get('id'); pid=call.get('postId'); post=source.get(pid)
        if not isinstance(cid,str) or not cid or cid in ids: raise ValueError('Every call needs a unique ID.')
        ids.add(cid)
        if post is None: raise ValueError('Call references an unknown post.')
        if call.get('publishedAt')!=post['publishedAt'] or call.get('postUrl')!=post['url']: raise ValueError('Call timestamp or source link differs from the archive.')
        evidence=call.get('evidence')
        if not isinstance(evidence,str) or not evidence.strip() or evidence not in post['text']: raise ValueError('Call evidence must be an exact nonempty substring of authored source text.')
        if call.get('postText')!=post['text']: raise ValueError('postText must preserve the full authored post.')
        if call.get('direction') not in {'bullish','bearish'} or call.get('kind') not in KINDS or call.get('assetClass') not in ASSETS: raise ValueError('Invalid call direction, kind or asset class.')
        if call.get('callType') not in TYPES or call.get('thesis') not in THESES: raise ValueError('Invalid call type or thesis category.')
        value=call.get('confidence')
        if isinstance(value,bool) or not isinstance(value,(int,float)) or not math.isfinite(value) or not 0<=value<=1: raise ValueError('Interpretation confidence must be a finite number in [0,1].')
        if not isinstance(call.get('reviewReason'),str): raise ValueError('Every call needs a reviewReason, empty when unambiguous.')
        day=publication_day(post['publishedAt'])
        if call.get('issuerMappingValidated') is True and (call['assetClass'] not in {'equity','etf'} or not mapping_proven(call,day)): raise ValueError('Validated issuer mapping requires dated US security evidence.')
        if call.get('reviewed') and not call.get('adjudicationReason'): raise ValueError('Review overrides need a recorded source-only adjudication reason.')
        chain=call.get('sameIssuerChain') or []
        if len(chain)>1 and not call.get('sameSecurityProof'): raise ValueError('Ticker stitching requires dated same-security proof, not an acquisition or company-name match.')
        by_post[pid]+=1
    for pid, review in covered.items():
        if review.get('reason') not in REASONS: raise ValueError('Invalid post classification reason.')
        if type(review.get('callCount')) is not int or review['callCount']!=by_post[pid]: raise ValueError('Post callCount must reconcile to the call book.')
    return {'version':'1.0','sourcePosts':len(posts),'classifiedPosts':len(reviews),'missingPosts':0,'calls':len(calls),
            'reasonCounts':dict(Counter(r['reason'] for r in reviews)), 'kindCounts':dict(Counter(c['kind'] for c in calls)),
            'assetClassCounts':dict(Counter(c['assetClass'] for c in calls)),
            'method':'Every recovered authored post reviewed using source-only batches; no keyword exclusion. Interpretation confidence is uncalibrated.'}

def main():
    p=argparse.ArgumentParser(description=__doc__)
    for name in ('calls','posts','reviews','study'): p.add_argument('--'+name,type=Path,required=True)
    p.add_argument('--output-dir',type=Path,required=True); a=p.parse_args()
    calls=read_json(a.calls); posts=read_json(a.posts); reviews=read_json(a.reviews)
    if isinstance(calls,dict): calls=calls['calls']
    if isinstance(posts,dict): posts=posts['posts']
    if isinstance(reviews,dict): reviews=reviews['posts']
    receipt=validate(calls,posts,reviews,read_json(a.study))
    receipt.update(callsHashSHA256=sha256(a.calls),sourceHashSHA256=sha256(a.posts),reviewsHashSHA256=sha256(a.reviews),studyHashSHA256=sha256(a.study))
    write_json(a.output_dir/'classification-manifest.json',receipt)
    print(f"Validated {receipt['classifiedPosts']} posts and {receipt['calls']} source-linked views.")

if __name__=='__main__': main()
