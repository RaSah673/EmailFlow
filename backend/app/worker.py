import json,os,time,signal
import httpx
from confluent_kafka import Consumer, KafkaException
from opentelemetry.propagate import extract
from .common import *

running=True
def stop(*args):
    global running; running=False

def run():
    init_db(); ensure_topics(); priority=os.getenv('PRIORITY','standard')
    signal.signal(signal.SIGTERM,stop); signal.signal(signal.SIGINT,stop)
    consumer=Consumer({'bootstrap.servers':BOOTSTRAP,'group.id':GROUPS[priority],'auto.offset.reset':'earliest','enable.auto.commit':False,'enable.auto.offset.store':False,'max.poll.interval.ms':300000})
    consumer.subscribe([TOPICS[priority]])
    gateway=os.getenv('GATEWAY_URL','http://gateway:8000')
    with httpx.Client(timeout=10) as client:
        try:
            while running:
                msg=consumer.poll(1)
                if msg is None: continue
                if msg.error(): log.warning('Kafka: %s',msg.error()); continue
                chunk=json.loads(msg.value()); cid=chunk['campaign_id']; delay=.5; started=time.monotonic()
                time.sleep(float(os.getenv('SIMULATED_CHUNK_LATENCY_MS','30'))/1000)
                while running:
                    die=False
                    try:
                        carrier={k:v.decode() for k,v in (msg.headers() or []) if v is not None}
                        with tracer.start_as_current_span('worker.deliver',context=extract(carrier)) as span:
                            span.set_attribute('campaign.id',cid); span.set_attribute('email.priority',priority)
                            r=client.post(gateway+'/deliver',json=chunk); r.raise_for_status(); receipt=r.json()
                            if receipt['accepted']!=chunk['end']-chunk['start']+1: raise RuntimeError('Incomplete acceptance')
                            with pool.connection() as conn:
                                row=conn.execute('SELECT fault FROM campaigns WHERE id=%s',(cid,)).fetchone()
                                if row['fault']=='worker_after_ack' and claim_fault(conn,cid,'worker_after_ack',chunk['start']):
                                    incident(conn,cid,'worker_after_ack','Worker received acceptance, exits before completion and Kafka offset commit.','error'); die=True
                                else:
                                    fresh=conn.execute('INSERT INTO completed_chunks(campaign_id,start_seq,end_seq) VALUES (%s,%s,%s) ON CONFLICT DO NOTHING RETURNING 1',(cid,chunk['start'],chunk['end'])).fetchone()
                                    if fresh:
                                        n=chunk['end']-chunk['start']+1
                                        conn.execute("UPDATE campaigns SET completed=completed+%s,status=CASE WHEN completed+%s=quantity AND published=quantity THEN 'completed' ELSE status END WHERE id=%s",(n,n,cid)); processed.add(n,{'stage':'completed','priority':priority})
                                    if receipt['duplicate']:
                                        resolve(conn,cid,'gateway_after_accept',chunk['start']); resolve(conn,cid,'worker_after_ack',chunk['start'])
                        if die: crash()
                        offsets=consumer.commit(message=msg,asynchronous=False)
                        if offsets and any(o.error for o in offsets): raise RuntimeError(f'Offset commit failed: {offsets}')
                        break
                    except Exception as e:
                        failures.add(1,{'kind':'worker_retry'}); log.warning('Retry campaign=%s: %s',cid,e)
                        # Retain current message; never commit past an unaccepted task.
                        if time.monotonic()-started>45:
                            raise RuntimeError("Retry window exhausted; restart to rejoin consumer group") from e
                        time.sleep(delay); delay=min(delay*2,10)
        finally: consumer.close()
if __name__=='__main__': run()
