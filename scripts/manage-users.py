"""Run user administration in a short-lived, dedicated security context."""
import json
import secrets
import subprocess
import sys

from cluster import kubectl


def main():
    if len(sys.argv) < 2:
        raise SystemExit('Usage: bash scripts/users.sh ACTION [USERNAME] [--role ROLE]')
    command = kubectl()
    name = 'emailflow-user-admin-' + secrets.token_hex(5)
    pod = {
        'apiVersion': 'v1', 'kind': 'Pod', 'metadata': {'name': name, 'labels': {'app': 'users'}},
        'spec': {
            'automountServiceAccountToken': False, 'restartPolicy': 'Never', 'activeDeadlineSeconds': 600,
            'containers': [{
                'name': 'users', 'image': 'emailflow-backend:local', 'imagePullPolicy': 'Never',
                'command': ['sleep', '600'],
                'envFrom': [{'secretRef': {'name': 'emailflow-users'}}, {'secretRef': {'name': 'emailflow-auth'}}],
                'resources': {'requests': {'cpu': '25m', 'memory': '64Mi'}, 'limits': {'cpu': '1', 'memory': '256Mi'}},
                'securityContext': {'runAsNonRoot': True, 'runAsUser': 10001, 'runAsGroup': 10001,
                                    'allowPrivilegeEscalation': False, 'readOnlyRootFilesystem': True,
                                    'capabilities': {'drop': ['ALL']}, 'seccompProfile': {'type': 'RuntimeDefault'}},
            }],
        },
    }
    subprocess.run(command + ['create', '-f', '-'], input=json.dumps(pod), text=True, check=True)
    try:
        subprocess.run(command + ['wait', '--for=condition=Ready', f'pod/{name}', '--timeout=90s'], check=True)
        # exec output goes to the operator terminal rather than the pod's log stream.
        terminal = ['-it'] if sys.stdin.isatty() else ['-i']
        return subprocess.run(command + ['exec', *terminal, name, '--', 'python', '-m', 'app.manage_users', *sys.argv[1:]],
                              check=False).returncode
    finally:
        subprocess.run(command + ['delete', f'pod/{name}', '--wait=false', '--ignore-not-found'], check=False)


if __name__ == '__main__':
    sys.exit(main())
