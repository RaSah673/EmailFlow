from contextlib import asynccontextmanager
from uuid import UUID
import hmac
import os
from fastapi import FastAPI, HTTPException, Header, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field
from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor
from .common import claim_fault, crash, incident, init_db, pool, processed, tracer
from .domain import valid_chunk
@asynccontextmanager
async def lifespan(app):
    init_db(); yield; pool.close()
app=FastAPI(title='EmailFlow durable simulation gateway',lifespan=lifespan,docs_url=None,redoc_url=None,openapi_url=None)
FastAPIInstrumentor.instrument_app(app)

@app.middleware('http')
async def limit_delivery_body(request: Request, call_next):
    if request.method == 'POST':
        length = request.headers.get('content-length', '')
        if not length.isascii() or not length.isdecimal() or len(length) > 4 or int(length) > 4096:
            return JSONResponse({'detail': 'Invalid request size'}, status_code=413)
    return await call_next(request)

@app.exception_handler(RequestValidationError)
async def invalid_delivery(request, exc):
    return JSONResponse({'detail': 'Invalid delivery fields'}, status_code=422)

class Chunk(BaseModel):
    campaign_id: UUID
    start: int=Field(ge=1, le=10_000_000)
    end: int=Field(ge=1, le=10_000_000)
@app.get('/health')
def health(): return {'ok':True}
@app.post('/deliver')
def deliver(chunk:Chunk,authorization:str=Header(default='')):
    if not hmac.compare_digest(authorization.encode(), ('Bearer '+os.environ['GATEWAY_TOKEN']).encode()): raise HTTPException(401,'Unauthorized')
    die=False
    with tracer.start_as_current_span('gateway.accept') as span:
        span.set_attribute('campaign.id',str(chunk.campaign_id))
        with pool.connection() as conn:
            row=conn.execute('SELECT quantity,chunk_size,mirror_limit,fault,scheduled_at<=now() AS due,status FROM campaigns WHERE id=%s',(chunk.campaign_id,)).fetchone()
            if not row or not valid_chunk(chunk.start,chunk.end,row['quantity'],row['chunk_size']): raise HTTPException(409,'Invalid chunk')
            if row['status']=='cancelled': raise HTTPException(410,'Campaign cancelled')
            if not row['due'] or row['status']=='scheduled': raise HTTPException(409,'Campaign not dispatching')
            count=conn.execute('WITH inserted AS (INSERT INTO receipts(campaign_id,seq) SELECT %s,seq FROM generate_series(%s::integer,%s::integer) AS seq ON CONFLICT DO NOTHING RETURNING 1) SELECT count(*) AS n FROM inserted',(chunk.campaign_id,chunk.start,chunk.end)).fetchone()['n']
            conn.execute('UPDATE campaigns SET accepted=accepted+%s WHERE id=%s',(count,chunk.campaign_id))
            if chunk.start<=row['mirror_limit']:
                conn.execute('INSERT INTO mirror_jobs(campaign_id,seq) SELECT %s,seq FROM generate_series(%s::integer,%s::integer) AS seq ON CONFLICT DO NOTHING',(chunk.campaign_id,chunk.start,min(chunk.end,row['mirror_limit'])))
            if row['fault']=='gateway_after_accept' and claim_fault(conn,chunk.campaign_id,'gateway_after_accept',chunk.start):
                incident(conn,chunk.campaign_id,'gateway_after_accept','Durable acceptance committed; gateway exits before HTTP response.','error'); die=True
            span.set_attribute('emails.new',count)
            if count: processed.add(count,{'stage':'accepted'})
    if die: crash()
    return {'accepted':chunk.end-chunk.start+1,'new':count,'duplicate':count==0}
