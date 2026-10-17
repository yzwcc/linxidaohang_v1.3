# 灵犀导航产品化资料目录

版本日期：2026-08-26

## 文档分级

| 目录 | 使用者 | 是否可交给客户 |
|---|---|---|
| `internal/` | 厂商部署、研发、售后人员 | 否 |
| `customer/` | 客户现场操作人员 | 是 |
| `pdf/` | 对应 PDF 成品 | 仅客户手册 PDF 可交付 |

## 厂商侧保留内容

- `productization/vendor/`：密钥、许可证、构建、审计、SSH 部署和冒烟验证工具。
- `productization/bootstrap/`、`common/`、`runtime/`、`systemd/`：发布和安装基础设施。
- `productization/build/`：原生模块、受保护 JAR、JRE、测试密钥和构建中间物。
- `productization/dist/`：单台设备绑定归档。
- `productization/reports/`：发布审计和内部验收记录。
- 正式 RSA 私钥、Java 混淆 mapping、灵心 AppSecret、豆包 API Key、SSH 密码和 SOC2 密钥。

上述内容不得发送给客户，也不得放在机器人网页、客户 U 盘、公共网盘或公开代码仓库中。

## 客户侧交付内容

- `customer/灵犀导航客户使用手册.md`
- `pdf/灵犀导航客户使用手册.pdf`
- 机器人本体、现场网络信息、机器人访问地址和售后联系方式。

客户不接收源码、厂商部署工具、设备归档、许可证签发工具、签名私钥、混淆 mapping 或任何平台密钥。

## 当前测试制品

`productization/dist/` 中带 `test` 的归档只用于隔离验证，不得销售或部署到正式客户设备。正式交付必须按厂商手册重新初始化加密私钥、采集目标设备指纹、签发正式许可证并生成无 `test` 标识的版本。

