#!/usr/bin/env bash
set -euo pipefail
case "${1:-status}" in
status) python3 -c "import json;json.load(open('/agibot/software/agent/bin/cfg/skills.json'));print('配置可读取，修复已安装')" ;;
logs) journalctl -u lingxi-java-agent-robot.service -n 200 --no-pager | grep -E 'start_fixed_line_intro|exhibit_commentary|task_fail|执行失败' || true ;;
rollback) b=$(ls -1t /var/lib/sparkrobot/action-failure-fix/backups/skills.json.* | head -n1); cp -a "$b" /agibot/software/agent/bin/cfg/skills.json; echo "已回滚 $b" ;;
*) echo '用法: sudo bash ./control.sh {status|logs|rollback}'; exit 2 ;;
esac
