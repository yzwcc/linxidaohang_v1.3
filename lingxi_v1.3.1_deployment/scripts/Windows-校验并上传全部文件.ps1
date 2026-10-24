[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [ValidatePattern('^(?:\d{1,3}\.){3}\d{1,3}$')]
    [string]$RobotIp
)

$ErrorActionPreference = 'Stop'
$BundleRoot = Split-Path -Parent $PSScriptRoot
$AimdkName = 'aimdk-sdk-v1_1_0-aimdk-aarch64-faf4c945-artifacts.zip'
$NavName = 'sparkrobot-lingxi-nav-internal-1.1.0-20261024-r18-mapping6.tar.gz'
$AimdkPath = Join-Path $BundleRoot "packages\$AimdkName"
$NavPath = Join-Path $BundleRoot "packages\$NavName"
$NavChecksumPath = "$NavPath.sha256"
$AimdkExpected = 'dc80fe5d2907881ed77beb230680867efac6bd28b458f83d2ff4e9479abe3615'

function Assert-Command([string]$Name) {
    if (-not (Get-Command $Name -ErrorAction SilentlyContinue)) {
        throw "Windows 缺少 $Name，请先在‘可选功能’中安装 OpenSSH 客户端。"
    }
}

function Assert-Hash([string]$Path, [string]$Expected) {
    if (-not (Test-Path -LiteralPath $Path -PathType Leaf)) {
        throw "缺少文件：$Path"
    }
    $Actual = (Get-FileHash -Algorithm SHA256 -LiteralPath $Path).Hash.ToLowerInvariant()
    if ($Actual -ne $Expected) {
        throw "SHA-256 不一致：$Path`n预期：$Expected`n实际：$Actual"
    }
    Write-Host "[通过] $(Split-Path -Leaf $Path)" -ForegroundColor Green
}

function Invoke-Checked([string]$Program, [string[]]$Arguments) {
    & $Program @Arguments
    if ($LASTEXITCODE -ne 0) {
        throw "$Program 执行失败，退出码 $LASTEXITCODE"
    }
}

Assert-Command 'ssh'
Assert-Command 'scp'
Assert-Hash $AimdkPath $AimdkExpected
$NavExpected = ((Get-Content -LiteralPath $NavChecksumPath -Raw).Trim() -split '\s+')[0].ToLowerInvariant()
Assert-Hash $NavPath $NavExpected

Write-Host "检查机器人账号连接：$RobotIp" -ForegroundColor Cyan
Invoke-Checked 'ssh' @("agi@$RobotIp", 'test "$(uname -m)" = aarch64 -o "$(uname -m)" = arm64')
Invoke-Checked 'ssh' @("run@$RobotIp", 'id -u >/dev/null')

Write-Host '上传 AIMDK 1.1.0 ZIP 和 agi 脚本...' -ForegroundColor Cyan
Invoke-Checked 'scp' @($AimdkPath, "agi@${RobotIp}:/home/agi/$AimdkName")
Invoke-Checked 'scp' @((Join-Path $PSScriptRoot 'robot\01-replace-aimdk.sh'), "agi@${RobotIp}:/home/agi/")
Invoke-Checked 'scp' @((Join-Path $PSScriptRoot 'robot\04-check-localization.sh'), "agi@${RobotIp}:/home/agi/")

Write-Host '上传导航包和 run 脚本...' -ForegroundColor Cyan
Invoke-Checked 'scp' @($NavPath, "run@${RobotIp}:/home/run/$NavName")
Invoke-Checked 'scp' @((Join-Path $PSScriptRoot 'robot\00-install-environment.sh'), "run@${RobotIp}:/home/run/")
Invoke-Checked 'scp' @((Join-Path $PSScriptRoot 'robot\02-install-lingxi-navigation.sh'), "run@${RobotIp}:/home/run/")
Invoke-Checked 'scp' @((Join-Path $PSScriptRoot 'robot\03-verify-after-web-start.sh'), "run@${RobotIp}:/home/run/")

Write-Host ''
Write-Host '[完成] 全部安装文件已校验并上传。' -ForegroundColor Green
Write-Host "下一步：ssh run@$RobotIp"
Write-Host '然后执行：chmod +x /home/run/00-install-environment.sh && /home/run/00-install-environment.sh'
