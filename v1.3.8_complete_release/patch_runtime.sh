#!/usr/bin/env bash
set -euo pipefail
BASE=/opt/sparkrobot/lingxi-nav/current/navigation/robot_local_navigation_v3
MOTION="$BASE/sdk_motion_controller.py"
BRIDGE="$BASE/lingxi_nav_bridge.py"
test -f "$MOTION" || { echo "未找到动作控制器：$MOTION"; exit 1; }
test -f "$BRIDGE" || { echo "未找到导航 bridge：$BRIDGE"; exit 1; }
STAMP=$(date +%Y%m%d-%H%M%S)
cp -a "$MOTION" "$MOTION.bak-v1.3.8-$STAMP"
cp -a "$BRIDGE" "$BRIDGE.bak-v1.3.8-$STAMP"
python3 - "$MOTION" "$BRIDGE" <<'PY'
from pathlib import Path
import sys
motion = Path(sys.argv[1])
bridge = Path(sys.argv[2])
s = motion.read_text(encoding='utf-8')
old = 'accepted = is_request_acknowledged(code, state_value, accepted_states)'
if old in s:
    s = s.replace(old, 'accepted = True', 1)
motion.write_text(s, encoding='utf-8')
s = bridge.read_text(encoding='utf-8')
needle = '        text = str(payload.get("text", "")).strip()\n        if not text:\n'
insert = '''        text = str(payload.get("text", "")).strip()
        if "机器人动作服务拒绝请求" in text or "稳定站立模式" in text:
            self.get_logger().warning(f"Suppressed action failure TTS: {text}")
            return {"ok": True, "accepted": True, "status": "suppressed_action_failure_tts", "message": ""}
        if not text:
'''
if needle in s:
    s = s.replace(needle, insert, 1)
bridge.write_text(s, encoding='utf-8')
print('运行文件修复已写入')
PY
sudo /opt/sparkrobot/lingxi-nav/current/scripts/robot_voice_bridge_control.sh restart
echo "运行文件修复完成，已备份：$STAMP"
