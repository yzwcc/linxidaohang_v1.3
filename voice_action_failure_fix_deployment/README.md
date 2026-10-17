# 动作成功后误播失败语音修复

```bash
cd /home/run/lingxi-action-failure-fix-1.0.0
chmod +x install.sh control.sh
sudo bash ./install.sh
sudo bash ./control.sh status
```

安装器会备份并禁用 `exhibit_commentary -> start_fixed_line_intro`，不修改导航、地图、AIMDK、知识库和密钥。回滚：`sudo bash ./control.sh rollback`。
