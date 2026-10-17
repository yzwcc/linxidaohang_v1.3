# 灵心知识库检索与语音接入部署包

本包包含两部分：独立 Retriever Proxy 服务，以及已验证的 Java Agent 知识库接入更新。它不包含导航、地图、AIMDK 或直播项目配置。

完整部署步骤见 `docs/灵心知识库独立部署教程.md`。

## 功能

- 通过 `POST /search` 调用灵心 `agent_kb` 检索接口。
- 使用灵心要求的 AK/SK HMAC-SHA256 请求签名。
- `GET /health` 健康检查。
- `kb-search` 命令行查询。
- 独立 systemd 服务，监听机器人本机 `127.0.0.1:18084`。
- Java Agent 按“本地问答 -> 灵心知识库 -> 大模型”顺序回答普通问题。
- Java Agent 更新前自动备份，可从备份 JAR 回滚。

## 在已部署灵犀导航的机器人上安装

在机器人 `run` 账号执行：

```bash
cd /home/run/sparkrobot-lingxi-kb-1.0.2-20260902
sudo ./install.sh --from-lingxi --agent-code AI_AGENT_你的智能体Code
sudo ./install_voice_agent.sh
```

`--from-lingxi` 只读取 `/etc/sparkrobot/lingxi-nav/lingxi-nav.properties` 的 `lingxi.appId/appKey/appSecret`，不会修改该文件。智能体 Code 可从灵心平台或现有 Java 日志中的 `AI_AGENT_...` 确认。知识库服务必须先安装，再安装 `install_voice_agent.sh`。

如果不希望复用现有灵心配置，也可以准备只包含以下字段的临时配置文件：

```text
RETRIEVER_APP_ID=app_xxx
RETRIEVER_APP_KEY=ak_xxx
RETRIEVER_APP_SECRET=sk_xxx
RETRIEVER_AGENT_CODE=AI_AGENT_xxx
```

然后执行：

```bash
sudo ./install.sh --config ./retriever.env
```

安装后检查：

```bash
sudo systemctl status sparkrobot-lingxi-kb.service --no-pager
curl -fsS http://127.0.0.1:18084/health
kb-search "这里填写要检索的问题"
```

## 查询接口

```bash
curl -X POST http://127.0.0.1:18084/search \
  -H 'Content-Type: application/json' \
  -d '{"query":"这里填写要检索的问题","top_k":5}'
```

返回上游 `data` 中的文档片段、问答、分数和元数据。服务端会固定使用配置中的 Agent Code，客户端不能覆盖，避免检索到其他智能体的知识库。

## 语音接入回滚

安装语音接入时会把旧 JAR 保存到：

```text
/var/lib/sparkrobot/lingxi-kb/agent-backups/
```

查看备份并回滚：

```bash
ls -lh /var/lib/sparkrobot/lingxi-kb/agent-backups/
sudo ./install_voice_agent.sh --rollback /var/lib/sparkrobot/lingxi-kb/agent-backups/robot-local-navigation-java-all.实际备份文件名.jar
```

回滚只重启 `lingxi-java-agent-robot.service`，不重启导航服务。

## 运维

```bash
sudo systemctl restart sparkrobot-lingxi-kb.service
sudo journalctl -u sparkrobot-lingxi-kb.service -n 100 --no-pager
sudo /usr/local/sbin/sparkrobot-lingxi-kb doctor
```

密钥保存在 `/etc/sparkrobot/lingxi-kb/retriever.env`，权限为 `run:run 0600`，不会进入安装包或日志。卸载知识库程序但保留配置、状态和日志：

```bash
sudo /usr/local/sbin/sparkrobot-lingxi-kb uninstall
```
