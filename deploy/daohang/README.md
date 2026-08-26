# daohang.sparkrobot.top 部署说明

目标：让客户通过 `https://daohang.sparkrobot.top` 打开导航控制台，并且控制台 API 能访问现场机器人。

## 当前关键事实

- `daohang.sparkrobot.top` 当前由公网服务器和 Caddy 提供 HTTPS 服务。
- 机器人地址是运行时填写的局域网地址，公网服务通过反向 SSH 隧道访问。
- 控制台不是纯静态网页，按钮和地图功能依赖 `server/` 后端代理机器人 `18080` 和 `8002`。

因此，正式部署必须满足其中一种网络条件：

1. 后端部署在能访问机器人局域网的机器上，并给它配置 HTTPS 域名。
2. 机器人或现场电脑主动建立内网穿透/反向隧道到公网，再由域名反向代理过去。

## 推荐稳定方案

使用一台公网 Linux 服务器：

- DNS：把 `daohang.sparkrobot.top` A 记录指向公网服务器 IP。
- 服务器：运行本项目 `server/`。
- HTTPS：用 Caddy 或 Nginx 自动签证书并反向代理到 `127.0.0.1:3000`。
- 机器人网络：机器人主动建立反向 SSH 隧道，云端使用 `127.0.0.1:18080`、`127.0.0.1:18002` 和 `127.0.0.1:10022`。

## 服务器环境变量

在服务器的 `/opt/lingxi-nav-console/server/.env` 中配置：

```env
PORT=3000
HOST=127.0.0.1
PUBLIC_BASE_URL=https://daohang.sparkrobot.top

ROBOT_CONNECTION_MODE=tunnel
ROBOT_HOST=127.0.0.1
ROBOT_BRIDGE_PORT=18080
ROBOT_WEB_PORT=18002
ROBOT_SSH_PORT=10022
ROBOT_MAP_ROOT=/agibot/data/var/MapManagerModule/

ROBOT_SSH_USER=agi
ROBOT_SSH_PASSWORD=
ROBOT_SYSTEM_SSH_USER=run
ROBOT_SYSTEM_SSH_PASSWORD=
ROBOT_SYSTEM_SSH_PORT=10022
ROBOT_NAV_HOME=/home/agi/robot_local_navigation_v3
JAVA_AGENT_EXECUTION_MODE=robot
ROBOT_JAVA_AGENT_HOME=/home/run/lingxi_java_agent
JAVA_AGENT_AUTO_START=true

TOKEN_SECRET=请换成足够长的随机字符串
DEV_AUTH_BYPASS=true
```

密码不要提交进仓库，只放服务器运行环境。

## 部署命令

```bash
cd /opt/lingxi-nav-console/server
npm ci
npm run build
npm test
sudo cp ../deploy/daohang/lingxi-nav-console.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now lingxi-nav-console
sudo systemctl status lingxi-nav-console
```

## Caddy 反向代理

安装 Caddy 后，把 `deploy/daohang/Caddyfile` 合并到 `/etc/caddy/Caddyfile`：

```bash
sudo caddy validate --config /etc/caddy/Caddyfile
sudo systemctl reload caddy
```

验证：

```bash
curl -i https://daohang.sparkrobot.top/health
curl -i https://daohang.sparkrobot.top/console/
```

## 如果继续使用 Vercel

Vercel 可以托管 HTTPS 域名，但不能直接访问机器人局域网 `192.168.0.198`。要继续使用 Vercel，必须先准备一个公网可访问的机器人代理地址，然后让 Vercel 页面/API 转发到这个代理地址。

现场交付更推荐“公网服务器 + Caddy + VPN/穿透”，因为它能长期稳定运行后端、SSH 运维接口和地图图片代理。
