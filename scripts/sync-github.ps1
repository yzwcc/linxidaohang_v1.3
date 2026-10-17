[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [ValidateNotNullOrEmpty()]
    [string]$Message,
    [switch]$DryRun
)

$ErrorActionPreference = 'Stop'

function Invoke-ProjectGit {
    param([Parameter(ValueFromRemainingArguments = $true)][string[]]$GitArgs)
    $result = & rtk proxy git @GitArgs
    if ($LASTEXITCODE -ne 0) {
        throw "Git failed ($LASTEXITCODE): git $($GitArgs -join ' ')"
    }
    return $result
}

Push-Location (Split-Path -Parent $PSScriptRoot)
try {
    $branch = (Invoke-ProjectGit symbolic-ref --quiet --short HEAD).Trim()
    $remote = (Invoke-ProjectGit remote get-url --push origin).Trim()
    if ($remote -notmatch '^(https://github\.com/|git@github\.com:)yzwcc/linxidaohang_v1\.3(?:\.git)?/?$') {
        throw "Unexpected origin: $remote"
    }
    if (@(Invoke-ProjectGit ls-files --unmerged).Count -gt 0) {
        throw 'Resolve merge conflicts before syncing.'
    }
    Invoke-ProjectGit status --short --branch
    if ($DryRun) {
        Invoke-ProjectGit add --all --dry-run
        Write-Host "Preview only. Target: origin/$branch"
        return
    }

    Invoke-ProjectGit add --all
    $staged = @(Invoke-ProjectGit diff --cached --name-only)
    if ($staged.Count -gt 0) {
        Invoke-ProjectGit commit -m $Message
    }
    Invoke-ProjectGit push --set-upstream origin "HEAD:refs/heads/$branch"
    $localCommit = (Invoke-ProjectGit rev-parse HEAD).Trim()
    $remoteRef = @(Invoke-ProjectGit ls-remote origin "refs/heads/$branch")
    if ($remoteRef.Count -ne 1 -or ($remoteRef[0] -split '\s+')[0] -ne $localCommit) {
        throw 'Remote verification failed; do not report this version as uploaded.'
    }
    Write-Host "Uploaded and verified: $branch $localCommit"
}
finally {
    Pop-Location
}
