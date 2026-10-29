# 独立建图 ARM64 联调包

日期：2026-10-30。本轮离线打包专用建图服务、统一进程管理、管理 API 和网页，
供之后在未选配厂商 SLAM 的 X2 上集中联调。机器人保持断电，没有连接或部署。
这不是最终安装包；生产入口与 0.1.19 APK 保持原样。

## 本次完成

- 新增 `robot-runtime/build-mapping.mjs`，构建专用建图 API 和共用网页。
  源码清单、构建工具版本和所有编译资产均进入散列清单。
- 新增 `prepare_mapping_bundle.py`，复用已有归档校验流程，准备、复核和解压
  `managed-mapping-runtime-arm64.tar.gz`。独立建图包与只读点流包的用途、清单、
  路径前缀不同，不能混用。
- 新增 `launch_mapping_bundle.py`，仅从解压包自带源码启动并校验全包。
  使用包内 Node、API、网页与统一进程管理，支持配对后开始、命名保存、
  丢弃建图和同引擎点云显示。地图和报告写入包外的新私有目录。
- 复用既有已锁定的 ARM64 Node 和 95 个生产依赖，不重新下载或安装依赖。
  包内没有证书、密钥、机器人配置、录制数据或业务地图。

生成件位于开发目录的
`artifacts/lingxi-next-managed-mapping-20261030-arm64.tar.gz`，
旁边附带 `.sha256` 校验文件；两者不进入 Git。
可公开保存的校验聚合为
[`slam-managed-mapping-runtime-preparation-evidence.json`](slam-managed-mapping-runtime-preparation-evidence.json)。

| 项目 | 本次结果 |
| --- | --- |
| 归档字节数 | 54,064,707 |
| 源码文件 | 160 |
| 包内文件（含清单） | 7,736 |
| 编译资产 | 5 |
| 已锁定生产依赖 | 95 |
| ARM64 Node | 22.23.4，ELF 架构与二进制散列已核对；本轮未执行 |
| 归档 SHA256 | `1a10c1c98d14c6199cfabb5b281716ec98536590b776bb02419523a60b303e30` |
| 包清单 SHA256 | `f455bf363c09ee6eb29ff3121b5e17a9b2a6378ae7b1961091b67d467ec5bcfd` |

## 外部运行依赖

目标必须是普通用户 Linux AArch64、已安装的 Humble Python 3.10。
原生 GLIM 库、解码器、点流扩展和它们的固定校验清单继续使用既有隔离准备结果；
本包不重复携带或替换它们，也不覆盖 AIMDK。

包外配置必须通过 `mapping_platform.py` 校验，指定 `arm64-humble` 平台、
录制或实时输入、已验证的原生依赖和 `pointAdapter`。
首次集成应先验证录制输入，再验证实际被动传感器输入。
配置格式与输入约束见 [SLAM_MAPPING_CONTROL.md](SLAM_MAPPING_CONTROL.md)。
没有上述输入时，解压成功也不能开始真实建图。

TLS 私钥、证书和配对码散列由目标环境提供。证书必须覆盖访问地址，
电脑或 Android 需信任签发 CA。密钥和配置放在包外；运行输出必须是新目录，
不能与包、配置和原生输入重叠。完整输出路径还受 Unix socket 长度限制，
应选择较短的私有路径。

## 构建和提取

以下由开发环境执行，不要求现在启动机器人。
在 `lingxi-next/` 中构建资产：

```sh
node robot-runtime/build-mapping.mjs .local/NEW_MAPPING_BUILD
```

普通用户 Linux x86-64 中，使用当前构建清单 SHA256 和既有已验证依赖：

```sh
python3 -I -B robot-runtime/python/prepare_mapping_bundle.py prepare \
  --build /absolute/NEW_MAPPING_BUILD \
  --build-manifest-sha256 REPLACE_WITH_BUILD_MANIFEST_SHA256 \
  --consumer /absolute/VERIFIED_ARM64_CONSUMER \
  --consumer-manifest-sha256 e35d411cf7cde7b8bca4b76717accaf2a137ca5512c2f1fe8fcf3f6f1a106521 \
  --output /private/NEW_PREPARATION
```

复核准备目录：

```sh
python3 -I -B robot-runtime/python/prepare_mapping_bundle.py verify \
  --preparation /private/NEW_PREPARATION \
  --output /private/NEW_VERIFIED_EVIDENCE.json
```

