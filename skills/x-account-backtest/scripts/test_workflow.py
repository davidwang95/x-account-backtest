"""Synthetic cross-module contract checks, including the combined call ledger."""
from __future__ import annotations
import copy
import csv
from pathlib import Path
import tempfile
import unittest
from prepare_study import prepare
from run_backtest import run
from study_utils import read_json, sha256, write_json
from validate_calls import validate

def fixture():
    study=prepare('@fixtureomega','2024-01-02','2024-01-02','2024-01-08',1,(1,2))
    study['account']['id']='999000'; study['cryptoPrimary']=1; study['cryptoHorizons']=[1,2]; study['bootstrapIterations']=20
    posts=[]; calls=[]; reviews=[]
    for i,(symbol,asset,text) in enumerate([('ABC','equity','SYNTHETIC: buy ABC.'),('BTC','crypto','SYNTHETIC: bullish Bitcoin.')],1):
        pid=str(9000+i); post={'id':pid,'account':'fixtureomega','authorId':'999000','publishedAt':'2024-01-02T15:00:00Z','text':text,'url':'https://x.com/fixtureomega/status/'+pid,'isReply':False,'isQuote':False}
        call={'id':pid+':0','postId':pid,'publishedAt':post['publishedAt'],'postUrl':post['url'],'postText':text,'evidence':text,
              'instrument':'SYNTHETIC '+symbol,'symbol':symbol,'assetClass':asset,'direction':'bullish','kind':'explicit' if asset=='equity' else 'inferred',
              'callType':'buy' if asset=='equity' else 'bullish','thesis':'valuation','confidence':.9,'reviewReason':'','issuerMappingValidated':asset=='equity'}
        if asset=='equity':
            call['securityId']='synthetic:abc'
            call['issuerMappingEvidence']={'asOfDate':'2024-01-02','sourceUrl':'https://example.invalid/synthetic/abc','securityId':'synthetic:abc',
                'issuerName':'SYNTHETIC ABC','market':'stocks','locale':'us','type':'CS'}
        posts.append(post); calls.append(call); reviews.append({'postId':pid,'reason':'directional','callCount':1})
    return study,posts,calls,reviews

class WorkflowTests(unittest.TestCase):
    def test_crypto_ledger_uses_its_scored_calendar_result_and_hashes(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp); prices=root/'prices'; crypto=root/'crypto-prices'; prices.mkdir(); crypto.mkdir()
            study,posts,calls,reviews=fixture()
            for name,data in [('study',study),('posts',posts),('calls',calls),('post-reviews',reviews)]: write_json(root/(name+'.json'),data)
            receipt=validate(calls,posts,reviews,study)
            for field,name in [('callsHashSHA256','calls.json'),('sourceHashSHA256','posts.json'),('reviewsHashSHA256','post-reviews.json'),('studyHashSHA256','study.json')]: receipt[field]=sha256(root/name)
            write_json(root/'classification-manifest.json',receipt)
            days=['2024-01-02','2024-01-03','2024-01-04','2024-01-05','2024-01-08']
            write_json(prices/'SPY.json',[{'date':d,'open':100+i} for i,d in enumerate(days)])
            write_json(prices/'ABC.json',[{'date':d,'open':v} for d,v in zip(days,[100,100,120,110,115])])
            write_json(crypto/'BTC.json',[{'date':d,'open':v} for d,v in [('2024-01-04',100),('2024-01-05',110),('2024-01-06',90)]])
            calendar=root/'calendar.json'; write_json(calendar,{'sessions':days,'source':'SYNTHETIC independently specified fixture calendar'})
            run(root,prices,crypto,calendar)
            with (root/'all-calls.csv').open(encoding='utf-8-sig',newline='') as stream: ledger=list(csv.DictReader(stream))
            row=next(v for v in ledger if v['assetClass']=='crypto')
            self.assertEqual((row['status'],row['horizonUnit'],row['measurementHorizon']),('hit','calendar_days','1'))
            self.assertAlmostEqual(float(row['directionalReturnPct']),10)
            self.assertEqual(row['relativeStatus'],'not_applicable')
            manifest=read_json(root/'run-manifest.json'); self.assertEqual(manifest['cryptoCacheHashes']['BTC.json'],sha256(crypto/'BTC.json'))
            self.assertTrue(read_json(root/'crypto-verification.json')['passed'])
            with self.assertRaisesRegex(ValueError,'Existing crypto outputs'): run(root,prices,None,calendar)

    def test_mapping_cannot_be_marked_verified_for_another_ticker(self):
        study,posts,calls,reviews=fixture(); call=calls[0]; call.pop('issuerMappingEvidence')
        call['issuerReference']={'asOf':'2024-01-02','sourceUrl':'https://example.invalid/synthetic/ref','status':'ok','responseSha256':'a'*64,
                                'metadata':{'ticker':'WRONG','market':'stocks','locale':'us','type':'CS','composite_figi':'SYNTHETIC'}}
        with self.assertRaisesRegex(ValueError,'dated US security evidence'): validate(calls,posts,reviews,study)

    def test_source_author_must_match_frozen_account(self):
        study,posts,calls,reviews=fixture(); posts[0]['authorId']='111111'
        with self.assertRaisesRegex(ValueError,'author ID'): validate(calls,posts,reviews,study)

    def test_review_override_needs_a_recorded_adjudication(self):
        study,posts,calls,reviews=fixture(); calls[0]['reviewed']=True
        with self.assertRaisesRegex(ValueError,'adjudication'): validate(calls,posts,reviews,study)

    def test_mapping_source_url_cannot_contain_a_secret(self):
        study,posts,calls,reviews=fixture(); calls[0]['issuerMappingEvidence']['sourceUrl']='https://example.invalid/ref?apiKey=fixture'
        with self.assertRaisesRegex(ValueError,'dated US security evidence'): validate(calls,posts,reviews,study)

if __name__=='__main__': unittest.main()
