#!/usr/bin/env python3
"""Verify authenticated delivery and archive behavior in the local stack."""
import argparse
import getpass
import http.cookiejar
import json
import os
from pathlib import Path
import re
import secrets
import subprocess
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime, timedelta, timezone
from uuid import UUID

from cluster import kubectl
from compose_local import compose_command

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'backend'))
from app.totp import totp


class Client:
    def __init__(self, origin, compose=False):
        self.origin = origin
        self.compose = compose
        self.csrf = ''
        self.opener = urllib.request.build_opener(urllib.request.ProxyHandler({}),
            urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar()))

    def call(self, path, body=None, *, origin=None, csrf=None):
        data = None if body is None else json.dumps(body, ensure_ascii=False).encode()
        request = urllib.request.Request(self.origin + '/api' + path, data=data, headers={
            'Content-Type': 'application/json', 'Origin': origin or self.origin,
            'X-CSRF-Token': self.csrf if csrf is None else csrf,
        })
        with self.opener.open(request, timeout=45) as response:
            return json.load(response)

    def sign_in(self, credentials):
        self.csrf = self.call('/auth/login', credentials)['csrf']
        return self.call('/auth/me')


def expect_status(code, operation):
    try:
        operation()
    except urllib.error.HTTPError as error:
        with error:
            assert error.code == code, f'Expected HTTP {code}, received {error.code}'
    else:
        raise AssertionError(f'Expected HTTP {code}, received success')


def sql(query, compose=False):
    target = compose_command() + ['exec', '-T', 'postgres'] if compose else kubectl() + ['exec', 'postgres-0', '--']
    return subprocess.check_output(target + ['psql', '-X', '-v',
        'ON_ERROR_STOP=1', '-U', 'emailflow', '-d', 'emailflow', '-At', '-c', query], text=True).strip()


def admin_command(*args, compose=False, input_text=None):
    target = (compose_command() + ['run', '--rm', '-T', 'users'] if compose
              else [sys.executable, str(Path(__file__).with_name('manage-users.py'))])
    # Preserve LF when piping credentials from Windows into a Linux container.
    data = input_text.encode() if input_text is not None else None
    return subprocess.run([*target, *args],
                          check=True, capture_output=True, input=data).stdout.decode()


def fixture(role, compose=False):
    username = f'it-{role}-{secrets.token_hex(4)}'
    password = secrets.token_urlsafe(30)
    output = admin_command('create', username, '--role', role, compose=compose, input_text=f'{password}\n{password}\n')
    secret = re.search(r'(?m)^([A-Z2-7]{32})\s*$', output)
    assert secret, 'Fixture did not return an enrollment secret'
    return {'username': username, 'password': password, 'secret': secret[1]}


