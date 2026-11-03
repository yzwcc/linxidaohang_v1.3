"""Bounded isolated service endpoint diagnosis, never a middleware or readiness fix."""
import argparse
from dataclasses import asdict
import hashlib
import json
import os
from pathlib import Path
import platform
import re
import subprocess
import time

ROOT = Path(__file__).resolve().parents[1]
MARKER = 'LINGXI_SERVICE_DIAGNOSTIC '
PACKAGES = {'ros-humble-rmw-fastrtps-shared-cpp': '6.2.10', 'ros-humble-fastrtps': '2.6.12',
            'ros-humble-nav2-lifecycle-manager': '1.1.20'}
SERVICE = re.compile(r'/lingxi_offline_[0-9a-f]{32}/(?:planner_server|controller_server|bt_navigator|velocity_smoother)/change_state')
READER_PRESENCE = {0: 'FAILURE', 1: 'MAYBE', 2: 'YES', 3: 'GONE'}
RMW_RET_TIMEOUT = 2


def environment():
    expected = dict(ROS_DISTRO='humble', ROS_LOCALHOST_ONLY='1', ROS_DOMAIN_ID='174',
                    RMW_IMPLEMENTATION='rmw_fastrtps_cpp')
    if (platform.system() != 'Linux' or platform.machine() != 'x86_64'
            or set(os.listdir('/sys/class/net')) != {'lo'}
            or any(os.environ.get(key) != value for key, value in expected.items())):
        raise ValueError('separate_loopback_x86_humble_required')
    versions = {}
    for package, expected_version in PACKAGES.items():
        value = subprocess.check_output(['dpkg-query', '-W', '-f=${Version}', package],
                                        text=True, timeout=5).strip()
        if value.split('-', 1)[0] != expected_version:
            raise ValueError('exact_diagnostic_abi_versions_required')
        versions[package] = value
    return versions


def records_from_log(path):
    if path.stat().st_size > 2 * 1024 * 1024:
        raise ValueError('bounded_diagnostic_log_required')
    records = []
    for line in path.read_text(encoding='utf-8').splitlines():
        if MARKER not in line:
            continue
        value = json.loads(line.split(MARKER, 1)[1])
        if (type(value) is not dict or value.get('schema') != 1
                or not SERVICE.fullmatch(value.get('service', ''))
                or value.get('operation') not in ('request', 'response')
                or type(value.get('sequence')) is not int):
            raise ValueError('typed_service_diagnostic_record_required')
        records.append(value)
        if len(records) > 512:
            raise ValueError('bounded_diagnostic_records_required')
    return records


def build(output):
    environment()
    output = output.resolve(strict=True) / 'native-build'
    output.mkdir(mode=0o700)
    prefix = Path('/opt/ros/humble')
    packages = ('rmw', 'rmw_fastrtps_shared_cpp', 'rcutils', 'rcpputils',
                'rosidl_runtime_c', 'rosidl_typesupport_interface', 'fastcdr', 'fastrtps')
    command = ['g++', '-std=c++17', '-O2', '-g', '-fPIC', '-shared', '-Wall', '-Wextra', '-Werror',
        str(ROOT / 'tools/native-service-diagnostic/service_diagnostic.cpp'),
        '-o', str(output / 'liblingxi_service_diagnostic.so')]
    command += ['-I' + str(prefix / 'include' / name) for name in packages]
    command += ['-L' + str(prefix / 'lib'), '-Wl,-rpath,' + str(prefix / 'lib'), '-Wl,-z,defs',
                '-lrmw_fastrtps_shared_cpp', '-lfastrtps', '-lfastcdr', '-ldl', '-pthread']
    subprocess.run(command, check=True, timeout=60)


def summarize(records):
    requests, responses = {}, {}
    for value in records:
        collection = requests if value['operation'] == 'request' else responses
        key = value['service'], value['sequence']
        if key in collection:
            raise ValueError('unique_service_sequence_required')
        collection[key] = value
    if not requests or not responses:
        raise ValueError('actual_request_and_response_observation_required')
    timeouts = []
    for key, response in responses.items():
        request = requests.get(key)
        if request is None:
            raise ValueError('correlated_service_request_required')
        if response['returnCode'] != 0:
            after = response['after']
            presence = after['rmwReaderPresence']
            if type(presence) is not int or presence not in READER_PRESENCE:
                raise ValueError('known_server_reader_presence_required')
            timeouts.append(dict(service=key[0], sequence=key[1], returnCode=response['returnCode'],
                clientReaderIdentityMatches=(request['declaredReader'] == request['actualReader']
                    == response['relatedReader']),
                clientWriterIdentityMatches=request['declaredWriter'] == request['actualWriter'],
                clientMatchedRequestCount=request['matchedRequestCount'],
                clientMatchedResponseCount=request['matchedResponseCount'],
                serverMatchedListAvailable=after['matchedListAvailable'],
                serverRelatedReaderMatched=after['relatedReaderMatched'],
                serverRmwReaderPresence=presence,
                serverRmwReaderPresenceName=READER_PRESENCE[presence],
                before=response['before'], after=after))
    mechanism_established = bool(timeouts) and all(
        item['returnCode'] == RMW_RET_TIMEOUT and item['clientReaderIdentityMatches']
        and item['serverRmwReaderPresenceName'] == 'MAYBE'
        and item['serverRelatedReaderMatched'] is False for item in timeouts)
    return dict(requestCount=len(requests), responseCount=len(responses),
                unobservedResponseCount=len(set(requests) - set(responses)),
                unsuccessfulResponses=timeouts, failureMechanismEstablished=mechanism_established,
                rootCauseEstablished=False)


