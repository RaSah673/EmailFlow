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


def migration(schema, role_passwords, owner_password=None, *, local_accounts=False):
    statements = ['BEGIN;', 'SELECT pg_advisory_xact_lock(879321);', schema.read_text(encoding='utf-8'),
                  'REVOKE CREATE ON SCHEMA public FROM PUBLIC;', 'REVOKE ALL ON DATABASE emailflow FROM PUBLIC;']
    if owner_password is not None:
        owner_password = password(owner_password)
        statements.append(f"ALTER ROLE emailflow PASSWORD '{owner_password}';")
    for role, grants in GRANTS.items():
        name = 'ef_' + role
        value = password(role_passwords[role])
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
    action = 'GRANT EXECUTE ON' if local_accounts else 'REVOKE EXECUTE ON'
    target = 'TO' if local_accounts else 'FROM'
    statements.append(f'{action} FUNCTION public.register_local_operator(uuid,text,text,text,bigint) {target} ef_api;')
    return '\n'.join([*statements, 'COMMIT;'])


def kubernetes_passwords(command):
    values = {}
    for role in GRANTS:
        dsn = urlsplit(secret(command, role, 'DATABASE_URL'))
        if (dsn.username, dsn.hostname, dsn.port, dsn.path) != ('ef_' + role, 'postgres', 5432, '/emailflow'):
            raise SystemExit(f'Unexpected connection target in {role} Secret')
        values[role] = dsn.password
    return values


def migrate_compose():
    from psycopg import Error, connect

    schema = Path(__file__).resolve().parents[1] / 'app/schema.sql'
    values = {role: os.environ[f'EMAILFLOW_DB_{role.upper()}_PASSWORD'] for role in GRANTS}
    origin = urlsplit(os.environ['APP_ORIGIN'])
    local_accounts = (os.getenv('EMAILFLOW_LOCAL_ACCOUNTS') == '1'
                      and origin.scheme == 'http' and origin.hostname in ('localhost', '127.0.0.1'))
    try:
        with connect(os.environ['DATABASE_URL'], autocommit=True) as conn:
            conn.execute(migration(schema, values, local_accounts=local_accounts))
    except Error as error:
        raise SystemExit(f'Owner migration failed ({type(error).__name__}). Check restricted PostgreSQL logs.') from None


def main():
    parser = argparse.ArgumentParser()
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument('--rotate-owner', action='store_true')
    mode.add_argument('--compose', action='store_true')
    args = parser.parse_args()
    if args.compose:
        migrate_compose()
        print('Owner migration completed.')
        return
    if args.rotate_owner and os.getenv('EMAILFLOW_UPGRADE') != '1':
        parser.error('Owner credential rotation requires an explicitly approved EMAILFLOW_UPGRADE=1 migration')
    command = kubectl()
    schema = Path(__file__).resolve().parents[1] / 'backend/app/schema.sql'
    owner_password = secret(command, 'db-owner', 'POSTGRES_PASSWORD') if args.rotate_owner else None
    result = subprocess.run(command + ['exec', '-i', 'postgres-0', '--', 'psql', '-X', '-q',
                                       '-v', 'ON_ERROR_STOP=1', '-U', 'emailflow', '-d', 'emailflow'],
                            input=migration(schema, kubernetes_passwords(command), owner_password),
                            text=True, capture_output=True, check=False)
    if result.returncode:
        # SQL may contain credentials. Keep server diagnostics inside the cluster.
        raise SystemExit(f'Owner migration failed (psql exit {result.returncode}); check restricted PostgreSQL logs.')
    print('Owner migration completed.')


if __name__ == '__main__':
    main()
