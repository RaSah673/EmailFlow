import os
from pathlib import Path
import sys
import unittest
from unittest.mock import patch
from uuid import uuid4


BACKEND = Path(__file__).resolve().parents[1] / 'backend'
DSN = os.environ.get('EMAILFLOW_TEST_DATABASE_URL')


@unittest.skipUnless(DSN, 'Set EMAILFLOW_TEST_DATABASE_URL for PostgreSQL integration tests')
class PostgresTests(unittest.TestCase):
    def test_v1_receipts_survive_repeatable_migration_and_search(self):
        from psycopg import connect
        from psycopg.rows import dict_row

        sys.path.insert(0, str(BACKEND))
        with patch.dict(os.environ, {'DATABASE_URL': DSN}):
            from app.archive import Search, candidate_query, vector

        schema = (BACKEND / 'app' / 'schema.sql').read_text(encoding='utf-8')
        v1, marker, _ = schema.partition('-- v2 migration')
        self.assertTrue(marker)
        campaign = uuid4()

        with connect(DSN, autocommit=True, row_factory=dict_row) as conn:
            self.assertEqual(conn.info.dbname, 'emailflow_ci')
            self.assertEqual(conn.info.server_version // 10000, 16)
            with conn.transaction(force_rollback=True):
                conn.execute(v1)
                conn.execute(
                    """INSERT INTO campaigns
                       (id,name,subject,body,quantity,priority,scheduled_at,chunk_size,
                        mirror_limit,fault,published,accepted,completed,status)
                       VALUES (%s,'Invoice','Invoice due','Invoice payment due',2,'standard',
                               now(),100,0,'none',2,2,2,'completed')""",
                    (campaign,),
                )
                conn.execute('INSERT INTO receipts(campaign_id,seq) VALUES (%s,1),(%s,2)', (campaign, campaign))
                conn.execute(schema)
                conn.execute(schema)
                receipts = conn.execute(
                    'SELECT seq FROM receipts WHERE campaign_id=%s ORDER BY seq', (campaign,),
                ).fetchall()
                self.assertEqual(receipts, [{'seq': 1}, {'seq': 2}])

                conn.execute(
                    """INSERT INTO archive_documents(campaign_id,model,search_text)
                       VALUES (%s,'test',to_tsvector('english','Invoice payment due'))""", (campaign,),
                )
                embedding = vector([1] + [0] * 383)
                conn.execute(
                    'INSERT INTO archive_vectors(campaign_id,part,embedding) VALUES (%s,0,%s::vector)',
                    (campaign, embedding),
                )
                search = Search(query='invoice payment', recipient=2)
                query, parameters = candidate_query(search, embedding, search.weights())
                rows = conn.execute(query, parameters).fetchall()
                self.assertEqual([row['id'] for row in rows], [campaign])
                self.assertAlmostEqual(rows[0]['semantic'], 1.0)
                self.assertGreater(rows[0]['keyword'], 0)
                missing = Search(query='invoice payment', recipient=3)
                query, parameters = candidate_query(missing, embedding, missing.weights())
                self.assertEqual(conn.execute(query, parameters).fetchall(), [])