def wait(service, predicate, deadline):
    while True:
        service.tick()
        state = service.state()
        if predicate(state):
            return state
        if time.monotonic() >= deadline:
            raise TimeoutError('bounded_diagnostic_observation_expired')
        time.sleep(.005)


def run(output):
    versions = environment()
    output = output.resolve(strict=True)
    library = output / 'native-build/liblingxi_service_diagnostic.so'
    if (os.environ.get('LD_PRELOAD') != str(library) or not library.is_file()
            or os.environ.get('LINGXI_NAV2_SERVICE_DIAGNOSTIC') != 'isolated_humble_fastdds_2_6_12'):
        raise ValueError('explicit_new_private_diagnostic_library_required')
    trial = output / 'runtime'
    trial.mkdir(mode=0o700)
    import rclpy
    import offline_nav2_service as product
    rclpy.init(args=[])
    coordinator = rclpy.create_node('offline_diagnostic_owner', use_global_arguments=False)
    service = product.Service('open', trial)
    observations, retirements, error, final_state = [], [], None, None
    try:
        for index in range(12):
            current = service.state()
            service.request(dict(id=f'{index * 2 + 1:032x}', action='start', session=service.session,
                identity=asdict(service.identity), expectedEpoch=current['ticket']['epoch'] if current['ticket'] else None,
                taskId=f'diagnostic-{index}', pointId='target', goal=dict(x=.9, y=0., yaw=0.)))
            state = wait(service, lambda value: value['phase'] in ('running', 'failed'), time.monotonic() + 7.)
            observations.append(dict(generation=index, state=state))
            failed = state['phase'] != 'running' or state['requiresStop']
            service.request(dict(id=f'{index * 2 + 2:032x}', action='stop', session=service.session,
                identity=asdict(service.identity), expectedEpoch=state['ticket']['epoch'],
                taskId=state['ticket']['task_id']))
            retired = wait(service, lambda value: value['outputQuiescent'], time.monotonic() + 3.)
            retirements.append(dict(generation=index, state=retired))
            if retired['phase'] != 'idle' or retired['requiresStop']:
                raise RuntimeError('diagnostic_generation_retirement_failed')
            if failed:
                error = 'RuntimeError:diagnostic_start_failed:' + state['detail']
                break
    except Exception as caught:
        error = type(caught).__name__ + ':' + str(caught)
    finally:
        try:
            try:
                final_state = service.state()
            finally:
                service.close()
        finally:
            try:
                coordinator.destroy_node()
            finally:
                rclpy.shutdown()
    logs = sorted(trial.glob('*/nav2.log'))
    records = []
    try:
        records = [record for path in logs for record in records_from_log(path)]
        summary = summarize(records)
    except (ValueError, KeyError, TypeError) as caught:
        summary = dict(rootCauseEstablished=False, diagnosticValid=False)
        error = error or type(caught).__name__ + ':' + str(caught)
    sources = ('tools/nav2_service_diagnostic.py', 'tools/native-service-diagnostic/service_diagnostic.cpp',
               'tools/nav2-offline-service-isolated.sh',
               'tools/offline_nav2_service.py', 'robot-navigation/lingxi_navigation/lifecycle_startup.py',
               'robot-navigation/nav2_offline.launch.py', 'robot-navigation/lingxi_navigation/nav2_profile.py')
    report = dict(schema=1, purpose='isolated_lifecycle_service_diagnosis_not_robot_acceptance',
        versions=versions, generations=observations, retirements=retirements, finalState=final_state,
        diagnostic=summary, records=records, error=error,
        startupTimeoutSeconds=service.runtime.startup_timeout,
        librarySha256=hashlib.sha256(library.read_bytes()).hexdigest(),
        sources={name: hashlib.sha256((ROOT / name).read_bytes()).hexdigest() for name in sources},
        logs={str(path.relative_to(output)): hashlib.sha256(path.read_bytes()).hexdigest() for path in logs},
        physicalMotionSent=False, navigationReady=False, middlewareChanged=False, transitionReplay=False)
    with (output / 'report.json').open('x', encoding='utf-8') as stream:
        json.dump(report, stream, indent=2, allow_nan=False)
    print(json.dumps(dict(report=str(output / 'report.json'), diagnostic=summary, error=error), allow_nan=False))
    return report


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--check', action='store_true')
    parser.add_argument('--output', type=Path)
    parser.add_argument('--build', type=Path)
    args = parser.parse_args()
    if args.check:
        print(json.dumps(environment()))
    elif args.build is not None:
        build(args.build)
    elif args.output is None:
        parser.error('--output required')
    else:
        report = run(args.output)
        raise SystemExit(bool(report['error'] or any(item['state']['requiresStop']
                                                    for item in report['generations'])))
