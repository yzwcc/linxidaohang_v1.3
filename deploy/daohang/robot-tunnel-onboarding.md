# 机器人公网接入说明

公网部署采用“现场网关主动连接服务器”的反向隧道模式。客户在网页里填写机器人 IP 后，现场网关会读取这个 IP，并把云端固定端口转发到该机器人。

## 当前云端固定端口

阿里云服务器后端固定访问：

```text
导航 bridge：http://127.0.0.1:18080
地图服务：http://127.0.0.1:18002
机器人 SSH：127.0.0.1:10022
```

现场网关把客户填写的机器人 IP 映射到云端：

```text
机器人 <网页填写IP>:18080 -> 云端 127.0.0.1:18080
机器人 <网页填写IP>:8002  -> 云端 127.0.0.1:18002
机器人 <网页填写IP>:22    -> 云端 127.0.0.1:10022
```

## 新机器人接入步骤

1. 确认机器人能访问公网服务器 `47.82.118.118:22`。
2. 在机器人 `agi` 用户下生成专用 SSH key：

```bash
mkdir -p ~/.ssh
chmod 700 ~/.ssh
ssh-keygen -t ed25519 -N '' -f ~/.ssh/daohang_server_ed25519 -C 'daohang-robot-tunnel'
cat ~/.ssh/daohang_server_ed25519.pub
```

3. 把公钥加入服务器 `robot-tunnel` 用户：

```bash
sudo -u robot-tunnel mkdir -p /home/robot-tunnel/.ssh
echo '<机器人公钥>' >> /home/robot-tunnel/.ssh/authorized_keys
chown -R robot-tunnel:robot-tunnel /home/robot-tunnel/.ssh
chmod 700 /home/robot-tunnel/.ssh
chmod 600 /home/robot-tunnel/.ssh/authorized_keys
```

4. 在每台待接入机器人的 `agi` 用户下安装动态网关脚本和 systemd 服务。脚本默认只在网页填写的 IP 属于本机时接管公网端口，因此多台已部署机器人同时在线时，只有网页选中的机器人会连接。IP 不来自预设列表。

```bash
cp deploy/daohang/field-gateway.sh /home/agi/daohang_reverse_tunnel.sh
chmod 700 /home/agi/daohang_reverse_tunnel.sh
sudo cp deploy/daohang/daohang-reverse-tunnel.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now daohang-reverse-tunnel.service
sudo systemctl status daohang-reverse-tunnel.service --no-pager
```

用户级每分钟 watchdog 可以保留为后备，但 systemd 必须是主要的开机启动方式。

只有使用独立现场网关设备、并明确需要它转发同一局域网内其他机器人时，才设置 `ALLOW_REMOTE_TARGET=true`。客户无电脑方案不要设置该变量，每台机器人直接运行默认模式即可。

默认机器人模式仍用网页 IP 判断由哪台机器人接管，但接管后将 SSH、导航和地图转发到该机器人自己的 `127.0.0.1`，避免机器人换 Wi-Fi、热点或网卡地址后出现回连自身地址的数据通道冻结。`ALLOW_REMOTE_TARGET=true` 时才直接转发网页填写的局域网 IP。

公网服务器还必须安装 `60-robot-tunnel-keepalive.conf` 到 `/etc/ssh/sshd_config.d/` 并重载 SSH。机器人端每 5 秒检查云端连接，公网端每 10 秒检查机器人连接；热点切换留下的半开会话会自动退出并释放固定端口。

5. 配置导航计算单元到语音计算单元的免密运维通道，并安装语音栈重启脚本：

```bash
# 在导航计算单元的 agi 用户下执行
ssh-keygen -t ed25519 -N '' -f /home/agi/.ssh/daohang_soc2_ed25519
ssh-copy-id -i /home/agi/.ssh/daohang_soc2_ed25519.pub run@10.0.1.42
cp deploy/daohang/restart_daohang_voice_stack.sh /home/agi/
chmod 700 /home/agi/restart_daohang_voice_stack.sh
/home/agi/restart_daohang_voice_stack.sh
```

这一步用于让网站“一键启动全部服务”同时重载机器人本机的唤醒、音频和交互进程。新机器人如果 soc2 地址不同，应通过 `SOC2_HOST` 调整脚本配置。

## 验证

在服务器上验证：

```bash
ss -ltnp | grep -E '(:18080|:18002|:10022)'
curl -sS http://127.0.0.1:18080/nav/status
curl -sS 'http://127.0.0.1:18002/api/discover_maps?map_root=%2Fagibot%2Fdata%2Fvar%2FMapManagerModule%2F'
```

## 注意

- 公网模式下，网页端填写的是“现场网关要连接的机器人 IP”。客户手机只要和机器人在同一个现场网络，就能通过网页填写该 IP。
- 语音服务运行在云端服务器上，连接灵心平台；它通过现场网关访问网页中填写的机器人导航 bridge，不再依赖客户电脑。
- 更换物理机器人时，在新机器人完成一次动态网关和导航服务部署后，只需要在网页填写它当时的 IP。旧机器人发现网页目标不再属于自己后会主动释放公网端口，新机器人会自动接管。
- 同一时间只建议一台机器人连接这些固定端口；如果要支持多台机器人，需要给每台机器人分配独立端口和机器人 ID。
