"""Audit core commissioning scope without implying independent-stack delivery."""
import json


CORE_FILES = frozenset((
    'web/index.html', 'api/index.mjs', 'bridge/aimdk_ros_bridge.py',
    'agent/lingxi-agent.jar', 'agent/lib/linksoul-agentsdk-1.5.0-SNAPSHOT.jar',
    'deploy/install.sh', 'deploy/rollback.sh', 'deploy/transaction.sh',
    'deploy/release-scope.json', 'runtime/node/bin/node', 'runtime/java/bin/java',
))
INCLUDED = ['web-console', 'management-api', 'aimdk-bridge', 'linksoul-agent', 'node-arm64', 'java-arm64']
NOT_INCLUDED = ['independent-glim-mapping', 'seeded-localization', 'octomap-clearance',
                'nav2-runtime', 'guarded-motion-handoff']
EXTERNAL = ['vendor-aimdk', 'robot-ros', 'private-cloud-credentials', 'robot-specific-configuration']


def verify_scope(status, scope_bytes, files):
    scope = json.loads(scope_bytes)
    if (type(status) is not dict or status.get('classification') != 'commissioning'
            or status.get('productionAccepted') is not False or status.get('scope') != scope
            or type(scope) is not dict or type(scope.get('schemaVersion')) is not int
            or scope.get('schemaVersion') != 1
            or scope.get('profile') != 'core-services-vendor-adapter'
            or scope.get('completeProduct') is not False
            or scope.get('independentStackIncluded') is not False
            or scope.get('included') != INCLUDED or scope.get('notIncluded') != NOT_INCLUDED
            or scope.get('requiresExternal') != EXTERNAL):
        raise ValueError('explicit_incomplete_core_commissioning_scope_required')
    if not CORE_FILES.issubset(files):
        raise ValueError('declared_core_release_components_missing')
    return scope
