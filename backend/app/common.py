import os, time, logging, json
from pathlib import Path
from psycopg_pool import ConnectionPool
from psycopg.rows import dict_row
from opentelemetry import trace, metrics
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor
from opentelemetry.sdk.metrics import MeterProvider
from opentelemetry.sdk.metrics.export import PeriodicExportingMetricReader
from opentelemetry.sdk._logs import LoggerProvider, LoggingHandler
from opentelemetry.sdk._logs.export import BatchLogRecordProcessor
from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
from opentelemetry.exporter.otlp.proto.http.metric_exporter import OTLPMetricExporter
from opentelemetry.exporter.otlp.proto.http._log_exporter import OTLPLogExporter
from opentelemetry.instrumentation.httpx import HTTPXClientInstrumentor

SERVICE=os.getenv('SERVICE_NAME','emailflow-api')
logging.basicConfig(level=logging.INFO,format='%(asctime)s %(levelname)s %(name)s %(message)s')
log=logging.getLogger(SERVICE)
resource=Resource.create({'service.name':SERVICE})
provider=TracerProvider(resource=resource)
endpoint=os.getenv('OTEL_EXPORTER_OTLP_ENDPOINT','').rstrip('/')
if endpoint:
    provider.add_span_processor(BatchSpanProcessor(OTLPSpanExporter(endpoint=endpoint+'/v1/traces')))
    metrics.set_meter_provider(MeterProvider(resource=resource,metric_readers=[PeriodicExportingMetricReader(OTLPMetricExporter(endpoint=endpoint+'/v1/metrics'),export_interval_millis=5000)]))
    lp=LoggerProvider(resource=resource)
    lp.add_log_record_processor(BatchLogRecordProcessor(OTLPLogExporter(endpoint=endpoint+'/v1/logs')))
    log.addHandler(LoggingHandler(level=logging.INFO,logger_provider=lp))
else:
    lp=None
trace.set_tracer_provider(provider)
HTTPXClientInstrumentor().instrument()
tracer=trace.get_tracer(SERVICE)
meter=metrics.get_meter(SERVICE)
processed=meter.create_counter('emailflow.emails.processed')
failures=meter.create_counter('emailflow.failures')
pool=ConnectionPool(os.getenv('DATABASE_URL','postgresql://emailflow:emailflow@postgres:5432/emailflow'),min_size=1,max_size=int(os.getenv('DB_POOL_SIZE','4')),kwargs={'row_factory':dict_row},open=False)
BOOTSTRAP=os.getenv('KAFKA_BOOTSTRAP','kafka:9092')
TOPICS={'standard':'emailflow.standard','urgent':'emailflow.urgent'}
GROUPS={p:'emailflow-'+p for p in TOPICS}
PARTITIONS={'standard':12,'urgent':24}

def init_db():
    pool.open(wait=True,timeout=90)
    with pool.connection() as conn:
        conn.execute('SELECT pg_advisory_xact_lock(879321)')
        conn.execute(Path(__file__).with_name('schema.sql').read_text())

def trace_id():
    ctx=trace.get_current_span().get_span_context()
    return format(ctx.trace_id,'032x') if ctx.is_valid else None

def incident(conn,campaign,kind,detail,severity='warning'):
    conn.execute('INSERT INTO incidents(campaign_id,service,kind,severity,detail,trace_id) VALUES (%s,%s,%s,%s,%s,%s)',(campaign,SERVICE,kind,severity,detail,trace_id()))
    failures.add(1,{'kind':kind})
    log.warning('%s campaign=%s %s',kind,campaign,detail)

def claim_fault(conn,campaign,kind,start):
    return bool(conn.execute('INSERT INTO fault_claims(campaign_id,kind,start_seq) VALUES (%s,%s,%s) ON CONFLICT DO NOTHING RETURNING kind',(campaign,kind,start)).fetchone())

def resolve(conn,campaign,kind,start):
    conn.execute('UPDATE incidents SET resolved_at=now() WHERE campaign_id=%s AND kind=%s AND resolved_at IS NULL AND EXISTS (SELECT 1 FROM fault_claims f WHERE f.campaign_id=incidents.campaign_id AND f.kind=incidents.kind AND f.start_seq=%s)',(campaign,kind,start))

def crash():
    # Called only after durable DB transaction and after the current span closes.
    provider.force_flush(timeout_millis=1000)
    if lp: lp.force_flush(timeout_millis=1000)
    os._exit(42)

def ensure_topics():
    from confluent_kafka.admin import AdminClient,NewTopic
    admin=AdminClient({'bootstrap.servers':BOOTSTRAP})
    from confluent_kafka import KafkaException, KafkaError
    for attempt in range(60):
        try:
            futures=admin.create_topics([NewTopic(TOPICS[p],PARTITIONS[p],1) for p in TOPICS],operation_timeout=10)
            for f in futures.values():
                try: f.result(timeout=15)
                except KafkaException as e:
                    if e.args[0].code()!=KafkaError.TOPIC_ALREADY_EXISTS: raise
            return
        except Exception:
            if attempt==59: raise
            time.sleep(2)
