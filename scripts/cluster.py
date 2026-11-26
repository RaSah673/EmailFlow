"""Common explicit target for local EmailFlow Kubernetes tools."""
import os
import re
from pathlib import Path


def kubectl():
    name = os.environ.get('EMAILFLOW_CLUSTER_NAME', 'emailflow')
    if not re.fullmatch(r'emailflow(?:-[a-z0-9]+)*', name):
        raise SystemExit('Use a dedicated Kind cluster named emailflow or emailflow-<suffix>.')
    os.environ.setdefault('KUBECONFIG', str(Path(__file__).resolve().parents[1] / '.emailflow-kubeconfig'))
    return ['kubectl', '--context', f'kind-{name}', '-n', 'emailflow']
