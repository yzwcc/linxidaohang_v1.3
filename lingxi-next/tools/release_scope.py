"""Audit commissioning scopes without implying production acceptance."""
import ast
import json
from pathlib import Path, PurePosixPath


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

INDEPENDENT_FILES = frozenset((
    'deploy/independent-scope.json', 'independent/manifest.json',
    'independent/runtime/mapping-main.mjs', 'independent/runtime/localization-main.mjs',
    'independent/runtime/slam-main.mjs',
    'independent/assets/mapping/api/main.mjs', 'independent/assets/mapping/web/index.html',
    'independent/assets/slam/api/main.mjs', 'independent/assets/slam/web/index.html',
    'independent/source/robot-runtime/python/mapping_owner.py',
    'independent/source/robot-runtime/python/localization_owner.py',
    'independent/source/robot-runtime/python/managed_mapping.py',
    'independent/source/robot-runtime/python/managed_slam.py',
    'independent/source/tools/run_glim_supervised_replay.py',
    'independent/source/tools/run_glim_mapping_trial.py',
    'independent/source/tools/arm64_python_runtime.py',
    'independent/source/tools/check_installed_slam_python.py',
    'independent/source/tools/prepare_installed_mapping_inputs.py',
    'independent/source/robot-navigation/bridge/physical_nav_bridge.py',
    'independent/source/robot-navigation/bridge/nav2_lease_guard.py',
    'independent/source/robot-slam/lingxi_slam/activity_lease.py',
    'independent/source/robot-navigation/lingxi_navigation/nav2_producer.py',
    'independent/source/robot-navigation/lingxi_navigation/nav2_action.py',
    'independent/source/robot-navigation/lingxi_navigation/lifecycle_startup.py',
    'independent/source/robot-navigation/lingxi_navigation/physical_maps.py',
    'independent/source/robot-navigation/lingxi_navigation/localization_guard.py',
    'independent/source/robot-navigation/lingxi_navigation/owned_inputs.py',
    'independent/source/robot-runtime/python/live_localization_ros.py',
    'independent/source/robot-navigation/nav2_physical.launch.py',
    'independent/source/robot-navigation/native-ground/CMakeLists.txt',
    'independent/source/robot-slam/native-acquisition-output/acquisition.cpp',
    'independent/source/robot-slam/native-acquisition-output/acquisition.h',
    'independent/source/robot-slam/native-acquisition-output/acquisition_contract.cpp',
    'independent/source/robot-slam/native-acquisition-output/CMakeLists.txt',
    'independent/source/robot-slam/native-acquisition-output/exports.map',
    'independent/source/robot-slam/native-point-output/point_output.cpp',
    'independent/source/docs/slam-point-arm64-target-evidence.json',
    'independent/source/tools/glim-full-mapping/submapping-eof.patch',
    'independent/source/tools/glim-observations/observation_capture.cpp',
    'deploy/lingxi-next-independent-mapping.service',
    'deploy/lingxi-next-independent-slam.service',
    'deploy/lingxi-next-navigation.service',
))
INDEPENDENT_INCLUDED = ['independent-glim-mapping', 'seeded-localization', 'octomap-clearance',
                         'nav2-runtime', 'guarded-motion-handoff']
INDEPENDENT_NOT_INCLUDED = ['arm64-native-execution', 'sensor-topic-and-tf-calibration',
                             'map-save-reload-field-acceptance',
                             'physical-navigation-and-estop-acceptance',
                             'cloud-voice-and-android-device-acceptance']
INDEPENDENT_EXTERNAL = ['ros-humble', 'vendor-aimdk', 'verified-glim-native-dependencies',
                        'robot-lidar-topics-and-tf', 'robot-footprint-and-calibration']
BOUND_SOURCE_MODULES = (
    'robot-runtime/python/async_mapping_bundle.py',
    'robot-runtime/python/acquisition_bundle.py',
    'robot-runtime/python/controlled_localization.py',
    'robot-runtime/python/live_acquisition.py',
    'robot-runtime/python/terrain_bundle.py',
    'tools/run_glim_supervised_replay.py',
)


def verify_bound_source_references(captured, files):
    """Check literal source paths used by native receipt/hash gates, without imports."""
    required = set()
    prefix = 'independent/source/'
    for module in BOUND_SOURCE_MODULES:
        name = prefix + module
        if name not in captured:
            raise ValueError('native_source_verifier_missing:' + name)
        for node in ast.walk(ast.parse(captured[name], filename=name)):
            if not isinstance(node, ast.Constant) or not isinstance(node.value, str):
                continue
            value = node.value
            path = PurePosixPath(value)
            if (value.startswith(('tools/', 'robot-runtime/', 'robot-slam/', 'robot-navigation/', 'docs/'))
                    and path.suffix in ('.py', '.cpp', '.h', '.hpp', '.txt', '.json', '.patch', '.cmake')
                    and path.as_posix() == value and '..' not in path.parts):
                required.add(prefix + value)
    missing = required - set(files)
    if missing:
        raise ValueError('native_source_reference_missing:' + sorted(missing)[0])
    return len(required)


def verify_staged_source_references(root):
    root = Path(root)
    files = {file.relative_to(root).as_posix() for file in root.rglob('*') if file.is_file()}
    captured = {}
    for module in BOUND_SOURCE_MODULES:
        name = 'independent/source/' + module
        if name in files:
            captured[name] = (root / name).read_bytes()
    return verify_bound_source_references(captured, files)


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


def verify_independent_scope(status, scope_bytes, files):
    """Verify independent commissioning contents without certifying field use."""
    scope = json.loads(scope_bytes)
    if (type(status) is not dict or status.get('classification') != 'commissioning'
            or status.get('productionAccepted') is not False
            or status.get('independentStackIncluded') is not True
            or type(scope) is not dict or type(scope.get('schemaVersion')) is not int
            or scope.get('schemaVersion') != 1 or status.get('independent') != scope
            or scope.get('profile') != 'independent-navigation-commissioning'
            or scope.get('completeProduct') is not False
            or scope.get('independentStackIncluded') is not True
            or scope.get('included') != INDEPENDENT_INCLUDED
            or scope.get('notIncluded') != INDEPENDENT_NOT_INCLUDED
            or scope.get('requiresExternal') != INDEPENDENT_EXTERNAL
            or not INDEPENDENT_FILES.issubset(files)):
        raise ValueError('declared_independent_commissioning_components_missing')
    return scope


if __name__ == '__main__':
    import argparse
    parser = argparse.ArgumentParser(description='Check staged native source references before compression')
    parser.add_argument('--stage', type=Path, required=True)
    args = parser.parse_args()
    print(json.dumps({'verifiedNativeSourceReferences': verify_staged_source_references(args.stage)}))
