"""Index accepted campaign content without coupling delivery to the model."""
import math
import os
import time
from datetime import datetime, timezone
from typing import Literal
from uuid import UUID

import httpx
from fastapi import APIRouter, Depends, HTTPException
from psycopg import errors, sql
from pydantic import BaseModel, Field, model_validator

from .common import init_db, log, pool, tracer
from .ranking import chunks, score
from .security import audit, current, limited

MODEL = 'BAAI/bge-small-en-v1.5'
router = APIRouter(prefix='/api/archive')


def vector(values):
    floats = [float(value) for value in values]
    if len(floats) != 384 or not all(math.isfinite(value) for value in floats) or not any(floats):
        raise ValueError('Invalid embedding')
    return '[' + ','.join(map(str, floats)) + ']'


def embed(texts, mode):
    url = os.getenv('EMBED_URL', 'http://embedding:8000')
    headers = {'Authorization': 'Bearer ' + os.environ['EMBED_TOKEN']}
    timeout = httpx.Timeout(10 if mode == 'query' else 25, connect=2)
    with httpx.Client(timeout=timeout, trust_env=False) as client:
        response = client.post(url + '/embed', json={'texts': texts, 'mode': mode}, headers=headers)
        response.raise_for_status()
        result = response.json()
    if result['model'] != MODEL or len(result['vectors']) != len(texts):
        raise ValueError('Invalid embedding response')
    return [vector(values) for values in result['vectors']]


def index_campaign(campaign):
    campaign_id = campaign['id']
    vectors = embed(chunks(campaign['subject'], campaign['body']), 'document')
    # Publish the document and its full vector set in the same transaction.
    with pool.connection() as conn:
        created = conn.execute(
            """INSERT INTO archive_documents(campaign_id,model,search_text)
               VALUES (%s,%s,setweight(to_tsvector('english',%s),'A')||
                            setweight(to_tsvector('english',%s),'B'))
               ON CONFLICT DO NOTHING RETURNING campaign_id""",
            (campaign_id, MODEL, campaign['subject'], campaign['body']),
        ).fetchone()
        if created:
            with conn.cursor() as cursor:
                cursor.executemany(
                    'INSERT INTO archive_vectors(campaign_id,part,embedding) VALUES (%s,%s,%s::vector)',
                    [(campaign_id, part, value) for part, value in enumerate(vectors)],
                )
        conn.execute('DELETE FROM archive_index_retries WHERE campaign_id=%s', (campaign_id,))


def run():
    init_db()
    while True:
        try:
            with pool.connection() as conn:
                campaign = conn.execute(
                    """SELECT c.id,c.subject,c.body FROM campaigns c
                       LEFT JOIN archive_index_retries r ON r.campaign_id=c.id
                       WHERE c.accepted>0 AND (r.next_attempt IS NULL OR r.next_attempt<=now())
                       AND NOT EXISTS (SELECT 1 FROM archive_documents d WHERE d.campaign_id=c.id)
                       ORDER BY c.created_at,c.id LIMIT 1""",
                ).fetchone()
            if not campaign:
                time.sleep(2)
                continue
            try:
                with tracer.start_as_current_span('archive.index', record_exception=False):
                    index_campaign(campaign)
            except Exception as exc:
                log.warning('Archive indexing failed (%s)', type(exc).__name__)
                with pool.connection() as conn:
                    conn.execute(
                        """INSERT INTO archive_index_retries(campaign_id,next_attempt)
                           VALUES (%s,now()+interval '30 seconds')
                           ON CONFLICT(campaign_id) DO UPDATE SET next_attempt=now()+interval '30 seconds'""",
                        (campaign['id'],),
                    )
                time.sleep(2)
        except Exception as exc:
            log.warning('Archive indexer unavailable (%s)', type(exc).__name__)
            time.sleep(5)


