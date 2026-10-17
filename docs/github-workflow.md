# GitHub 版本管理

仓库：https://github.com/yzwcc/linxidaohang_v1.3

每次完成一项代码更新并执行相应检查后，保存一个 Git 提交并上传当前分支。
Git 提交记录用于查阅和恢复各次更新；正式产品版本号仍按项目发布计划管理。

在项目根目录运行：

```powershell
rtk proxy powershell -NoProfile -ExecutionPolicy Bypass -File scripts/sync-github.ps1 -Message "描述本次更新"
```

仅预览待提交文件：

```powershell
rtk proxy powershell -NoProfile -ExecutionPolicy Bypass -File scripts/sync-github.ps1 -Message "预览" -DryRun
```

首次使用需登录有仓库写入权限的 GitHub 账号：

```powershell
rtk gh auth login --hostname github.com --git-protocol https --web
rtk gh auth setup-git
```

脚本会暂存未被忽略的文件、创建提交、推送并核对远程提交编号。
上传失败时本地提交保留，解决登录或网络问题后重新执行即可。
远程存在更新时，先获取并审查差异、按实际情况合并，再推送；不要强制覆盖远程历史。

凭据、依赖、缓存、压缩部署包和验证副本不上传。交付构建脚本、补丁脚本及说明文档纳入版本管理。
