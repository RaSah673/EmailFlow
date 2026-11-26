#!/usr/bin/env python3
"""Run repeatable schema and grant changes as the PostgreSQL owner."""
import argparse
import base64
import json
import os
from pathlib import Path
import re
import subprocess
from urllib.parse import urlsplit

from cluster import kubectl

GRANTS = {
    'api': [
        ('SELECT', 'campaigns,receipts,mirror_jobs,incidents,archive_documents,archive_vectors'),
        ('INSERT,UPDATE', 'campaigns'), ('SELECT,UPDATE(last_totp_step,password_hash)', 'auth_users'),
        ('SELECT,INSERT,DELETE,UPDATE(last_seen)', 'auth_sessions'),
        ('SELECT,INSERT,UPDATE', 'auth_limits'), ('INSERT,SELECT', 'security_audit'),
    ],
    'scheduler': [('SELECT,UPDATE', 'campaigns')],
    'gateway': [('SELECT,UPDATE', 'campaigns'), ('SELECT,INSERT', 'receipts,mirror_jobs,fault_claims'), ('INSERT', 'incidents')],
    'worker': [('SELECT,UPDATE', 'campaigns'), ('SELECT,INSERT', 'completed_chunks,fault_claims'), ('SELECT,INSERT,UPDATE', 'incidents')],
    'mirror': [('SELECT', 'campaigns'), ('SELECT,UPDATE', 'mirror_jobs'), ('INSERT', 'incidents')],
    'archive': [('SELECT', 'campaigns'), ('SELECT,INSERT', 'archive_documents,archive_vectors'),
                ('SELECT,INSERT,UPDATE,DELETE', 'archive_index_retries')],
    'users': [('SELECT,INSERT,UPDATE', 'auth_users'), ('SELECT,DELETE', 'auth_sessions,auth_limits'),
              ('INSERT', 'security_audit')],
}
SEQUENCES = {'api': 'security_audit_id_seq', 'users': 'security_audit_id_seq',
             'gateway': 'incidents_id_seq', 'worker': 'incidents_id_seq', 'mirror': 'incidents_id_seq'}


def secret(command, role, key):
    value = json.loads(subprocess.check_output(command + ['get', 'secret', 'emailflow-' + role, '-o', 'json']))
    return base64.b64decode(value['data'][key], validate=True).decode()


def password(value):
    if not value or not re.fullmatch(r'[a-f0-9]{64}', value):
        raise SystemExit('Unexpected credential format. Reconcile the Kubernetes Secret before migrating.')
    return value


def migration(command, rotate_owner):
    schema = Path(__file__).resolve().parents[1] / 'backend/app/schema.sql'
    statements = ['BEGIN;', 'SELECT pg_advisory_xact_lock(879321);', schema.read_text(encoding='utf-8'),
                  'REVOKE CREATE ON SCHEMA public FROM PUBLIC;', 'REVOKE ALL ON DATABASE emailflow FROM PUBLIC;']
    if rotate_owner:
        owner_password = password(secret(command, 'db-owner', 'POSTGRES_PASSWORD'))
        statements.append(f"ALTER ROLE emailflow PASSWORD '{owner_password}';")
    for role, grants in GRANTS.items():
        name = 'ef_' + role
        dsn = urlsplit(secret(command, role, 'DATABASE_URL'))
        if (dsn.username, dsn.hostname, dsn.port, dsn.path) != (name, 'postgres', 5432, '/emailflow'):
            raise SystemExit(f'Unexpected connection target in {role} Secret')
        value = password(dsn.password)
        # Existing service passwords are deliberately stable across deployments.
        statements.extend([
            f"DO $$ BEGIN IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname='{name}') THEN CREATE ROLE {name} LOGIN PASSWORD '{value}'; END IF; END $$;",
            f'ALTER ROLE {name} LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION;',
            f'REVOKE ALL ON ALL TABLES IN SCHEMA public FROM {name};',
            f'REVOKE ALL ON ALL SEQUENCES IN SCHEMA public FROM {name};',
            f'GRANT CONNECT ON DATABASE emailflow TO {name};',
            f'GRANT USAGE ON SCHEMA public TO {name};',
        ])
        statements.extend(f'GRANT {permissions} ON {tables} TO {name};' for permissions, tables in grants)
        if role in SEQUENCES:
            statements.append(f'GRANT USAGE ON SEQUENCE {SEQUENCES[role]} TO {name};')
    return '\n'.join([*statements, 'COMMIT;'])


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--rotate-owner', action='store_true')
    args = parser.parse_args()
    if args.rotate_owner and os.getenv('EMAILFLOW_UPGRADE') != '1':
        parser.error('Owner credential rotation requires an explicitly approved EMAILFLOW_UPGRADE=1 migration')
    command = kubectl()
    result = subprocess.run(command + ['exec', '-i', 'postgres-0', '--', 'psql', '-X', '-q',
                                       '-v', 'ON_ERROR_STOP=1', '-U', 'emailflow', '-d', 'emailflow'],
                            input=migration(command, args.rotate_owner), text=True, capture_output=True, check=False)
    if result.returncode:
        # SQL may contain credentials. Keep server diagnostics inside the cluster.
        raise SystemExit(f'Owner migration failed (psql exit {result.returncode}); check restricted PostgreSQL logs.')
    print('Owner migration completed.')


if __name__ == '__main__':
    main()
