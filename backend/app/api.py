from contextlib import asynccontextmanager
from datetime import datetime
from uuid import uuid4,UUID
from typing import Literal
import ssl
import httpx
from fastapi import FastAPI,HTTPException,Query,Depends,Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import BaseModel,Field,field_validator
from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor
from confluent_kafka import Consumer,TopicPartition
from .common import BOOTSTRAP, GROUPS, PARTITIONS, TOPICS, init_db, pool
from .domain import require_aware,delivery_key
from .security import router as auth_router,current,operator,admin,audit,limited
from .archive import router as archive_router
@asynccontextmanager
async def lifespan(app):
    init_db(); yield; pool.close()
app=FastAPI(title='EmailFlow API',lifespan=lifespan,docs_url=None,redoc_url=None,openapi_url=None)
app.include_router(auth_router)
app.include_router(archive_router)
@app.middleware('http')
async def security_headers(request:Request,call_next):
    if request.method == 'POST' and request.url.path in ('/api/auth/login','/api/archive/search','/api/campaigns'):
        length = request.headers.get('content-length', '')
        if not length.isascii() or not length.isdecimal() or len(length) > 6 or int(length) > 262144:
            return JSONResponse({'detail':'Invalid request size'},status_code=413)
    response=await call_next(request)
    response.headers['Cache-Control']='no-store'
    response.headers['X-Content-Type-Options']='nosniff'
    return response
@app.exception_handler(RequestValidationError)
async def validation_error(request,exc):
    return JSONResponse(status_code=422,content={'detail':'Invalid request fields','fields':[{'location':list(e['loc']),'type':e['type']} for e in exc.errors()]})
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
    @field_validator('name','subject','body')
    @classmethod
    def valid_text(cls,value):
        if '\x00' in value or not value.strip():
            raise ValueError('Invalid message text')
        return value
@app.get('/api/health')
def health(): return {'ok':True}
@app.post('/api/campaigns',status_code=201)
def create(c:Campaign,u=Depends(operator)):
    if c.fault!='none' and u['role']!='admin': raise HTTPException(403,'Fault simulation requires administrator')
    with pool.connection() as conn:
        blocked=limited(conn,'campaign:'+str(u['id']),10,60)
    if blocked: raise HTTPException(429,'Campaign rate limit reached')
    with pool.connection() as conn:
        conn.execute('SELECT pg_advisory_xact_lock(879322)')
        usage=conn.execute("SELECT count(*) AS active,coalesce(sum(quantity-completed),0) AS remaining FROM campaigns WHERE status NOT IN ('cancelled','completed')").fetchone()
        if usage['active']>=100 or usage['remaining']+c.quantity>50000000: raise HTTPException(429,'Workspace scheduling quota exceeded')
        row=conn.execute('INSERT INTO campaigns(id,name,subject,body,quantity,priority,scheduled_at,chunk_size,mirror_limit,fault) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) RETURNING *',(uuid4(),c.name,c.subject,c.body,c.quantity,c.priority,c.scheduled_at,c.chunk_size,c.mirror_limit,c.fault)).fetchone()
        audit(conn,u['username'],'campaign_create','success',str(row['id'])); return row
@app.get('/api/campaigns')
def list_campaigns(before:UUID|None=None,limit:int=Query(50,ge=1,le=200),
                   priority:Literal['all','urgent','standard']='all',u=Depends(current)):
    priority = None if priority == 'all' else priority
    with pool.connection() as conn:
        return conn.execute('''SELECT id,name,subject,quantity,priority,scheduled_at,status,
            published,accepted,completed,fault,mirror_limit FROM campaigns
            WHERE (%s::text IS NULL OR priority=%s) AND (%s::uuid IS NULL OR
                (created_at,id)<(SELECT created_at,id FROM campaigns WHERE id=%s))
            ORDER BY created_at DESC,id DESC LIMIT %s''',(priority,priority,before,before,limit)).fetchall()

@app.get('/api/campaigns/{cid}')
def get_campaign(cid:UUID,u=Depends(current)):
    with pool.connection() as conn:
        row=conn.execute('SELECT * FROM campaigns WHERE id=%s',(cid,)).fetchone()
    if not row: raise HTTPException(404,'Campaign not found')
    return row
@app.post('/api/campaigns/{cid}/cancel')
def cancel(cid:UUID,u=Depends(operator)):
    with pool.connection() as conn:
        row=conn.execute("UPDATE campaigns SET status='cancelled' WHERE id=%s AND status='scheduled' AND published=0 AND accepted=0 RETURNING *",(cid,)).fetchone()
        if not row: raise HTTPException(409,'Only campaigns not yet dispatched may be cancelled')
        audit(conn,u['username'],'campaign_cancel','success',str(cid)); return row
@app.get('/api/campaigns/{cid}/receipts')
def receipts(cid:UUID,after:int=Query(0,ge=0),limit:int=Query(30,ge=1,le=100),u=Depends(current)):
    with pool.connection() as conn:
        result=conn.execute('SELECT r.seq,r.accepted_at,m.status AS mirror_status FROM receipts r LEFT JOIN mirror_jobs m USING(campaign_id,seq) WHERE r.campaign_id=%s AND r.seq>%s ORDER BY r.seq LIMIT %s',(cid,after,limit)).fetchall()
        for row in result: row['delivery_key']=delivery_key(str(cid),row['seq'])
        return result
@app.get('/api/summary')
def summary(u=Depends(current)):
    with pool.connection() as conn:
        return conn.execute("SELECT count(*) AS campaigns,coalesce(sum(quantity) FILTER(WHERE status!='cancelled'),0) AS total,coalesce(sum(published),0) AS published,coalesce(sum(accepted),0) AS accepted,coalesce(sum(completed),0) AS completed,count(*) FILTER(WHERE status='scheduled') AS scheduled,coalesce(sum(quantity-accepted) FILTER(WHERE priority='urgent' AND status NOT IN ('cancelled','completed') AND scheduled_at<=now()),0) AS urgent_remaining FROM campaigns").fetchone()
@app.get('/api/incidents')
def incidents(u=Depends(admin)):
    with pool.connection() as conn: return conn.execute('SELECT * FROM incidents ORDER BY id DESC LIMIT 100').fetchall()

def kube(path):
    root=Path('/var/run/secrets/kubernetes.io/serviceaccount')
    if not root.exists(): raise RuntimeError('Kubernetes not connected (Compose mode)')
    context=ssl.create_default_context(cafile=str(root/'ca.crt'))
    with httpx.Client(verify=context,timeout=3,trust_env=False) as client:
        r=client.get('https://kubernetes.default.svc'+path,headers={'Authorization':'Bearer '+(root/'token').read_text()});r.raise_for_status();return r
@app.get('/api/infra')
def infra(u=Depends(admin)):
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
def traces(u=Depends(admin)):
    try:
        base=os.getenv('JAEGER_URL','http://jaeger:16686')
        r=httpx.get(base+'/api/traces',params={'service':'emailflow-worker-urgent','limit':12,'lookback':'1h'},timeout=3);r.raise_for_status()
        return [{'trace_id':t['traceID'],'spans':len(t['spans']),'operation':t['spans'][0]['operationName']} for t in r.json()['data'] if t['spans']]
    except Exception: return []
