"""Run the named local Docker Compose project with persistent generated secrets."""
import base64
import os
from pathlib import Path
import secrets
import subprocess
import sys


ROOT = Path(__file__).resolve().parents[1]
ENV_FILE = ROOT / '.env.emailflow'
DB_ROLES = ('owner', 'api', 'scheduler', 'gateway', 'worker', 'mirror', 'archive', 'users')


def compose_command():
    return ['docker', 'compose', '--project-directory', str(ROOT), '--env-file', str(ENV_FILE),
            '-f', str(ROOT / 'compose.yaml')]


def ensure_env_file():
    values = {f'EMAILFLOW_DB_{role.upper()}_PASSWORD': secrets.token_hex(32) for role in DB_ROLES}
    values.update(GATEWAY_TOKEN=secrets.token_urlsafe(48), EMBED_TOKEN=secrets.token_urlsafe(48),
                  AUTH_ENCRYPTION_KEY=base64.urlsafe_b64encode(secrets.token_bytes(32)).decode())
    try:
        descriptor = os.open(ENV_FILE, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except FileExistsError:
        return
    with os.fdopen(descriptor, 'w', encoding='utf-8') as file:
        file.writelines(f'{key}={value}\n' for key, value in values.items())


def main():
    ensure_env_file()
    args = sys.argv[1:] or ['up', '--build', '-d', '--wait', '--wait-timeout', '180']
    return subprocess.run(compose_command() + args, cwd=ROOT, check=False).returncode


if __name__ == '__main__':
    sys.exit(main())
