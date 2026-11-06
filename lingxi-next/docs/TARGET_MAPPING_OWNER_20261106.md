# Target Independent Mapping Owner - 2026-11-06

Target: `192.168.0.128`. This records an installed mapping owner and web
console, not a captured or accepted map.

## Installed And Verified

- Release `0.1.26-independent-commissioning-20261106` remains installed. The
  independent mapping console is reachable over robot LAN HTTPS on port 3443;
  the API returns HTTP 200 and protected state returns HTTP 401 without pairing.
- `lingxi-next-independent-mapping.service` is active and its private control
  socket reports live GLIM mapping input, `idle`, no session, and a 90-second
  capture limit. The API, AIMDK read-only bridge, and point-cloud preview remain
  active. Shared SLAM, navigation, and voice remain inactive.
- An isolated ARM64 CPython 3.10 venv at
  `/var/lib/lingxi-next/python-runtimes/cp310-v2` passed the installed-owner
  preflight with the existing locked numerical packages and ROS Humble imports.
  The wheel kit SHA256 is
  `4eb8c2dca4f4873546bd7dd3694a60bd759da9eb6570a66dc6274cc90dc1d412`; its
  runtime lock SHA256 is
  `71d74391a6edc9e6e87a4e79ba7f91a60c82daac1b57506fc3041bd9a6915cd9`.
- The source-bound observed ARM64 mapper candidate was uploaded to a private
  staging directory and verified against the current source. Its
  `async-mapping-manifest.json` SHA256 is
  `34442a3c8a125abf59a33734e2594bc9d75821633d7c6071d9ec7a93e9366a252`.
  Installed-input preparation verified the exact 142-file closure and emitted
  manifest SHA256
  `6cc4a7bc0101a76da746458440c82193ffb1315c3e2504ec7c026a42fbb08126`.
- The robot environment file was backed up to
  `/etc/lingxi-next/robot.env.pre-mapping-20261106-b` before selecting the
  verified interpreter and private mapping inputs.

## Acceptance Still Pending

No mapping session has been started, no scan or map was saved, and no movement
command was sent. Mapping, localization, scale/gravity/ground calibration,
navigation, voice, and Android field acceptance remain unverified. The vendor
AIMDK files and original map catalog were not changed. The mapping page awaits
an authenticated operator pairing session and an explicitly started capture.

Next: pair to the console, inspect the live laser point cloud, and have the
operator scan with the external remote. Stop and save only after reviewing the
visible trajectory and coverage; then verify the saved map and fresh-process
reload before any localization or navigation trial.
