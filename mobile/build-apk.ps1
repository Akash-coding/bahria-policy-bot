$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
if (-not (Test-Path (Join-Path $PSScriptRoot "android"))) {
    $root = "D:\behria chatbot"
    $mobile = Join-Path $root "mobile"
} else {
    $mobile = $PSScriptRoot
    $root = Split-Path -Parent $mobile
}

$buildRoot = "D:\AndroidBuild"
$sdkRoot = "D:\AndroidSdk"
$jdkDir = Join-Path $buildRoot "jdk-17"
$gradleDir = Join-Path $buildRoot "gradle-8.7"
$tmpDir = Join-Path $buildRoot "tmp"
$gradleHome = Join-Path $buildRoot "gradle-home"
$androidUser = Join-Path $buildRoot "android-user"
$downloadDir = Join-Path $buildRoot "downloads"

New-Item -ItemType Directory -Force -Path $buildRoot, $sdkRoot, $tmpDir, $gradleHome, $androidUser, $downloadDir | Out-Null

$env:TEMP = $tmpDir
$env:TMP = $tmpDir
$env:GRADLE_USER_HOME = $gradleHome
$env:ANDROID_USER_HOME = $androidUser
$env:ANDROID_SDK_ROOT = $sdkRoot
$env:ANDROID_HOME = $sdkRoot
$env:JAVA_HOME = $jdkDir

function Download-File($url, $dest) {
    if (Test-Path $dest) {
        Write-Host "Already have $dest"
        return
    }
    Write-Host "Downloading $url"
    & curl.exe -L --retry 3 --retry-delay 2 -o $dest $url
    if ($LASTEXITCODE -ne 0) { throw "Download failed: $url" }
}

function Expand-Zip($zip, $dest) {
    if (Test-Path $dest) { return }
    Write-Host "Extracting $zip"
    Expand-Archive -LiteralPath $zip -DestinationPath $dest -Force
}

# JDK 17
$jdkZip = Join-Path $downloadDir "jdk17.zip"
if (-not (Test-Path (Join-Path $jdkDir "bin\java.exe"))) {
    Download-File "https://api.adoptium.net/v3/binary/latest/17/ga/windows/x64/jdk/hotspot/normal/eclipse?project=jdk" $jdkZip
    $jdkExtract = Join-Path $buildRoot "jdk-extract"
    if (Test-Path $jdkExtract) { Remove-Item $jdkExtract -Recurse -Force }
    Expand-Archive -LiteralPath $jdkZip -DestinationPath $jdkExtract -Force
    $inner = Get-ChildItem $jdkExtract -Directory | Select-Object -First 1
    if (Test-Path $jdkDir) { Remove-Item $jdkDir -Recurse -Force }
    Move-Item $inner.FullName $jdkDir
}

# Gradle 8.7
$gradleZip = Join-Path $downloadDir "gradle-8.7-bin.zip"
if (-not (Test-Path (Join-Path $gradleDir "bin\gradle.bat"))) {
    Download-File "https://services.gradle.org/distributions/gradle-8.7-bin.zip" $gradleZip
    $gradleExtract = Join-Path $buildRoot "gradle-extract"
    if (Test-Path $gradleExtract) { Remove-Item $gradleExtract -Recurse -Force }
    Expand-Archive -LiteralPath $gradleZip -DestinationPath $gradleExtract -Force
    $inner = Get-ChildItem $gradleExtract -Directory | Select-Object -First 1
    if (Test-Path $gradleDir) { Remove-Item $gradleDir -Recurse -Force }
    Move-Item $inner.FullName $gradleDir
}

