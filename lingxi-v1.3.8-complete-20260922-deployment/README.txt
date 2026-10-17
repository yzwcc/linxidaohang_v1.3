V1.3.8 完整部署包（2026-09-22）

基于 2026-09-21 r9 完整包，包含 AIMDK、导航、知识库、灵创和网页控制台，并增加 SOC2 原生动作误报修复。

主包安装完成后，在 SOC2 板执行：
chmod +x patch_soc2_skill.sh
sudo bash ./patch_soc2_skill.sh

脚本只修改 /agibot/software/agent_cfg/skills.json，并自动备份。
