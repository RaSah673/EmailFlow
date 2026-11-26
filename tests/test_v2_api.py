import importlib.util
import os
import sys
import unittest
from contextlib import contextmanager
from pathlib import Path
from unittest.mock import Mock, patch
from uuid import uuid4

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'backend'))
DEPENDENCIES = ('fastapi', 'psycopg', 'psycopg_pool', 'confluent_kafka', 'argon2', 'httpx')


@unittest.skipUnless(all(importlib.util.find_spec(name) for name in DEPENDENCIES), 'Install backend requirements for API tests')
class ApiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        with patch.dict(os.environ, {'DATABASE_URL': 'postgresql:///emailflow'}):
            from app import api, archive, gateway, scheduler, security
            from fastapi.testclient import TestClient
        cls.api, cls.archive, cls.gateway, cls.scheduler, cls.security = api, archive, gateway, scheduler, security
        # ASGI requests use a mock DB pool. Live PostgreSQL is tested independently.
        cls.client = TestClient(api.app)

    def test_direct_api_requires_authentication(self):
        for path in ('/api/campaigns', '/api/archive/status', '/api/infra', '/api/auth/audit'):
            with self.subTest(path=path):
                self.assertEqual(self.client.get(path).status_code, 401)

    def test_registration_is_unavailable_in_managed_mode(self):
        with patch.object(self.security, 'LOCAL_ACCOUNTS', False):
            options = self.client.get('/api/auth/options')
            self.assertEqual(options.json(), {'registration': False, 'demo_username': None})
            headers = {'Origin': self.security.ORIGIN}
            start = self.client.post('/api/auth/register', headers=headers, json={
                'email': 'person@example.com', 'password': 'registration test fixture',
            })
            verify = self.client.post('/api/auth/register/verify', headers=headers, json={
                'enrollment_token': 'unused', 'code': '123456',
            })
            self.assertEqual(start.status_code, 404)
            self.assertEqual(verify.status_code, 404)

    def test_local_registration_checks_origin_before_enrollment(self):
        with patch.object(self.security, 'LOCAL_ACCOUNTS', True), patch.object(self.security, 'pool') as pool:
            response = self.client.post('/api/auth/register', headers={'Origin': 'http://elsewhere.test'}, json={
                'email': 'person@example.com', 'password': 'registration test fixture',
            })
            self.assertEqual(response.status_code, 403)
            pool.connection.assert_not_called()

    def test_origin_aliases_are_limited_to_http_loopback_and_the_configured_port(self):
        expected = {'http://localhost:18080', 'http://127.0.0.1:18080'}
        for configured in expected:
            with self.subTest(configured=configured):
                self.assertEqual(self.security.allowed_origins(configured), expected)
        self.assertEqual(self.security.allowed_origins('http://localhost'), {'http://localhost', 'http://127.0.0.1'})
        self.assertEqual(self.security.allowed_origins('https://example.test'), {'https://example.test'})
        self.assertEqual(self.security.allowed_origins('https://localhost:8443'), {'https://localhost:8443'})

    def test_demo_password_only_capability_is_limited_to_local_viewer(self):
        account = {'enabled': True, 'role': 'viewer', 'totp_secret': ''}
        username = self.security.DEMO_USERNAME
        with patch.object(self.security, 'LOCAL_ACCOUNTS', True):
            self.assertTrue(self.security.is_demo(username, account))
            self.assertFalse(self.security.is_demo(username, dict(account, role='admin')))
            self.assertFalse(self.security.is_demo(username, dict(account, enabled=False)))
            self.assertFalse(self.security.is_demo(username, dict(account, totp_secret='enrolled')))
            self.assertFalse(self.security.is_demo('other@example.com', account))
        with patch.object(self.security, 'LOCAL_ACCOUNTS', False):
            self.assertFalse(self.security.is_demo(username, account))

    def test_viewer_cannot_write_or_read_admin_routes(self):
        self.api.app.dependency_overrides[self.security.current] = lambda: {'role': 'viewer'}
        try:
            self.assertEqual(self.client.get('/api/infra').status_code, 403)
            self.assertEqual(self.client.get('/api/auth/audit').status_code, 403)
            self.assertEqual(self.client.post(f'/api/campaigns/{uuid4()}/cancel').status_code, 403)
        finally:
            self.api.app.dependency_overrides.clear()

    def test_operator_cannot_inject_a_crash(self):
        self.api.app.dependency_overrides[self.security.current] = lambda: {'role': 'operator'}
        try:
            response = self.client.post('/api/campaigns', json={
                'name': 'Test', 'subject': 'Test', 'body': 'Synthetic message', 'quantity': 1,
                'scheduled_at': '2030-01-01T00:00:00Z', 'fault': 'worker_after_ack',
            })
            self.assertEqual(response.status_code, 403)
        finally:
            self.api.app.dependency_overrides.clear()

    def test_origin_and_csrf_are_checked_before_mutation(self):
        token = 'fixture-session-token'
        csrf = 'fixture-csrf'
        db = Mock()
        db.execute.return_value.fetchone.return_value = {'csrf_hash': self.security.digest(csrf), 'role': 'viewer', 'token_hash': 'hash'}

        @contextmanager
        def connection():
            yield db

        self.client.cookies.set(self.security.COOKIE, token)
        try:
            with patch.object(self.security.pool, 'connection', connection):
                for origin, candidate in ((self.security.ORIGIN, 'wrong'), ('http://example.test', csrf)):
                    response = self.client.post('/api/archive/search', json={'query': 'sample'},
                                                headers={'Origin': origin, 'X-CSRF-Token': candidate})
                    self.assertEqual(response.status_code, 403)
                self.assertFalse(any('UPDATE auth_sessions' in call.args[0] for call in db.execute.call_args_list))
        finally:
            self.client.cookies.clear()

    def test_validation_does_not_echo_input(self):
        response = self.client.post('/api/auth/login', json={'username': 'fixture', 'password': 'candidate-secret', 'code': 'not-a-code'})
        self.assertEqual(response.status_code, 422)
        self.assertNotIn('candidate-secret', response.text)
        self.assertNotIn('not-a-code', response.text)
        self.assertEqual(self.client.post('/api/campaigns', content=b'x' * 262145).status_code, 413)

    def test_search_weights_dates_and_parameters(self):
        for fields in ({'semantic': 0, 'keyword': 0, 'recency': 0, 'priority_weight': 0},
                       {'scheduled_from': '2030-01-01T12:00:00'},
                       {'scheduled_from': '2030-01-02T12:00:00Z', 'scheduled_to': '2030-01-01T12:00:00Z'},
                       {'semantic': float('nan')}):
            with self.subTest(fields=fields), self.assertRaises(ValueError):
                self.archive.Search(query='test', **fields)
        s = self.archive.Search(query='literal reference', priority='urgent', recipient=99)
        value = self.archive.vector([1] + [0] * 383)
        query, parameters = self.archive.candidate_query(s, value, s.weights())
        self.assertEqual(query.as_string().count('%s'), len(parameters))
        self.assertNotIn(s.query, query.as_string())
        fallback, parameters = self.archive.candidate_query(s, None, {**s.weights(), 'semantic': 0})
        self.assertNotIn('semantic_chunks', fallback.as_string())
        self.assertEqual(fallback.as_string().count('%s'), len(parameters))

    def test_reservation_commits_before_return(self):
        campaign = uuid4()
        db = Mock()
        db.execute.return_value.fetchone.return_value = {'id': campaign, 'status': 'scheduled'}
        committed = []

        @contextmanager
        def connection():
            yield db
            committed.append(True)

        with patch.object(self.scheduler.pool, 'connection', connection):
            self.assertEqual(self.scheduler.reserve_campaign('urgent'), campaign)
        self.assertEqual(committed, [True])
        self.assertIn("status='dispatching'", db.execute.call_args.args[0])


if __name__ == '__main__':
    unittest.main()
