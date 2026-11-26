from contextlib import asynccontextmanager
from datetime import datetime,timezone
from uuid import uuid4,UUID
from typing import Literal
import ssl
import httpx
from fastapi import FastAPI,HTTPException,Query
from pydantic import BaseModel,Field,field_validator
from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor
from confluent_kafka import Consumer,TopicPartition
from .common import *
from .domain import require_aware,delivery_key
@asynccontextmanager
async def lifespan(app):
    init_db(); yield; pool.close()
app=FastAPI(title='EmailFlow API',lifespan=lifespan)
FastAPIInstrumentor.instrument_app(app)
class Campaign(BaseModel):
    name:str=Field(min_length=1,max_length=120)
    subject:str=Field(min_length=1,max_length=200,pattern=r'^[^\r\n]+$')
    body:str=Field(min_length=1,max_length=20000)
    quantity:int=Field(ge=1,le=10000000)
    priority:Literal['standard','urgent']='standard'
    scheduled_at:datetime
    chunk_size:int=Field(default=100,ge=1,le=1000)
    mirror_limit:int=Field(default=5,ge=0,le=100)
    fault:Literal['none','gateway_after_accept','worker_after_ack']='none'
    @field_validator('scheduled_at')
    @classmethod
    def aware(cls,v): return require_aware(v)
@app.get('/api/health')
def health(): return {'ok':True}
@app.post('/api/campaigns',status_code=201)
def create(c:Campaign):
    with pool.connection() as conn:
        return conn.execute('INSERT INTO campaigns(id,name,subject,body,quantity,priority,scheduled_at,chunk_size,mirror_limit,fault) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) RETURNING *',(uuid4(),c.name,c.subject,c.body,c.quantity,c.priority,c.scheduled_at,c.chunk_size,c.mirror_limit,c.fault)).fetchone()
@app.get('/api/campaigns')
def list_campaigns():
    with pool.connection() as conn:
        return conn.execute('SELECT * FROM campaigns ORDER BY created_at DESC LIMIT 200').fetchall()
@app.post('/api/campaigns/{cid}/cancel')
def cancel(cid:UUID):
    with pool.connection() as conn:
        row=conn.execute("UPDATE campaigns SET status='cancelled' WHERE id=%s AND status='scheduled' RETURNING *",(cid,)).fetchone()
        if not row: raise HTTPException(409,'Only campaigns not yet dispatched may be cancelled')
        return row
@app.get('/api/campaigns/{cid}/receipts')
def receipts(cid:UUID,after:int=Query(0,ge=0),limit:int=Query(30,ge=1,le=100)):
    with pool.connection() as conn:
        result=conn.execute('SELECT r.seq,r.accepted_at,m.status AS mirror_status FROM receipts r LEFT JOIN mirror_jobs m USING(campaign_id,seq) WHERE r.campaign_id=%s AND r.seq>%s ORDER BY r.seq LIMIT %s',(cid,after,limit)).fetchall()
        for row in result: row['delivery_key']=delivery_key(str(cid),row['seq'])
        return result
@app.get('/api/summary')
def summary():
    with pool.connection() as conn:
        return conn.execute("SELECT count(*) AS campaigns,coalesce(sum(quantity) FILTER(WHERE status!='cancelled'),0) AS total,coalesce(sum(published),0) AS published,coalesce(sum(accepted),0) AS accepted,coalesce(sum(completed),0) AS completed,count(*) FILTER(WHERE status='scheduled') AS scheduled,coalesce(sum(quantity-accepted) FILTER(WHERE priority='urgent' AND status NOT IN ('cancelled','completed') AND scheduled_at<=now()),0) AS urgent_remaining FROM campaigns").fetchone()
@app.get('/api/incidents')
def incidents():
    with pool.connection() as conn: return conn.execute('SELECT * FROM incidents ORDER BY id DESC LIMIT 100').fetchall()

def kube(path):
    root=Path('/var/run/secrets/kubernetes.io/serviceaccount')
    if not root.exists(): raise RuntimeError('Kubernetes not connected (Compose mode)')
    context=ssl.create_default_context(cafile=str(root/'ca.crt'))
    with httpx.Client(verify=context,timeout=3,trust_env=False) as client:
        r=client.get('https://kubernetes.default.svc'+path,headers={'Authorization':'Bearer '+(root/'token').read_text()});r.raise_for_status();return r
@app.get('/api/infra')
def infra():
    result={'workers':[], 'kubernetes_error':None,'kafka_error':None,'logs':None}
    ns=os.getenv('POD_NAMESPACE','emailflow')
    try:
        items=kube(f'/apis/apps/v1/namespaces/{ns}/deployments').json()['items']
        for d in items:
            name=d['metadata']['name']
            if name in ('worker-standard','worker-urgent','gateway','scheduler','api'):
                result['workers'].append({'name':name,'desired':d['spec'].get('replicas',1),'ready':d.get('status',{}).get('readyReplicas',0)})
    except Exception as e: result['kubernetes_error']=str(e)
    try:
        pods=kube(f'/api/v1/namespaces/{ns}/pods?labelSelector=app%3Dotel').json()['items']
        if pods:
            name=pods[0]['metadata']['name']; result['logs']=kube(f'/api/v1/namespaces/{ns}/pods/{name}/log?tailLines=80').text
    except Exception: pass
    for p in TOPICS:
        consumer=Consumer({'bootstrap.servers':BOOTSTRAP,'group.id':GROUPS[p],'enable.auto.commit':False,'socket.timeout.ms':3000})
        try:
            partitions=[TopicPartition(TOPICS[p],i) for i in range(PARTITIONS[p])]
            offsets=consumer.committed(partitions,timeout=3)
            lag=0
            for o in offsets:
                low,high=consumer.get_watermark_offsets(o,timeout=1,cached=False)
                lag+=max(0,high-max(low,o.offset))
            result['workers'].append({'name':'Kafka '+p,'lag':lag,'partitions':len(partitions)})
        except Exception as e: result['kafka_error']=str(e)
        finally: consumer.close()
    return result
@app.get('/api/traces')
def traces():
    try:
        base=os.getenv('JAEGER_URL','http://jaeger:16686')
        r=httpx.get(base+'/api/traces',params={'service':'emailflow-worker-urgent','limit':12,'lookback':'1h'},timeout=3);r.raise_for_status()
        return [{'trace_id':t['traceID'],'spans':len(t['spans']),'operation':t['spans'][0]['operationName']} for t in r.json()['data'] if t['spans']]
    except Exception: return []
