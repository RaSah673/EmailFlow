import smtplib,time,os
from email.message import EmailMessage
from .common import *
from .domain import delivery_key

def run():
    init_db()
    while True:
        try:
            with pool.connection() as conn:
                expired=conn.execute("UPDATE mirror_jobs SET status='unknown',updated_at=now() WHERE status='sending' AND updated_at<now()-interval '2 minutes' RETURNING campaign_id,seq").fetchall()
                for x in expired: incident(conn,x['campaign_id'],'smtp_unknown',f"Sample {x['seq']}: SMTP outcome unknown; no automatic resend.")
                job=conn.execute("SELECT m.campaign_id,m.seq,c.subject,c.body FROM mirror_jobs m JOIN campaigns c ON c.id=m.campaign_id WHERE m.status='pending' ORDER BY m.updated_at LIMIT 1 FOR UPDATE OF m SKIP LOCKED").fetchone()
                if job: conn.execute("UPDATE mirror_jobs SET status='sending',updated_at=now() WHERE campaign_id=%s AND seq=%s",(job['campaign_id'],job['seq']))
            if not job: time.sleep(.5); continue
            with tracer.start_as_current_span('mirror.smtp'):
                message=EmailMessage(); message['From']='sender@emailflow.test'; message['To']=f"recipient-{job['seq']}@emailflow.test"; message['Subject']=job['subject']; message['Message-ID']=f"<{delivery_key(str(job['campaign_id']),job['seq'])}>"; message.set_content(job['body'])
                try:
                    with smtplib.SMTP(os.getenv('SMTP_HOST','mailcatcher'),1025,timeout=10) as smtp: smtp.send_message(message)
                    state='sent'; error=None
                except Exception as e: state='unknown'; error=str(e)
                with pool.connection() as conn:
                    conn.execute('UPDATE mirror_jobs SET status=%s,updated_at=now() WHERE campaign_id=%s AND seq=%s',(state,job['campaign_id'],job['seq']))
                    if error: incident(conn,job['campaign_id'],'smtp_unknown',f"Sample {job['seq']}: {error}; no automatic resend.")
        except Exception: log.exception('Mirror error'); time.sleep(2)
if __name__=='__main__': run()
