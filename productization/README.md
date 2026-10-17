# 灵犀导航产品化工具

该目录用于厂商将灵犀导航构建为单机器人绑定产品。客户只使用机器人本机网页，不接收本目录、设备归档或部署工具。

## 标准流程

1. `vendor/collect_claim_remote.py`：通过 SSH 采集新机器人设备指纹。
2. `vendor/init_vendor_keys.py`：首次初始化厂商加密签名私钥。
3. `vendor/issue_license.py`：为目标机器人签发许可证。
4. `vendor/build_native_navigation.sh`：在 ARM64/Python 3.10 环境构建原生导航模块。
5. `vendor/protect_java_jar.py`：保护厂商 Java 业务类并保留 SDK 回调入口。
6. `vendor/build_release.py`：组合控制台、运行时、许可证和签名清单。
7. `vendor/audit_release.py`：独立审计源码、秘密、架构、ABI、签名和权限。
8. `vendor/smoke_test_release.py`：在机器人 `/tmp` 做无安装、无移动冒烟验证。
9. `vendor/deploy_robot.py`：厂商 SSH 安装并清理临时文件。

## 目录

- `bootstrap/`、`common/`：设备身份、许可证和发布验签。
- `runtime/`、`systemd/`：机器人安装、控制脚本和服务定义。
- `vendor/`：厂商私有构建与部署工具。
- `tests/`：保护层和隔离安装生命周期测试。
- `build/`：厂商构建输入与测试制品，不交付客户。
- `dist/`：单机器人绑定归档，不交付客户。
- `reports/`：审计、设备申请和内部验证记录。
- `docs/`：厂商内部文档与客户手册。

## 当前状态

`dist/sparkrobot-lingxi-nav-1.0.0-test.2-fef658bf.tar.gz` 是测试签名归档，仅用于验证流程。正式客户归档必须使用厂商人员亲自初始化的加密私钥，并按目标新机器人的设备指纹重新签发。

完整命令、升级回滚和验收标准见 [厂商部署与运维手册](docs/internal/厂商部署与运维手册.md)。