def login_data(fixture_data):
    return {'username': fixture_data['username'], 'password': fixture_data['password'],
            'code': totp(fixture_data['secret'], int(time.time() // 30))}


def campaign(name, subject, priority, count, fault='none', delay=15):
    return {'name': name, 'subject': subject, 'body': subject + '. Synthetic local test.',
            'quantity': count, 'priority': priority, 'fault': fault, 'chunk_size': 100, 'mirror_limit': 2,
            'scheduled_at': (datetime.now(timezone.utc) + timedelta(seconds=delay)).isoformat()}


def ready_replicas():
    raw = subprocess.check_output(kubectl() + ['get', 'deployment', 'worker', '-o', 'json'])
    return json.loads(raw).get('status', {}).get('readyReplicas', 0)


def verify_delivery(client, timeout, workloads, *, check_scaling=False):
    started = time.monotonic()
    queued = {}
    for payload in workloads:
        row = client.call('/campaigns', payload)
        cid = str(UUID(row['id']))
        assert client.call(f'/campaigns/{cid}')['accepted'] == 0, 'Accepted before scheduled time'
        queued[cid] = payload
    peak = 1
    pending = dict(queued)
    while pending:
        assert time.monotonic() - started < timeout, 'Campaign delivery timed out'
        if check_scaling:
            peak = max(peak, ready_replicas())
        for cid, payload in list(pending.items()):
            row = client.call(f'/campaigns/{cid}')
            if row['status'] != 'completed':
                continue
            size, fault = payload['quantity'], payload['fault']
            assert row['accepted'] == row['completed'] == row['published'] == size
            assert sql(f"SELECT count(*) || ':' || count(DISTINCT seq) FROM receipts WHERE campaign_id='{cid}'", client.compose) == f'{size}:{size}'
            assert sql(f"SELECT count(*) FROM completed_chunks WHERE campaign_id='{cid}'", client.compose) == str((size + 99) // 100)
            if fault != 'none':
                assert sql(f"SELECT count(*) FROM incidents WHERE campaign_id='{cid}' AND kind='{fault}' AND resolved_at IS NOT NULL", client.compose) == '1'
            print(f'PASS {payload["priority"]} {fault}: {size} unique receipts in {time.monotonic() - started:.1f}s', flush=True)
            pending.pop(cid)
        if pending:
            time.sleep(3)
    if check_scaling:
        assert peak > 1, f'Scale-out not observed: {peak}'
        print(f'Observed peak ready worker replicas: {peak}', flush=True)
    return list(queued)


def verify_archive(client, invoice_id, window):
    base = {'query': 'invoice payment', 'scheduled_from': window, 'recipient': 2}
    lexical = {**base, 'semantic': 0, 'keyword': 1, 'recency': 0, 'priority_weight': 0}
    deadline = time.monotonic() + 180
    while True:
        response = client.call('/archive/search', lexical)
        if response['results'] and response['results'][0]['id'] == invoice_id:
            break
        assert time.monotonic() < deadline, 'Archive backfill or lexical retrieval timed out'
        time.sleep(12)
    assert response['results'][0]['receipts'][0]['seq'] == 2
    assert 'invoice payment' in client.call(f'/archive/documents/{invoice_id}')['body'].lower()
    semantic = client.call('/archive/search', {**base, 'semantic': 1, 'keyword': 0, 'recency': 0, 'priority_weight': 0})
    assert semantic['semantic_available'] and semantic['results'][0]['id'] == invoice_id
    urgent = client.call('/archive/search', {**base, 'semantic': 0, 'keyword': 0, 'recency': 0, 'priority_weight': 1})
    assert urgent['results'][0]['priority'] == 'urgent' and urgent['results'][0]['id'] != invoice_id
    filtered = client.call('/archive/search', {**lexical, 'priority': 'urgent'})
    assert all(hit['priority'] == 'urgent' for hit in filtered['results'])
    assert not client.call('/archive/search', {**lexical, 'recipient': 9999999})['results']
    expect_status(422, lambda: client.call('/archive/search', {**lexical, 'keyword': 0}))
    expect_status(422, lambda: client.call('/archive/search', {**lexical, 'scheduled_to': '2000-01-01T00:00:00Z'}))
    print('PASS archive lexical and semantic retrieval, configurable ranking, recipient and date filters', flush=True)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--compose', action='store_true', help='Target the emailflow Docker Compose project')
    parser.add_argument('--million', action='store_true', help='Run one million recipients per priority and check shared worker scaling')
    parser.add_argument('--ephemeral-users', action='store_true', help='Create and disable disposable users')
    parser.add_argument('--embedding-outage', action='store_true', help='Pause embedding temporarily')
    parser.add_argument('--timeout', type=int, default=1800)
    parser.add_argument('--origin', default=os.getenv('EMAILFLOW_ORIGIN', 'http://localhost:8080'))
    args = parser.parse_args()
    if not re.fullmatch(r'http://(localhost|127\.0\.0\.1):[1-9][0-9]{0,4}', args.origin):
        parser.error('Use a loopback HTTP origin with an explicit port')
    if args.compose and args.million:
        parser.error('--million verifies KEDA scaling and requires Kind')
    if (args.ephemeral_users or args.embedding_outage) and not args.compose and not os.getenv('EMAILFLOW_CLUSTER_NAME', '').endswith('-test'):
        parser.error('Fault and account fixtures require a dedicated -test cluster')

    admin = Client(args.origin, args.compose)
    try:
        expect_status(401, lambda: admin.call('/summary'))
    except urllib.error.URLError as exc:
        parser.exit(1, f'Local dashboard unavailable at {args.origin}: {exc.reason}\n')
    fixtures = {}
    try:
        if args.ephemeral_users:
            for role in ('admin', 'operator', 'viewer'):
                fixtures[role] = fixture(role, args.compose)
            credentials = login_data(fixtures['admin'])
        else:
            credentials = {'username': input('Admin username: '), 'password': getpass.getpass('Password: '),
                           'code': getpass.getpass('Current TOTP code: ')}
        assert admin.sign_in(credentials)['role'] == 'admin'
        expect_status(401, lambda: Client(args.origin).sign_in(credentials))
        expect_status(403, lambda: admin.call('/archive/search', {'query': 'fixture'}, csrf='invalid'))
        expect_status(403, lambda: admin.call('/archive/search', {'query': 'fixture'}, origin='http://example.test'))
        if fixtures:
            viewer, operator = Client(args.origin), Client(args.origin)
            viewer_login = login_data(fixtures['viewer'])
            assert viewer.sign_in(viewer_login)['role'] == 'viewer'
            assert operator.sign_in(login_data(fixtures['operator']))['role'] == 'operator'
            assert viewer.call('/archive/status')['indexed'] >= 0
            expect_status(403, lambda: viewer.call('/infra'))
            expect_status(403, lambda: viewer.call('/campaigns', campaign('Fixture', 'Read only', 'standard', 1)))
            expect_status(403, lambda: operator.call('/campaigns', campaign('Fixture', 'Crash denied', 'urgent', 1, 'worker_after_ack')))
            future = operator.call('/campaigns', campaign('Cancellation', 'Future scheduled test', 'standard', 5, delay=3600))
            assert operator.call(f'/campaigns/{future["id"]}/cancel', {})['status'] == 'cancelled'
        window = datetime.now(timezone.utc).isoformat()
        ids = verify_delivery(admin, args.timeout, [
            campaign('Integration standard', 'Invoice payment due', 'standard', 201),
            campaign('Integration gateway crash', 'Service outage report', 'urgent', 1000, 'gateway_after_accept'),
            campaign('Integration worker crash', 'Appointment reminder', 'urgent', 1000, 'worker_after_ack'),
        ])
        expect_status(409, lambda: admin.call(f'/campaigns/{ids[0]}/cancel', {}))
        verify_archive(admin, ids[0], window)
        if args.embedding_outage:
            if args.compose:
                target = compose_command()
                stop = ['stop', 'embedding']
                start = ['up', '-d', '--wait', 'embedding']
            else:
                target = kubectl()
                stop = ['scale', 'deployment/embedding', '--replicas=0']
                start = ['scale', 'deployment/embedding', '--replicas=1']
            subprocess.run(target + stop, check=True)
            try:
                if not args.compose:
                    subprocess.run(target + ['rollout', 'status', 'deployment/embedding', '--timeout=90s'], check=True)
                outage = admin.call('/archive/search', {'query': 'invoice payment', 'scheduled_from': window})
                assert not outage['semantic_available'] and any(hit['id'] == ids[0] for hit in outage['results'])
                verify_delivery(admin, args.timeout, [campaign('Embedding paused', 'Delivery independent', 'standard', 101)])
            finally:
                subprocess.run(target + start, check=True)
        if args.million:
            verify_delivery(admin, args.timeout, [
                campaign('Standard scale test', 'Synthetic standard workload', 'standard', 1_000_000),
                campaign('Urgent scale test', 'Synthetic urgent workload', 'urgent', 1_000_000),
            ], check_scaling=True)
        if fixtures:
            for _ in range(20):
                viewer.call('/archive/search', {'query': 'fixture', 'semantic': 0, 'keyword': 1, 'recency': 0, 'priority_weight': 0})
            expect_status(429, lambda: viewer.call('/archive/search', {'query': 'fixture'}))
            for _ in range(4):
                expect_status(401, lambda: Client(args.origin).sign_in(viewer_login))
            expect_status(429, lambda: Client(args.origin).sign_in(viewer_login))
            sql(f"UPDATE auth_sessions SET last_seen=now()-interval '31 minutes' WHERE user_id=(SELECT id FROM auth_users WHERE username='{fixtures['viewer']['username']}')", args.compose)
            expect_status(401, lambda: viewer.call('/summary'))
            admin_command('revoke', fixtures['operator']['username'], compose=args.compose)
            expect_status(401, lambda: operator.call('/summary'))
        admin.call('/auth/logout', {})
        expect_status(401, lambda: admin.call('/summary'))
        print('All selected integration scenarios passed.')
    finally:
        for credentials in fixtures.values():
            admin_command('disable', credentials['username'], compose=args.compose)


if __name__ == '__main__':
    main()
