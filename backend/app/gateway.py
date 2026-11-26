from contextlib import asynccontextmanager
from uuid import UUID
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor
from .common import *
from .domain import valid_chunk
@asynccontextmanager
async def lifespan(app):
    init_db(); yield; pool.close()
app=FastAPI(title='EmailFlow durable simulation gateway',lifespan=lifespan)
FastAPIInstrumentor.instrument_app(app)
class Chunk(BaseModel):
    campaign_id: UUID
    start: int
    end: int
@app.get('/health')
def health(): return {'ok':True}
@app.post('/deliver')
def deliver(chunk:Chunk):
    die=False
    with tracer.start_as_current_span('gateway.accept') as span:
        span.set_attribute('campaign.id',str(chunk.campaign_id))
        with pool.connection() as conn:
            row=conn.execute('SELECT quantity,chunk_size,mirror_limit,fault,scheduled_at<=now() AS due,status FROM campaigns WHERE id=%s',(chunk.campaign_id,)).fetchone()
            if not row or not row['due'] or row['status']=='cancelled' or not valid_chunk(chunk.start,chunk.end,row['quantity'],row['chunk_size']): raise HTTPException(409,'Invalid or not-due chunk')
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
