"""Synthetic process peer only: no ROS imports, sensors or robot IO."""
import argparse
from pathlib import Path
import signal
import socket
import subprocess
import sys
import time

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from offline_nav2_worker import Channel, Ticket

parser = argparse.ArgumentParser()
for name in ('fd', 'task', 'epoch', 'scenario'):
    parser.add_argument('--' + name, required=True)
args = parser.parse_args()
stream = socket.socket(fileno=int(args.fd))
channel = Channel(stream, Ticket(args.task, args.epoch))
while channel.receive() is None:
    time.sleep(.001)
# A descendant deliberately ignores graceful signals; parent must retire the group.
child = subprocess.Popen([sys.executable, '-c',
    'import signal,time;signal.signal(signal.SIGINT,signal.SIG_IGN);signal.signal(signal.SIGTERM,signal.SIG_IGN);time.sleep(30)'])
signal.signal(signal.SIGINT, signal.SIG_IGN)
signal.signal(signal.SIGTERM, signal.SIG_IGN)
if args.scenario == 'worker-exit':
    raise SystemExit(2)
if args.scenario == 'startup-block':
    time.sleep(.4)
channel.send('accepted', {})
sequence = 0
while True:
    for _ in range(32):
        if channel.receive() is None:
            break
    sequence += 1
    channel.send('velocity', dict(sequence=sequence, generated=time.monotonic(), forward=0., lateral=0., yaw=0.))
    time.sleep(.4 if args.scenario == 'running-block' else .02)
