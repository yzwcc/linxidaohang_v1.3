"""One parent-linked service for mapping and on-demand localization; no movement."""
import argparse
from contextlib import ExitStack
import json
from pathlib import Path
import selectors
import signal
import sys
import threading

from mapping_service import MappingSocketServer, create_manager as create_mapping
from localization_service import LocalizationServiceServer, create_manager as create_localization
from lingxi_slam.activity_lease import ActivityGate


def serve(mapping_endpoint, localization_endpoint, mapping, localization, *, parent_input=None, status=None):
    if Path(mapping_endpoint) == Path(localization_endpoint):
        raise ValueError("distinct_slam_service_endpoints_required")
    if (not isinstance(mapping.activity_gate, ActivityGate) or not isinstance(localization.activity_gate, ActivityGate)
            or mapping.maps != localization.activity_gate.path.parent
            or mapping.activity_gate.path != localization.activity_gate.path
            or mapping.input_mode != localization.input_mode):
        raise ValueError("shared_catalog_activity_gate_and_input_required")
    stopping = threading.Event()
    previous = {}
    try:
        with ExitStack() as stack:
            servers = [stack.enter_context(MappingSocketServer(mapping_endpoint, mapping)),
                       stack.enter_context(LocalizationServiceServer(localization_endpoint, localization))]
            previous = {value: signal.signal(value, lambda *_: stopping.set())
                        for value in (signal.SIGINT, signal.SIGTERM)}
            with selectors.DefaultSelector() as selector:
                for server in servers:
                    selector.register(server, selectors.EVENT_READ, server)
                if parent_input is not None:
                    selector.register(parent_input, selectors.EVENT_READ, None)
                if status is not None:
                    status({"service": "lingxi-slam", "event": "listening"})
                try:
                    while not stopping.is_set():
                        for key, _ in selector.select(.05):
                            if key.data is None:
                                stopping.set()
                                break
                            if not stopping.is_set():
                                key.data._handle_request_noblock()
                finally:
                    # Revoke admission to both managers before closing either socket.
                    with mapping.lock:
                        mapping.closed = True
                        if mapping.current:
                            mapping._revoke(mapping.current["owner"])
                    with localization.lock:
                        localization.closed = True
                        if localization.current:
                            localization.current["stop"].set()
    finally:
        for value, handler in previous.items():
            signal.signal(value, handler)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("config", "maps", "runs", "socket", "localization-socket"):
        parser.add_argument("--" + name, type=Path, required=True)
    parser.add_argument("--parent-stdin", action="store_true")
    args = parser.parse_args()
    mapping = create_mapping(args.config, args.maps)
    localization = create_localization(args.config, args.maps, args.runs)
    serve(args.socket, args.localization_socket, mapping, localization,
          parent_input=sys.stdin.buffer if args.parent_stdin else None,
          status=lambda value: print(json.dumps(value), flush=True))


if __name__ == "__main__":
    main()
