# 灵犀 v1.3.1 完整部署包

版本：v1.3.1-complete-20261022-r8
适用对象：内部部署人员  
目标系统：Ubuntu 22.04 ARM64、ROS 2 Humble、智元 X2 系列机器人

本包把一台新机器人部署为可以通过手机网页使用的灵犀导航产品。客户日常只需要让手机和机器人连接同一个局域网，打开机器人当前 IP 的网页并点击“一键开启全部服务”，不需要电脑常驻运行。

## 包含内容

- 新 AIMDK：aimdk-aarch64-1bde262f-artifacts.7z，版本 aimdk v0.0.0-g1bde262。
- 导航主包：sparkrobot-lingxi-nav-internal-1.0.7-20260901。
- 地图服务、导航 bridge、网页控制台、语音兼容网关和灵心 Java Agent。
- 灵创动作、表情和可选舞蹈资源编排能力：1.0.3-20260902。
- 灵心知识库检索和语音接入：1.0.3-20261022。
- Java Agent 启用 V1.3.8 交互拒识策略：填充词、重复 ASR 片段和播报回声过滤；质量元数据校验；真实 KWS/新鲜唤醒会话门控。
- 连续导航卡住修复：1.0.1-20260907，必须最后覆盖导航文件。
- 中文部署教程和客户日常使用手册。

完整包内部已经包含 AIMDK 和导航主包，不需要另外下载或上传这两个组件。components/ 中的副本用于校验和安装，不能删掉。

## 不包含的内容

包内没有灵心 AppSecret、豆包 API Key、SSH 私钥、知识库密钥或直播项目配置。安装时交互填写的密钥只写入机器人受保护配置，不会进入归档。不要把直播项目目录或其语音配置复制到本包。

## 最短部署流程

以下流程必须按顺序执行。外层安装器由机器人 run 用户执行；AIMDK 编译和替换由 agi 用户执行。

### 1. Windows 上传

在保存本包的 Windows PowerShell 中，把 ROBOT_IP 替换为现场机器人 IP：

~~~powershell
$RobotIp = "172.20.10.2"
$Package = ".\lingxi-v1.3.1-complete-20261022-r8-deployment.tar.gz"
$Checksum = ".\lingxi-v1.3.1-complete-20261022-r8-deployment.tar.gz.sha256"
scp $Package ("run@" + $RobotIp + ":/home/run/")
scp $Checksum ("run@" + $RobotIp + ":/home/run/")
~~~

### 2. 机器人 run 用户准备环境

~~~bash
cd /home/run
sha256sum -c lingxi-v1.3.1-complete-20261022-r8-deployment.tar.gz.sha256
tar -xzf lingxi-v1.3.1-complete-20261022-r8-deployment.tar.gz
cd lingxi-v1.3.1-complete-20261022-r8-deployment
chmod +x install_all.sh control_all.sh
bash ./install_all.sh prepare
~~~

看到校验失败时不要继续安装。prepare 会安装编译依赖，并把 AIMDK 文件、AIMDK 脚本和导航安装脚本准备到 /home/agi、/home/run。

### 3. 机器人 agi 用户替换 AIMDK

用新的 SSH 会话登录 agi（不要假设 run 可以切换用户）：

~~~bash
ssh agi@ROBOT_IP
cd /home/agi
bash ./01-replace-aimdk.sh
~~~

脚本会先编译和验证新 AIMDK，成功后才删除旧 /home/agi/aimdk。完成后退出 agi 会话。

### 4. run 用户安装导航主包

~~~bash
cd /home/run/lingxi-v1.3.1-complete-20261022-r8-deployment
bash ./install_all.sh install-navigation
~~~

按提示填写同一灵心应用的 App ID、AppKey、AppSecret、豆包 API Key、模型、机器人名称和实际 SOC2 地址。安装器会删除临时 secrets.env，不要手动把密钥写进包目录。

### 5. 安装灵创、知识库和连续导航修复

~~~bash
bash ./install_all.sh install-addons --agent-code AI_AGENT_xxxxxxxx
~~~

把 AI_AGENT_xxxxxxxx 替换为客户实际的灵心智能体 Code。安装器固定按以下顺序执行：灵创文件 -> 知识库服务和 Java Agent -> 连续导航修复 -> 网页控制台重启。最后一步保证网页能读取新的动作、表情和舞蹈选项，同时不会让机器人执行真实舞蹈或导航。

### 6. 无移动健康检查

~~~bash
bash ./install_all.sh doctor
sudo sparkrobot-lingxi-complete status
sudo sparkrobot-lingxi-complete doctor
~~~

doctor 只检查服务、文件、ROS/AIMDK 导入、资源和权限，不会发送导航、重定位、语音或动作指令。

## 使用入口

手机与机器人连接同一个局域网后打开：

~~~text
http://机器人当前IP:3000/console/
~~~

客户在网页中可以选择地图、查看和编辑点位、根据机器人实际位置自动重定位、启动/停止导航、配置讲解任务，以及在点位讲解和普通动作完成后选择灵创舞蹈。机器人更换网络后 IP 可能变化，网页地址也应使用新的 IP；本包不固定某一台机器人的地址。

## 文档

- 完整部署教程：docs/灵犀完整部署教程-v1.3.1-complete.md。内容包括上传、安装、验收、排障和回滚。
- 客户日常使用手册：docs/客户日常使用手册-v1.3.1.md。面向没有电脑基础的客户。

## 交付前检查

在开发电脑上构建并审计：

~~~powershell
python .\integrated_deployment\build_package.py
python .\integrated_deployment\audit_package.py
~~~

构建机需要 Python 3 和 JDK 17（`java`、`javac`、`jar` 在 PATH）。完整包构建会先重编 V1.3.8 交互拒识 Agent 并运行 Java 回归测试，再生成 KB ZIP 和完整包。

交付给 Linux 机器人优先使用：

~~~text
integrated_deployment/dist/lingxi-v1.3.1-complete-20261022-r8-deployment.tar.gz
integrated_deployment/dist/lingxi-v1.3.1-complete-20261022-r8-deployment.tar.gz.sha256
~~~

ZIP 可用于 Windows 传输或查看，但机器人端仍建议使用 TAR.GZ。每台新机器人都必须独立完成定位、急停、短距离导航和语音验收；离线审计不能替代现场安全验收。

语音拒识默认在兼容模式下接受缺失的声学质量元数据，但会校验已提供的值；唤醒门控默认 enforce，需要真实 SDK KWS/状态事件或可核验的新鲜 SOC2 唤醒证据。该策略是输入门控，不包含端侧 RNNoise、AEC 或实际 DOA 音频滤波；声学链路需由现场单独验收。
