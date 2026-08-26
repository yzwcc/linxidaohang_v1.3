# Lingxi Navigation Console Server

HTTPS proxy backend for the WeChat mini program. It keeps robot LAN addresses,
SSH credentials, and bridge control logic out of the mini program bundle.

## Setup

```bash
npm install
cp .env.example .env
npm run dev
```

For official WeChat release, deploy this service behind HTTPS and configure the
domain in the mini program request domain allowlist.

## Web console

After the server starts, open:

```text
http://127.0.0.1:3000/console/
```

Use this browser console as the stable on-site control surface for map
configuration, point editing, task orchestration, service start/restart, and
health checks. The mini program can stay focused on daily customer navigation.

## Environment

- `ROBOT_HOST`: backend transport address. Use `127.0.0.1` in tunnel mode; the actual robot target always comes from the IP saved on the website.
- `ROBOT_BRIDGE_PORT`: Python navigation bridge port, defaults to `18080`.
- `ROBOT_WEB_PORT`: map label server port, defaults to `8002`.
- `ROBOT_SSH_USER` / `ROBOT_SSH_PASSWORD`: used only for whitelisted bridge
  start/stop/restart commands.
- `ROBOT_NAV_HOME`: robot-side navigation package path.
- `DEV_AUTH_BYPASS=true`: allows local development without WeChat code login.

Never commit real secrets. Keep them in `.env` or deployment secret storage.