# Android command-line tools
$cmdZip = Join-Path $downloadDir "commandlinetools.zip"
$sdkManager = Join-Path $sdkRoot "cmdline-tools\latest\bin\sdkmanager.bat"
if (-not (Test-Path $sdkManager)) {
    Download-File "https://dl.google.com/android/repository/commandlinetools-win-15859902_latest.zip" $cmdZip
    $cmdExtract = Join-Path $buildRoot "cmdline-extract"
    if (Test-Path $cmdExtract) { Remove-Item $cmdExtract -Recurse -Force }
    Expand-Archive -LiteralPath $cmdZip -DestinationPath $cmdExtract -Force
    $latest = Join-Path $sdkRoot "cmdline-tools\latest"
    New-Item -ItemType Directory -Force -Path (Split-Path $latest) | Out-Null
    if (Test-Path $latest) { Remove-Item $latest -Recurse -Force }
    $inner = Get-ChildItem $cmdExtract -Directory | Select-Object -First 1
    Move-Item $inner.FullName $latest
}

$env:Path = "$(Join-Path $jdkDir 'bin');$(Join-Path $gradleDir 'bin');$(Join-Path $sdkRoot 'cmdline-tools\latest\bin');$(Join-Path $sdkRoot 'platform-tools');$env:Path"

$sdkManager = Join-Path $sdkRoot "cmdline-tools\latest\bin\sdkmanager.bat"
$platformDir = Join-Path $sdkRoot "platforms\android-34"
if (-not (Test-Path $platformDir)) {
    Write-Host "Installing Android SDK packages"
    $packages = @("platforms;android-34", "build-tools;34.0.0", "platform-tools")
    & $sdkManager --sdk_root=$sdkRoot $packages
    if ($LASTEXITCODE -ne 0) { throw "sdkmanager install failed" }
    $yes = $("y`n" * 80)
    $yes | & $sdkManager --sdk_root=$sdkRoot --licenses | Out-Host
} else {
    Write-Host "Android SDK already installed"
}

# Web build (direct /api paths so the APK can talk to this laptop)
Set-Location $mobile
if (-not (Test-Path (Join-Path $mobile "node_modules"))) {
    cmd /c mklink /J node_modules ..\frontend\node_modules | Out-Null
}

$lanIp = ""
try {
    $udp = New-Object System.Net.Sockets.UdpClient
    $udp.Connect("8.8.8.8", 80)
    $lanIp = $udp.Client.LocalEndPoint.Address.ToString()
    $udp.Close()
} catch {}
if (-not $lanIp -or $lanIp.StartsWith("127.")) {
    $lanIp = "127.0.0.1"
}
$lanXml = @"
<?xml version="1.0" encoding="utf-8"?>
<resources>
    <string name="server_host" translatable="false">$lanIp</string>
    <string name="server_url" translatable="false">http://$lanIp`:8000</string>
</resources>
"@
Set-Content -Path (Join-Path $mobile "android\app\src\main\res\values\lan.xml") -Value $lanXml -Encoding UTF8
Write-Host "APK will use http://$lanIp`:8000"

Write-Host "Building web app"
$env:VITE_API_DIRECT = "true"
$env:NODE_OPTIONS = "--max-old-space-size=1536"
npx --yes --prefer-offline vite build
if ($LASTEXITCODE -ne 0) { throw "vite build failed" }

$www = Join-Path $mobile "android\app\src\main\assets\www"
if (Test-Path $www) { Remove-Item $www -Recurse -Force }
Copy-Item (Join-Path $mobile "dist") $www -Recurse

$localProps = @"
sdk.dir=$($sdkRoot.Replace('\','\\'))
"@
Set-Content -Path (Join-Path $mobile "android\local.properties") -Value "sdk.dir=$($sdkRoot.Replace('\','/'))" -Encoding ASCII

Write-Host "Building APK"
Set-Location (Join-Path $mobile "android")
& (Join-Path $gradleDir "bin\gradle.bat") assembleDebug --no-daemon
if ($LASTEXITCODE -ne 0) { throw "gradle assembleDebug failed" }

$apk = Join-Path $mobile "android\app\build\outputs\apk\debug\app-debug.apk"
$dest = Join-Path $root "BahriaAI.apk"
Copy-Item $apk $dest -Force
Write-Host "APK ready: $dest"
