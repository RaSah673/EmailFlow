#!/usr/bin/env python3
"""Requires running Kind stack + ports.sh. Uses only Python stdlib and kubectl."""
import argparse,json,time,urllib.request,subprocess
from datetime import datetime,timezone,timedelta
p=argparse.ArgumentParser();p.add_argument('--million',action='store_true');p.add_argument('--timeout',type=int,default=1800);args=p.parse_args()
base='http://127.0.0.1:8080/api'
def api(path,body=None):
    data=None if body is None else json.dumps(body).encode();r=urllib.request.Request(base+path,data=data,headers={'Content-Type':'application/json'});return json.load(urllib.request.urlopen(r,timeout=30))
def sql(q):
    return subprocess.check_output(['kubectl','--context','kind-emailflow','-n','emailflow','exec','postgres-0','--','psql','-U','emailflow','-d','emailflow','-At','-c',q],text=True).strip()
def replicas():
    raw=subprocess.check_output(['kubectl','--context','kind-emailflow','-n','emailflow','get','deployment','worker-urgent','-o','json'],text=True)
    return json.loads(raw)['spec']['replicas']
for fault,n in [('none',201),('gateway_after_accept',1000),('worker_after_ack',1000)]+([('none',1000000)] if args.million else []):
    row=api('/campaigns',{'name':f'Integration {fault} {n}','subject':'Test notification','body':'Local test only','quantity':n,'priority':'urgent','scheduled_at':(datetime.now(timezone.utc)+timedelta(seconds=20)).isoformat(),'chunk_size':100,'mirror_limit':2,'fault':fault});cid=row['id'];peak=1;deadline=time.monotonic()+args.timeout
    time.sleep(2);before=next(c for c in api('/campaigns') if c['id']==cid);assert before['accepted']==0,'Accepted before due time'
    while time.monotonic()<deadline:
        c=next(c for c in api('/campaigns') if c['id']==cid);peak=max(peak,replicas());print(f'{cid} {c["completed"]}/{n} desired_peak={peak}',flush=True)
        if c['status']=='completed':break
        time.sleep(3)
    else:raise AssertionError('Campaign timed out')
    assert c['accepted']==c['completed']==c['published']==n,c
    assert sql(f"SELECT count(*) || ':' || count(DISTINCT seq) FROM receipts WHERE campaign_id='{cid}'") == f'{n}:{n}'
    assert sql(f"SELECT count(*) FROM completed_chunks WHERE campaign_id='{cid}'") == str((n+99)//100)
    if fault!='none':
        assert sql(f"SELECT count(*) FROM incidents WHERE campaign_id='{cid}' AND kind='{fault}' AND resolved_at IS NOT NULL")=='1','Crash incident not resolved'
    if n==1000000: assert peak>1,'Autoscaling not observed; inspect KEDA and available resources'
    print(f'PASS {fault}: {n} unique receipts, peak desired replicas {peak}',flush=True)
print('All selected integration scenarios passed.')
