[CmdletBinding()]
param(
    [switch]$Stop
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"
try {
    # Windows PowerShell 5.1 is the baseline. Avoid newer-only syntax here.
    [Console]::OutputEncoding = New-Object System.Text.UTF8Encoding
}
catch {
    # Console encoding must never prevent the launcher from reaching its log path.
}

$PackageRoot = [System.IO.Path]::GetFullPath((Join-Path $PSScriptRoot ".."))
$BackendDir = Join-Path $PackageRoot "backend"
$PackagedRuntimeDir = Join-Path $PackageRoot "runtime"
$RuntimeManifestFile = Join-Path $PackagedRuntimeDir "runtime.json"
$WheelhouseManifestFile = Join-Path $PackagedRuntimeDir "wheelhouse.json"
$RequirementsFile = Join-Path $PackagedRuntimeDir "requirements.txt"
$WheelhouseDir = Join-Path $PackagedRuntimeDir "wheels"
$PackagedModelDir = Join-Path $PackagedRuntimeDir "models"
$RuntimeDir = Join-Path $PackageRoot ".runtime"
$BundledPythonDir = Join-Path $RuntimeDir "python"
$BundledPython = Join-Path $BundledPythonDir "python.exe"
$PackagesDir = Join-Path $RuntimeDir "python-packages"
$PidFile = Join-Path $RuntimeDir "speech-trainer.pid"
$LogDir = Join-Path $RuntimeDir "logs"
$StdoutLog = Join-Path $LogDir "backend.log"
$StderrLog = Join-Path $LogDir "backend-error.log"
$LauncherLog = Join-Path $LogDir "launcher.log"
$LaunchStatusFile = Join-Path $RuntimeDir "launch.success"
$DependencyMarker = Join-Path $RuntimeDir "dependencies.ready"
$Port = 17860
$AppUrl = "http://127.0.0.1:$Port/"
$TranscriptStarted = $false

function Write-Step([string]$Message) {
    Write-Host ""
    Write-Host "==> $Message" -ForegroundColor Cyan
}

function Read-JsonFile([string]$Path, [string]$Description) {
    if (-not (Test-Path -LiteralPath $Path -PathType Leaf)) {
        throw "安装包缺少$Description。请重新下载并完整解压新版安装包。"
    }
    try {
        return Get-Content -LiteralPath $Path -Raw -Encoding UTF8 | ConvertFrom-Json
    }
    catch {
        throw "安装包中的$Description已损坏。请重新下载并完整解压新版安装包。"
    }
}

function Test-OwnedRuntimePath([string]$Path) {
    $fullRuntime = [System.IO.Path]::GetFullPath($RuntimeDir).TrimEnd('\') + '\'
    $fullPath = [System.IO.Path]::GetFullPath($Path)
    return $fullPath.StartsWith($fullRuntime, [System.StringComparison]::OrdinalIgnoreCase)
}

function Remove-OwnedRuntimePath([string]$Path) {
    if (-not (Test-OwnedRuntimePath $Path)) {
        throw "拒绝清理安装包运行目录之外的路径。"
    }
    if (Test-Path -LiteralPath $Path) {
        Remove-Item -LiteralPath $Path -Recurse -Force
    }
}

function Test-BundledPython([string]$PythonPath) {
    if (-not (Test-Path -LiteralPath $PythonPath -PathType Leaf)) { return $false }
    try {
        # Windows PowerShell 5.1 removes embedded double quotes while rebuilding
        # arguments for native programs. Keep the Python probe free of them so it
        # reaches python.exe unchanged on the oldest supported PowerShell.
        $probeCode = "import sys;print(f'{sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}|{64 if sys.maxsize>2**32 else 32}')"
        $probe = & $PythonPath -I -c $probeCode 2>$null
        return $LASTEXITCODE -eq 0 -and [string]$probe -eq "3.12.14|64"
    }
    catch {
        return $false
    }
}

function Ensure-BundledPython {
    $manifest = Read-JsonFile $RuntimeManifestFile "内置 Python 清单"
    if (
        [int]$manifest.schema_version -ne 1 -or
        [string]$manifest.python_version -ne "3.12.14" -or
        [string]$manifest.architecture -ne "x86_64"
    ) {
        throw "安装包中的内置 Python 版本不受支持。请使用完整的新版安装包。"
    }

    $archiveName = [string]$manifest.archive
    if ([System.IO.Path]::GetFileName($archiveName) -ne $archiveName) {
        throw "安装包中的内置 Python 清单无效。请使用完整的新版安装包。"
    }
    $archive = Join-Path $PackagedRuntimeDir $archiveName
    if (-not (Test-Path -LiteralPath $archive -PathType Leaf)) {
        throw "安装包缺少内置 Python 文件。请重新下载并完整解压新版安装包。"
    }
    $actualHash = (Get-FileHash -LiteralPath $archive -Algorithm SHA256).Hash.ToLowerInvariant()
    if ($actualHash -ne ([string]$manifest.archive_sha256).ToLowerInvariant()) {
        throw "安装包内置 Python 校验失败。请删除当前副本后重新下载并解压。"
    }

    if (Test-BundledPython $BundledPython) { return }

    Write-Step "首次运行：正在准备安装包自带的运行环境"
    $staging = Join-Path $RuntimeDir "python.installing"
    Remove-OwnedRuntimePath $staging
    New-Item -ItemType Directory -Force -Path $staging | Out-Null
    try {
        Expand-Archive -LiteralPath $archive -DestinationPath $staging -Force
        $stagingPython = Join-Path $staging "python.exe"
        if (-not (Test-BundledPython $stagingPython)) {
            throw "解压后的内置 Python 无法运行。请确认系统为 64 位 Windows 10/11。"
        }
        Remove-OwnedRuntimePath $BundledPythonDir
        Move-Item -LiteralPath $staging -Destination $BundledPythonDir
    }
    catch {
        Remove-OwnedRuntimePath $staging
        throw
    }
}

function Set-IsolatedPythonEnvironment([string]$CandidatePackages = $PackagesDir) {
    $separator = [System.IO.Path]::PathSeparator
    $env:PYTHONPATH = "$BackendDir$separator$CandidatePackages"
    $env:PYTHONNOUSERSITE = "1"
    $env:PYTHONDONTWRITEBYTECODE = "1"
    $env:SPEECH_TRAINER_MODEL_DIR = $PackagedModelDir
    Remove-Item Env:PYTHONHOME -ErrorAction SilentlyContinue
    $env:PATH = "$BundledPythonDir$separator$env:PATH"
}

function Test-PackagedWheelhouse($Manifest) {
    if (-not (Test-Path -LiteralPath $RequirementsFile -PathType Leaf)) {
        throw "安装包缺少依赖清单。请重新下载并完整解压新版安装包。"
    }
    $requirementsHash = (Get-FileHash -LiteralPath $RequirementsFile -Algorithm SHA256).Hash.ToLowerInvariant()
    if ($requirementsHash -ne ([string]$Manifest.requirements_sha256).ToLowerInvariant()) {
        throw "安装包依赖清单校验失败。请重新下载并完整解压新版安装包。"
    }

    $wheelProperties = @($Manifest.wheels.PSObject.Properties)
    if ($wheelProperties.Count -eq 0) {
        throw "安装包没有可用的离线依赖。请重新下载完整的新版安装包。"
    }
    foreach ($wheel in $wheelProperties) {
        $wheelName = [string]$wheel.Name
        if ([System.IO.Path]::GetFileName($wheelName) -ne $wheelName) {
            throw "安装包离线依赖清单无效。请重新下载完整的新版安装包。"
        }
        $wheelPath = Join-Path $WheelhouseDir $wheelName
        if (-not (Test-Path -LiteralPath $wheelPath -PathType Leaf)) {
            throw "安装包缺少离线依赖文件。请重新下载并完整解压新版安装包。"
        }
        $wheelHash = (Get-FileHash -LiteralPath $wheelPath -Algorithm SHA256).Hash.ToLowerInvariant()
        if ($wheelHash -ne ([string]$wheel.Value).ToLowerInvariant()) {
            throw "安装包离线依赖校验失败。请删除当前副本后重新下载并解压。"
        }
    }
}

function Test-InstalledDependencies([string]$CandidatePackages) {
    if (-not (Test-Path -LiteralPath $CandidatePackages -PathType Container)) { return $false }
    Set-IsolatedPythonEnvironment $CandidatePackages
    try {
        & $BundledPython -c "import fastapi,numpy,scipy,sherpa_onnx,sqlalchemy,uvicorn,websockets" 2>$null
        return $LASTEXITCODE -eq 0
    }
    catch {
        return $false
    }
}

function Ensure-Dependencies {
    Ensure-BundledPython
    $wheelhouse = Read-JsonFile $WheelhouseManifestFile "离线依赖清单"
    Test-PackagedWheelhouse $wheelhouse

    $runtimeManifest = Read-JsonFile $RuntimeManifestFile "内置 Python 清单"
    $expectedMarker = "$($runtimeManifest.archive_sha256)|$($wheelhouse.requirements_sha256)"
    if ((Test-Path -LiteralPath $DependencyMarker -PathType Leaf) -and (Test-InstalledDependencies $PackagesDir)) {
        $actualMarker = (Get-Content -LiteralPath $DependencyMarker -Raw).Trim()
        if ($actualMarker -eq $expectedMarker) {
            Set-IsolatedPythonEnvironment
            return
        }
    }

    Write-Step "首次运行：正在安装随包附带的程序组件（无需下载）"
    $staging = Join-Path $RuntimeDir "python-packages.installing"
    Remove-OwnedRuntimePath $staging
    New-Item -ItemType Directory -Force -Path $staging | Out-Null
    try {
        & $BundledPython -I -m pip install --disable-pip-version-check --no-index `
            --no-compile --target $staging --requirement $RequirementsFile `
            --find-links $WheelhouseDir
        if ($LASTEXITCODE -ne 0) { throw "安装随包程序组件失败。" }

        if (-not (Test-InstalledDependencies $staging)) {
            throw "随包程序组件安装后未通过自检。"
        }

        Remove-OwnedRuntimePath $PackagesDir
        Move-Item -LiteralPath $staging -Destination $PackagesDir
        Set-IsolatedPythonEnvironment
        Set-Content -LiteralPath $DependencyMarker -Value $expectedMarker -Encoding ASCII
    }
    catch {
        Remove-OwnedRuntimePath $staging
        throw
    }
}

function Test-SpeechTrainer {
    try {
        $health = Invoke-RestMethod -Uri "${AppUrl}api/health" -TimeoutSec 2
        return $health.status -eq "ok"
    }
    catch {
        return $false
    }
}

function Stop-SpeechTrainer {
    if (-not (Test-Path -LiteralPath $PidFile)) {
        Write-Host "训练器当前没有由本启动包记录的运行进程。"
        return
    }

    $recorded = (Get-Content -LiteralPath $PidFile -Raw).Trim()
    if ($recorded -notmatch "^\d+$") {
        throw "PID 记录无效，已拒绝停止未知进程。"
    }

    $process = Get-CimInstance Win32_Process -Filter "ProcessId = $recorded" -ErrorAction SilentlyContinue
    if ($null -eq $process) {
        Remove-Item -LiteralPath $PidFile -Force
        Write-Host "训练器已经停止。"
        return
    }

    $expectedPython = [System.IO.Path]::GetFullPath($BundledPython)
    $actualPython = [System.IO.Path]::GetFullPath([string]$process.ExecutablePath)
    $commandLine = [string]$process.CommandLine
    if (
        $actualPython -ne $expectedPython -or
        $commandLine -notlike "*uvicorn*" -or
        $commandLine -notlike "*app.main:app*"
    ) {
        throw "PID 对应的不是本测试包进程，已拒绝停止。"
    }

    Stop-Process -Id ([int]$recorded) -Force
    Remove-Item -LiteralPath $PidFile -Force
    Write-Host "训练器已停止。"
}

function Ensure-LocalModels {
    $checkCode = @"
from app.providers.asr.sherpa_local import local_finalizer_ready, local_model_ready
raise SystemExit(0 if local_model_ready() and local_finalizer_ready() else 1)
"@
    & $BundledPython -c $checkCode
    if ($LASTEXITCODE -eq 0) { return }
    throw "安装包内置语音模型不完整。请删除当前副本，重新下载新版完整安装包并解压。"
}

try {
    New-Item -ItemType Directory -Force -Path $RuntimeDir, $LogDir | Out-Null
    try {
        Start-Transcript -Path $LauncherLog -Append | Out-Null
        $TranscriptStarted = $true
    }
    catch {
        # Startup can continue even if transcript support is unavailable. The CMD
        # wrapper still keeps failures visible and writes its bootstrap log.
    }

    Write-Host "Speech Trainer launcher"
    Write-Host "PowerShell: $($PSVersionTable.PSVersion)"
    Write-Host "Package: $PackageRoot"

    if ($Stop) {
        Stop-SpeechTrainer
        return
    }

    if (Test-SpeechTrainer) {
        Write-Host "训练器已经在运行，正在打开浏览器。"
        Start-Process $AppUrl
        Set-Content -LiteralPath $LaunchStatusFile -Value "ready" -Encoding ASCII
        return
    }

    if (Test-Path -LiteralPath (Join-Path $BackendDir ".env")) {
        & attrib.exe +h (Join-Path $BackendDir ".env") 2>$null | Out-Null
    }

    $listeners = @(
        Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue
    )
    if ($listeners.Count -gt 0) {
        throw "端口 $Port 已被其他程序占用。为避免影响其他项目，训练器未启动，也没有终止任何进程。"
    }

    Ensure-Dependencies
    Ensure-LocalModels

    Write-Step "正在启动表达能力训练器"
    $arguments = @(
        "-m", "uvicorn", "app.main:app",
        "--host", "127.0.0.1",
        "--port", [string]$Port
    )
    $process = Start-Process -FilePath $BundledPython -ArgumentList $arguments `
        -WorkingDirectory $BackendDir -WindowStyle Hidden -PassThru `
        -RedirectStandardOutput $StdoutLog -RedirectStandardError $StderrLog
    Set-Content -LiteralPath $PidFile -Value $process.Id -Encoding ASCII

    $ready = $false
    for ($attempt = 0; $attempt -lt 60; $attempt += 1) {
        Start-Sleep -Milliseconds 500
        if (Test-SpeechTrainer) {
            $ready = $true
            break
        }
        if ($process.HasExited) { break }
    }
    if (-not $ready) {
        if (-not $process.HasExited) { Stop-Process -Id $process.Id -Force }
        Remove-Item -LiteralPath $PidFile -Force -ErrorAction SilentlyContinue
        $tail = if (Test-Path -LiteralPath $StderrLog) { Get-Content -LiteralPath $StderrLog -Tail 20 } else { @() }
        throw "启动失败。最近日志：`n$($tail -join "`n")"
    }

    Write-Host ""
    Write-Host "启动成功：$AppUrl" -ForegroundColor Green
    Write-Host "关闭浏览器不会停止服务；需要停止时双击「停止训练器.cmd」。"
    Start-Process $AppUrl
    Set-Content -LiteralPath $LaunchStatusFile -Value "ready" -Encoding ASCII
}
catch {
    Write-Host ""
    Write-Host "启动失败：$($_.Exception.Message)" -ForegroundColor Red
    Write-Host "日志目录：$LogDir"
    Write-Host ""
    exit 1
}
finally {
    if ($TranscriptStarted) {
        try { Stop-Transcript | Out-Null } catch { }
    }
}
