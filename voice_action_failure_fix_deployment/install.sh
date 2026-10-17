#!/usr/bin/env bash
set -euo pipefail
CFG=/agibot/software/agent/bin/cfg/skills.json
B=/var/lib/sparkrobot/action-failure-fix/backups/skills.json.$(date +%Y%m%d-%H%M%S)
test -f "$CFG" || exit 1
mkdir -p "$(dirname "$B")"
cp -a "$CFG" "$B"
python3 - "$CFG" <<'PY'
import json,sys
p=sys.argv[1]; d=json.load(open(p,encoding='utf-8')); n=0
def w(x):
 global n
 if isinstance(x,dict):
  if x.get('value')=='exhibit_commentary' or x.get('skill_name')=='start_fixed_line_intro' or x.get('skillName')=='start_fixed_line_intro': x['enabled']=False; n+=1
  for v in x.values(): w(v)
 elif isinstance(x,list):
  for v in x: w(v)
w(d)
if not n: raise SystemExit('未找到目标技能映射')
open(p,'w',encoding='utf-8').write(json.dumps(d,ensure_ascii=False,indent=2)+'\n')
print('已禁用目标技能映射:',n)
PY
systemctl restart lingxi-java-agent-robot.service 2>/dev/null || true
echo "修复完成，备份：$B"
