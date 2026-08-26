$ErrorActionPreference = "Stop"

$Root = Split-Path -Parent $PSScriptRoot
Set-Location $Root

$Config = Join-Path $Root "config\lingxi-nav.properties"
$AppJar = Join-Path $Root "target\robot-local-navigation-java-0.1.0-all.jar"
$SdkJar = Join-Path $Root "linksoul-agentsdk-1.4.0-SNAPSHOT.jar"

if (!(Test-Path $Config)) {
    Write-Error "Missing $Config. Copy config\lingxi-nav.properties.example and fill credentials first."
}

if (!(Test-Path $AppJar)) {
    Write-Error "Missing $AppJar. Build first with Maven: mvn -DskipTests package"
}

if (!(Test-Path $SdkJar)) {
    Write-Error "Missing $SdkJar. Keep the LinkSoul AgentSDK jar in the project root."
}

java -cp "$AppJar;$SdkJar" com.agibot.aiem.nav.LingxiNavAgentApp --config $Config
