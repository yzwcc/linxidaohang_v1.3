#!/usr/bin/env bash
set -euo pipefail

[[ "$(id -un)" == "run" ]] || { echo "请使用 run 账号执行本脚本" >&2; exit 1; }

CURRENT="$(readlink -f /opt/sparkrobot/lingxi-nav/current)"
[[ "${CURRENT}" == "/opt/sparkrobot/lingxi-nav/releases/1.0.7-20260901" ]] || {
  echo "当前导航版本不正确：${CURRENT}" >&2
  exit 1
}

sudo sparkrobot-lingxi-internal verify
sudo sparkrobot-lingxi-internal doctor
curl -fsS http://127.0.0.1:3000/health
curl -fsS http://127.0.0.1:3000/api/robots/default/status

for port in 3000 8002 18080 18082 18083; do
  ss -ltn | grep -q ":${port} " || { echo "端口 ${port} 未监听" >&2; exit 1; }
done

echo
echo "[通过] 网页、导航、地图、语音网关和 Java Agent 均已就绪"
echo "下一步：在网页选择地图并录入起点、导航点，然后检查定位。"
