# V1.3.1 R16 点位页全局路径安全设置热修

此热修只适用于 V1.3.1 R16 基线及已知路径安全 UI 热修状态，不修改 AIMDK、PNC 算法、Java Agent、地图点位或机器人外形参数。它把“机器人实测外接半径（米）”和“额外障碍净空（米）”放到 3000 控制台的点位编辑页，紧邻 `task_type`。两项仍写入全局 `navigation.path_safety`，不会成为单个点位字段。两个输入由独立表单承载，输入校验或回车提交不会干扰点位保存。安装器仅接受精确匹配的原始 R16、首版热修或 R3 五文件状态，并拒绝混合/未知状态。现场已有的 Java Agent JAR 会先做 ZIP 完整性检查并记录 SHA-256；确认热修过程中它没有变化后，安装器仅把该 JAR 的哈希同步到 payload manifest，不替换 JAR。

R16 的安全拒绝保持不变。机器人半径需要按现场整机外形实测填写；没有半径时 `task_type=2、pnc_mode=0` 全局导航继续被拒绝。额外净空范围为 0 到 2 米，机器人半径范围为大于 0 到 2 米。

## 安装前提

- 当前安装为 `sparkrobot-lingxi-nav-internal-1.0.8-20261023-r16` 精确基线。安装器逐文件校验五个控制台基线 SHA-256，不匹配时拒绝覆盖。
- 机器人处于空闲状态：无全局导航、无活动或暂停的讲解任务、无自动返航。安装器在线检查 `/nav/status`；无法确认空闲时停止安装。
- 在 ARM64 机器人上由 `run` 用户校验和解包，再使用 `sudo` 安装。

## 安装

将 TAR.GZ 和同名 `.sha256` 文件上传到机器人 `/home/run/`，执行：

```bash
cd /home/run
sha256sum -c lingxi-v1.3.1-r16-path-safety-ui-hotfix-20261024-r14.tar.gz.sha256
tar -xzf lingxi-v1.3.1-r16-path-safety-ui-hotfix-20261024-r14.tar.gz
cd lingxi-v1.3.1-r16-path-safety-ui-hotfix-20261024-r14
sudo bash ./install.sh install
```

安装完成后强制刷新浏览器页面（Windows/Linux：`Ctrl+F5`，macOS：`Cmd+Shift+R`），然后编辑点位查看新增字段。

安装器备份五个控制台文件及 `payload-manifest.json`，只替换这些文件并更新 R16 完整性清单。安装前确认当前发布目录准确为 `/opt/sparkrobot/lingxi-nav/releases/1.0.8-20261023-r16`、控制台原本处于运行状态。若 `/nav/status` 暂时不可用，安装器仅在 R16 脚本确认 bridge、地图服务和导航 gateway 全部停止时才自动启动导航栈，以便读取状态；若为部分运行或状态不明则安全退出。确认导航、讲解和自动返航均空闲后，安装器使用 R16 `navigation_control.sh stop` 有序停止导航栈，更新文件并重启 3000 控制台，再启动 bridge、8002 地图服务和导航 gateway，并检查 3000 `/health`、8002 `/api/settings`、18082 `/nav/status`、18083 `/nav/status` 与导航空闲状态。Java Agent JAR 保持现场原文件，只在确认安装前后哈希一致后同步其清单条目。此过程会造成短暂导航服务中断，但不会重启 PNC/底盘服务、Java Agent 或机器人语音栈。验收失败会恢复原控制台文件和 manifest，再同步未变化的现场 JAR 哈希，随后尝试恢复导航栈。不得在机器人运行导航或讲解时强制安装。

## 页面设置

打开 `http://机器人IP:3000/console/`，进入“地图与点位”并编辑点位。在 `task_type` 下方可看到两项全局设置：

- **机器人实测外接半径（米）**：填写整机（含实际载荷/突出物）外接半径的实测值，不要用猜测值。若整机矩形长宽为 `L` 和 `W`，几何外接半径可初估为 `sqrt(L^2 + W^2) / 2`，但应以最不利实际转向包络核对。
- **额外障碍净空（米）**：填写在实测机器人半径外额外保留的静态障碍距离。R16 基线默认为 `0.10` 米。

点击“保存全局安全参数”。只有成功从机器人读取既有设置后才允许保存，避免地图服务暂时不可达时用网页默认数值覆盖现有配置。成功状态表示参数已经写入机器人持久配置；刷新页面可再次读取确认。R16 地图服务没有提供桥接应用回执，因此页面不会声称“桥接已应用”。保存参数不会移动机器人，也不会自动开始导航。

## 回滚

安装器会打印备份目录。确认机器人空闲后执行：

```bash
sudo bash ./install.sh rollback \
  /var/lib/sparkrobot/lingxi-nav/path-safety-ui-hotfix-backups/YYYYMMDDTHHMMSSZ
```

## 离线隔离测试

发布前构建机执行：

```bash
bash ./test_install.sh
```

该测试在临时隔离目录执行安装、清单验证和回滚，不连接机器人、不启动导航、不更改现场配置。