提取到不存在的新目录，使用与实际归档配套的散列：

```sh
python3 -I -B robot-runtime/python/prepare_mapping_bundle.py extract \
  --archive /private/managed-mapping-runtime-arm64.tar.gz \
  --archive-sha256 REPLACE_WITH_ARCHIVE_SHA256 \
  --manifest-sha256 REPLACE_WITH_MAPPING_MANIFEST_SHA256 \
  --output /private/mapping-runtime
```

解压后不依赖原开发目录：包内 `source/robot-runtime/python/prepare_mapping_bundle.py`
同样支持上述命令。提取前检查归档全部成员、路径、权限和内容散列，提取后用包内
源码重新校验；拒绝覆盖现有目录。重新构建后必须使用新清单，不沿用本文历史散列。

## 后续实机启动方式

下面是后续集中联调用的命令模板，本轮未执行。端口显式选择 `3443`，
不得使用现有产品端口 `3000`，也不得选择随机端口 `0`。
示例绑定本机回环；局域网联调时按现场网络选择绑定地址和相应证书。

```sh
export LINGXI_HOST=127.0.0.1
export LINGXI_PORT=3443
export LINGXI_TLS_KEY=/private/tls/server.key
export LINGXI_TLS_CERT=/private/tls/server.crt
export LINGXI_PAIRING_CODE_SHA256=REPLACE_WITH_PAIRING_CODE_SHA256
python3 -I -B \
  /private/mapping-runtime/source/robot-runtime/python/launch_mapping_bundle.py \
  --bundle /private/mapping-runtime \
  --manifest-sha256 REPLACE_WITH_MAPPING_MANIFEST_SHA256 \
  --config /private/config/mapping.json \
  --output /private/mrun \
  --seconds 300
```

网页路径由包固定，不能用 `LINGXI_WEB_ROOT` 指向其他资产；不接受外部
`LINGXI_DATA_DIR` 或 `LINGXI_POINT_READ_ONLY` 覆盖。
一次联调运行 5–3600 秒，单次实时输入会话仍受既有 5–300 秒限制。
到期或收到停止信号后，服务先关闭原生子进程，API 继续排空点流后退出。
有界收尾可能超出所填运行时长；强制清理会记为失败。
启动器不安装 systemd 服务，不自动接管生产系统。

## 离线验证

- 完整 Linux Python 回归 849 项：845 项通过、4 项明确跳过。
  其中新建图包合成测试 8 项，原只读包回归 14 项。
- Windows 新建图包测试 8 项通过；构建清单测试 4 项通过。
- Vitest 214 项 / 18 文件通过。
- 实际归档完成准备、独立复核、完整提取和提取后包内源码校验。
  从提取包执行启动器，在 x86-64 上确认因架构不符拒绝且未创建运行输出。
- 10 项普通用户 Linux 跨进程测试全部通过，加载提取包的服务/进程管理源码、
  编译 API、依赖和网页；用本机 x64 Node 执行 JavaScript。
  覆盖保存/替换/丢弃、旧会话拒绝、点流、配对、HTTPS/WSS、
  首页与引用脚本可访问、进程崩溃/强杀清理、启动失败和到期退出。

上述跨进程测试中的原生进程、地图内容和重载回执是合成替身。
它们证明提取后的软件组合能运行，不证明本轮执行了 ARM64 原生 GLIM、
实际传感器采集、几何精度或物理导航。之前实机和原生试验证据保持其历史来源。

使用提取包复现跨进程测试时，在开发源码目录中运行：

```sh
LINGXI_MAPPING_API_BUNDLE=/private/mapping-runtime/api/main.mjs \
LINGXI_MAPPING_OWNER_PYTHON=/usr/bin/python3 \
LINGXI_MAPPING_SOURCE_ROOT=/private/mapping-runtime/source \
LINGXI_MAPPING_WEB_ROOT=/private/mapping-runtime/web \
/absolute/linux-x64-node22/bin/node --test tools/check-managed-mapping-owner.mjs
```

下一阶段优先做底盘坐标/时间与外参接入、保存地图定位和独立导航集成，
继续复用录制数据推进。长程闭环、实际 ARM64 组合、现场传感器、局部避障、
语音并发和 Android 操作集中安排实机验收；当前各项产品就绪标记仍为 false。
