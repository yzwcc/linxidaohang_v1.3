#!/usr/bin/env bash
set -euo pipefail

[[ "$(id -un)" == "run" ]] || { echo "请使用 run 账号执行本脚本" >&2; exit 1; }

CURRENT="$(readlink -f /opt/sparkrobot/lingxi-nav/current)"
[[ "${CURRENT}" == "/opt/sparkrobot/lingxi-nav/releases/1.1.0-20261024-r18-mapping6" ]] || {
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
echo "建图入口：http://$(hostname -I | awk '{print $1}'):3000/console/ ，选择‘网页建图’；移动由外置遥控器负责。"
echo "下一步：检查激光雷达与 IMU 在线，外置遥控器移动建图，网页点击结束并保存，再到地图页验证地图。"
