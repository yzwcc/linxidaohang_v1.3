"""Private Linux worker. Native dependencies are loaded only inside this process."""
import base64
import ctypes
import os
from pathlib import Path
import resource
import signal
import sys
import traceback


def bind_parent(parent_pid):
    if type(parent_pid) is not int or parent_pid <= 1 or os.getppid() != parent_pid:
        raise RuntimeError('current_terrain_owner_required')
    prctl = ctypes.CDLL(None, use_errno=True).prctl
    prctl.argtypes = [ctypes.c_int, ctypes.c_ulong, ctypes.c_ulong, ctypes.c_ulong, ctypes.c_ulong]
    prctl.restype = ctypes.c_int
    if prctl(1, signal.SIGKILL, 0, 0, 0) != 0:
        raise OSError(ctypes.get_errno(), 'terrain_parent_death_binding_failed')
    # Close the race where the owner dies before the kernel binding takes effect.
    if os.getppid() != parent_pid:
        raise RuntimeError('terrain_owner_changed_during_start')


def main():
    if len(sys.argv) != 2:
        raise ValueError('explicit_terrain_owner_pid_required')
    bind_parent(int(sys.argv[1]))
    resource.setrlimit(resource.RLIMIT_CORE, (0, 0))
    resource.setrlimit(resource.RLIMIT_AS, (2 * 1024**3, 2 * 1024**3))
    resource.setrlimit(resource.RLIMIT_FSIZE, (8 * 1024**2, 8 * 1024**2))
    from .ground import GroundDeclaration, PatchworkSegmenter
    from .occupancy import ClearanceProjection, OctomapProjector
    from .terrain import TerrainBudget, TerrainProducer
    from .terrain_protocol import decode_observations, frame, read_message
    session = None
    # Native diagnostics must never share the framed stdout pipe.
    protocol = os.fdopen(os.dup(sys.stdout.fileno()), 'wb', buffering=0)
    os.dup2(sys.stderr.fileno(), sys.stdout.fileno())
    def send(kind, **value):
        payload = memoryview(frame(dict(version=1, session=session, kind=kind, **value)))
        while payload:
            written = protocol.write(payload)
            if not written:
                raise BrokenPipeError('terrain_protocol_write_failed')
            payload = payload[written:]
    try:
        request = read_message(sys.stdin.buffer)
        if (set(request) != {'version', 'kind', 'session', 'configuration'} or request['version'] != 1
                or request['kind'] != 'configure' or type(request['session']) is not str or len(request['session']) != 32):
            raise ValueError('private_terrain_configuration_required')
        session, config = request['session'], request['configuration']
        budget = TerrainBudget(**config['budget'])
        producer = TerrainProducer(OctomapProjector(Path(config['octomapLibrary']), config['octomapSha256'], config['dependencies']),
            PatchworkSegmenter(Path(config['groundLibrary']), config['groundSha256']),
            ClearanceProjection(**config['projection']), GroundDeclaration(**config['declaration']), budget)
        send('ready')
        while True:
            request = read_message(sys.stdin.buffer)
            if (set(request) != {'version', 'kind', 'session', 'observations', 'sequence', 'receivedAt'}
                    or request['version'] != 1 or request['session'] != session or request['kind'] != 'update'):
                raise ValueError('current_complete_terrain_request_required')
            data = decode_observations(request['observations'], budget)
            result = producer.update(data, sequence=request['sequence'], received_at=request['receivedAt'])
            send('terrain', summary=result.summary(), costs=base64.b64encode(result.fused.costs).decode(),
                 receivedAt=result.received_at, expiresAt=result.expires_at)
    except EOFError:
        return
    except BaseException:
        traceback.print_exc(file=sys.stderr)
        send('failed')
        raise SystemExit(1)
    finally:
        protocol.close()


if __name__ == '__main__':
    main()
