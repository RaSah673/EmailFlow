import json,time
from confluent_kafka import Producer
from opentelemetry.propagate import inject
from .common import *
from .domain import chunk_ranges

def reserve_campaign(priority):
    # Commit before exposing tasks to Kafka. Dispatch retries can replay ranges
    # without reopening the campaign for cancellation.
    with pool.connection() as conn:
        row = conn.execute(
            """SELECT id,status FROM campaigns WHERE priority=%s
               AND status IN ('scheduled','dispatching') AND scheduled_at<=now()
               ORDER BY scheduled_at,id LIMIT 1 FOR UPDATE SKIP LOCKED""", (priority,)
        ).fetchone()
        if row and row['status'] == 'scheduled':
            conn.execute("UPDATE campaigns SET status='dispatching' WHERE id=%s", (row['id'],))
        return row['id'] if row else None

def run():
    init_db(); ensure_topics()
    producer=Producer({'bootstrap.servers':BOOTSTRAP,'enable.idempotence':True,'acks':'all','message.timeout.ms':15000,'linger.ms':5})
    while True:
        work=False
        for priority in ('urgent','standard'):
            try:
                cid = reserve_campaign(priority)
                if cid is None:
                    continue
                with tracer.start_as_current_span('scheduler.dispatch') as span, pool.connection() as conn:
                    row=conn.execute("SELECT id,quantity,published,chunk_size FROM campaigns WHERE id=%s AND status='dispatching' FOR UPDATE SKIP LOCKED",(cid,)).fetchone()
                    if not row: continue
                    work=True; errors=[]; last=row['published']; cid=str(row['id']); span.set_attribute('campaign.id',cid)
                    def callback(err,msg):
                        if err: errors.append(str(err))
                    carrier={}; inject(carrier)
                    for start,end in chunk_ranges(last,row['quantity'],row['chunk_size']):
                        producer.produce(TOPICS[priority],key=f'{cid}:{start}',value=json.dumps({'campaign_id':cid,'start':start,'end':end}),headers=list(carrier.items()),on_delivery=callback); last=end
                    remaining=producer.flush(20)
                    if remaining or errors: raise RuntimeError(f'Kafka publish incomplete: {remaining} {errors}')
                    conn.execute("UPDATE campaigns SET published=%s,status=CASE WHEN completed=quantity THEN 'completed' WHEN %s=quantity THEN 'queued' ELSE 'dispatching' END WHERE id=%s",(last,last,row['id']))
                    span.set_attribute('emails.published',last-row['published'])
            except Exception:
                log.exception('Scheduler publish failed; cursor unchanged, retrying'); failures.add(1,{'kind':'publish'}); time.sleep(1)
        if not work: time.sleep(.5)
if __name__=='__main__': run()
