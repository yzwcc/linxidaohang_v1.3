#!/usr/bin/env bash
set -euo pipefail
P=/agibot/software/agent_cfg/skills.json
test -f "$P" || { echo "未找到 SOC2 技能配置：$P"; exit 1; }
B="$P.bak-$(date +%Y%m%d-%H%M%S)"
cp -a "$P" "$B"
python3 - "$P" <<'PY'
import json,sys,tempfile,os
p=sys.argv[1]
d=json.load(open(p,encoding='utf-8'))
n=0
def walk(x):
 global n
 if isinstance(x,dict):
  if x.get('skill_name')=='start_fixed_line_intro' or x.get('value')=='exhibit_commentary': x['enabled']=False; x['disabled_by']='lingxi-v1.3.8-soc2-fix'; n+=1
  for v in x.values(): walk(v)
 elif isinstance(x,list):
  for v in x: walk(v)
walk(d)
if not n: raise SystemExit('未找到目标技能映射')
f,t=tempfile.mkstemp(dir=os.path.dirname(p)); o=os.fdopen(f,'w',encoding='utf-8'); json.dump(d,o,ensure_ascii=False,indent=2); o.write('\n'); o.close(); os.replace(t,p)
print('已禁用原生误报技能映射:',n)
PY
pkill -HUP -f 'aima-agent-app' 2>/dev/null || true
echo "SOC2 修复完成，备份：$B"
