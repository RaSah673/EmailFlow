import json
import os
import signal
import time

import httpx
from confluent_kafka import Consumer
from opentelemetry.propagate import extract

from .common import (
    BOOTSTRAP, TOPICS, WORKER_GROUP, claim_fault, crash, ensure_topics, failures,
    incident, init_db, log, pool, processed, resolve, tracer,
)

running=True
def stop(*args):
    global running; running=False


def queued_tasks(consumer):
    while running:
        first = consumer.poll(1)
        if first is None:
            continue
        # Bound the batch so retries do not exceed Kafka's poll interval.
        batch = [first, *consumer.consume(num_messages=3, timeout=0)]
        # Stable sorting keeps offsets in order within each partition.
        yield from sorted(batch, key=lambda message: message.topic() != TOPICS['urgent'])


def run():
    init_db(); ensure_topics()
    signal.signal(signal.SIGTERM,stop); signal.signal(signal.SIGINT,stop)
    consumer=Consumer({'bootstrap.servers':BOOTSTRAP,'group.id':WORKER_GROUP,'auto.offset.reset':'earliest','enable.auto.commit':False,'enable.auto.offset.store':False,'max.poll.interval.ms':300000})
    consumer.subscribe(list(TOPICS.values()))
    gateway=os.getenv('GATEWAY_URL','http://gateway:8000')
    headers = {'Authorization': 'Bearer ' + os.environ['GATEWAY_TOKEN']}
    with httpx.Client(timeout=10, trust_env=False, headers=headers) as client:
        try:
            for msg in queued_tasks(consumer):
                if not running: break
                if msg.error(): log.warning('Kafka: %s',msg.error()); continue
                priority = 'urgent' if msg.topic() == TOPICS['urgent'] else 'standard'
                chunk=json.loads(msg.value()); cid=chunk['campaign_id']; delay=.5; started=time.monotonic()
                time.sleep(float(os.getenv('SIMULATED_CHUNK_LATENCY_MS','30'))/1000)
                while running:
                    die=False
                    try:
                        carrier={k:v.decode() for k,v in (msg.headers() or []) if v is not None}
                        with tracer.start_as_current_span('worker.deliver',context=extract(carrier)) as span:
                            span.set_attribute('campaign.id',cid); span.set_attribute('email.priority',priority)
                            r=client.post(gateway+'/deliver',json=chunk)
                            if r.status_code==410:
                                # A v1 publisher could leave a range for a cancelled campaign.
                                offsets=consumer.commit(message=msg,asynchronous=False)
                                if offsets and any(o.error for o in offsets): raise RuntimeError(f'Offset commit failed: {offsets}')
                                break
                            r.raise_for_status(); receipt=r.json()
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