class Search(BaseModel):
    query: str = Field(min_length=1, max_length=1000)
    priority: Literal['all', 'urgent', 'standard'] = 'all'
    recipient: int | None = Field(None, ge=1, le=10_000_000)
    scheduled_from: datetime | None = None
    scheduled_to: datetime | None = None
    semantic: float = Field(.6, ge=0, le=1, allow_inf_nan=False)
    keyword: float = Field(.25, ge=0, le=1, allow_inf_nan=False)
    recency: float = Field(.1, ge=0, le=1, allow_inf_nan=False)
    priority_weight: float = Field(.05, ge=0, le=1, allow_inf_nan=False)
    limit: int = Field(20, ge=1, le=50)

    @model_validator(mode='after')
    def valid(self):
        if not self.query.strip() or '\x00' in self.query or not sum(self.weights().values()):
            raise ValueError('Query and nonzero weights required')
        if any(value is not None and value.utcoffset() is None for value in (self.scheduled_from, self.scheduled_to)):
            raise ValueError('Dates must include timezone')
        if self.scheduled_from and self.scheduled_to and self.scheduled_from > self.scheduled_to:
            raise ValueError('Invalid date range')
        return self

    def weights(self):
        return {'semantic': self.semantic, 'keyword': self.keyword, 'recency': self.recency, 'priority': self.priority_weight}


def candidate_query(search, query_vector, weights):
    filters = [sql.SQL('c.accepted>0')]
    arguments = []
    for value, condition in (
        (None if search.priority == 'all' else search.priority, sql.SQL('c.priority=%s')),
        (search.recipient, sql.SQL('EXISTS (SELECT 1 FROM receipts r WHERE r.campaign_id=c.id AND r.seq=%s)')),
        (search.scheduled_from, sql.SQL('c.scheduled_at>=%s')),
        (search.scheduled_to, sql.SQL('c.scheduled_at<=%s')),
    ):
        if value is not None:
            filters.append(condition)
            arguments.append(value)
    where = sql.SQL(' AND ').join(filters)
    ctes, queries, params = [], [], []
    if query_vector is not None:
        ctes.append(sql.SQL('''semantic_chunks AS MATERIALIZED (
            SELECT v.campaign_id,v.embedding <=> %s::vector AS distance
            FROM archive_vectors v JOIN campaigns c ON c.id=v.campaign_id
            WHERE {} ORDER BY v.embedding <=> %s::vector LIMIT 400)''').format(where))
        params.extend((query_vector, *arguments, query_vector))
        queries.append(sql.SQL('SELECT campaign_id FROM semantic_chunks GROUP BY campaign_id ORDER BY min(distance),campaign_id LIMIT 100'))
    if weights['keyword']:
        queries.append(sql.SQL('''SELECT d.campaign_id FROM archive_documents d JOIN campaigns c ON c.id=d.campaign_id
            WHERE {} AND d.search_text @@ websearch_to_tsquery('english',%s)
            ORDER BY ts_rank_cd(d.search_text,websearch_to_tsquery('english',%s),32) DESC,d.campaign_id LIMIT 100''').format(where))
        params.extend((*arguments, search.query, search.query))
    for name, order in (
        ('recency', sql.SQL('c.scheduled_at DESC,c.id')),
        ('priority', sql.SQL("(c.priority='urgent') DESC,c.scheduled_at DESC,c.id")),
    ):
        if weights[name]:
            queries.append(sql.SQL('''SELECT d.campaign_id FROM archive_documents d JOIN campaigns c ON c.id=d.campaign_id
                WHERE {} ORDER BY {} LIMIT 100''').format(where, order))
            params.extend(arguments)
    ctes.append(sql.SQL('candidates AS ({})').format(sql.SQL(' UNION ').join(sql.SQL('({})').format(query) for query in queries)))
    query = sql.SQL('''WITH {}
        SELECT c.id,c.name,c.subject,left(c.body,350) AS preview,length(c.body)>350 AS truncated,
               c.priority,c.scheduled_at,c.accepted,
               CASE WHEN %s::vector IS NULL THEN 0 ELSE
                   coalesce(1-(SELECT min(v.embedding <=> %s::vector) FROM archive_vectors v WHERE v.campaign_id=c.id),0)
               END AS semantic,
               ts_rank_cd(d.search_text,websearch_to_tsquery('english',%s),32) AS keyword
        FROM candidates k JOIN campaigns c ON c.id=k.campaign_id JOIN archive_documents d ON d.campaign_id=c.id''').format(sql.SQL(', ').join(ctes))
    return query, [*params, query_vector, query_vector, search.query]


@router.get('/status')
def status(u=Depends(current)):
    with pool.connection() as conn:
        conn.execute("SET LOCAL statement_timeout='3s'")
        return conn.execute('''SELECT count(d.campaign_id) AS indexed,
            count(*) FILTER(WHERE d.campaign_id IS NULL) AS pending,
            coalesce(sum(c.accepted) FILTER(WHERE d.campaign_id IS NOT NULL),0) AS searchable_emails
            FROM campaigns c LEFT JOIN archive_documents d ON d.campaign_id=c.id WHERE c.accepted>0''').fetchone()


@router.get('/documents/{campaign_id}')
def document(campaign_id: UUID, u=Depends(current)):
    with pool.connection() as conn:
        row = conn.execute('''SELECT c.body FROM archive_documents d JOIN campaigns c ON c.id=d.campaign_id
                              WHERE d.campaign_id=%s''', (campaign_id,)).fetchone()
    if not row:
        raise HTTPException(404, 'Archive document not found')
    return row


@router.post('/search')
def search_archive(s: Search, u=Depends(current)):
    with pool.connection() as conn:
        blocked = limited(conn, 'search:' + str(u['id']), 20, 60)
        audit(conn, u['username'], 'archive_search', 'limited' if blocked else 'requested')
    if blocked:
        raise HTTPException(429, 'Search rate limit reached')
    weights = s.weights()
    query_vector = None
    semantic_available = True
    if weights['semantic']:
        try:
            query_vector = embed([s.query], 'query')[0]
        except (httpx.HTTPError, ValueError, KeyError, TypeError):
            semantic_available = False
            weights['semantic'] = 0
            if not sum(weights.values()):
                raise HTTPException(503, 'Semantic search is temporarily unavailable') from None

    query, parameters = candidate_query(s, query_vector, weights)
    now = datetime.now(timezone.utc)
    total = sum(weights.values())
    try:
        with pool.connection() as conn:
            conn.execute("SET LOCAL statement_timeout='8s'")
            conn.execute("SET LOCAL hnsw.iterative_scan='strict_order'")
            rows = conn.execute(query, parameters).fetchall()
            for row in rows:
                age = (now - row['scheduled_at']).total_seconds() / 86400
                row['score'], row['components'] = score(row.pop('semantic'), row.pop('keyword'), age, row['priority'] == 'urgent', weights)
                row['contributions'] = {key: value * weights[key] / total for key, value in row['components'].items()}
            rows.sort(key=lambda row: (row['score'], str(row['id'])), reverse=True)
            results = rows[:s.limit]
            if results:
                receipts = conn.execute('''SELECT x.campaign_id,r.seq,r.accepted_at
                    FROM unnest(%s::uuid[]) AS x(campaign_id) CROSS JOIN LATERAL (
                        SELECT seq,accepted_at FROM receipts
                        WHERE campaign_id=x.campaign_id AND (%s::integer IS NULL OR seq=%s)
                        ORDER BY seq LIMIT 5
                    ) r''', ([row['id'] for row in results], s.recipient, s.recipient)).fetchall()
                grouped = {row['id']: [] for row in results}
                for receipt in receipts:
                    grouped[receipt.pop('campaign_id')].append(receipt)
                for row in results:
                    row['receipts'] = grouped[row['id']]
    except errors.QueryCanceled:
        raise HTTPException(503, 'Archive query timed out; narrow the filters') from None
    return {'results': results, 'candidate_count': len(rows), 'model': MODEL,
            'grouped': True, 'semantic_available': semantic_available}


if __name__ == '__main__':
    run()
