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
$RuntimeDir = Join-Path $PackageRoot ".runtime"
$VenvDir = Join-Path $RuntimeDir "venv"
$VenvPython = Join-Path $VenvDir "Scripts\python.exe"
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
    if (-not (Test-Path $PidFile)) {
        Write-Host "训练器当前没有由本启动包记录的运行进程。"
        return
    }

    $recorded = (Get-Content $PidFile -Raw).Trim()
    if ($recorded -notmatch "^\d+$") {
        throw "PID 记录无效，已拒绝停止未知进程。"
    }

    $process = Get-CimInstance Win32_Process -Filter "ProcessId = $recorded" -ErrorAction SilentlyContinue
    if ($null -eq $process) {
        Remove-Item $PidFile -Force
        Write-Host "训练器已经停止。"
        return
    }

    $expectedPython = [System.IO.Path]::GetFullPath($VenvPython)
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
    Remove-Item $PidFile -Force
    Write-Host "训练器已停止。"
}

function Test-PythonCandidate([string]$FilePath, [string[]]$PrefixArgs) {
    try {
        $probe = & $FilePath @PrefixArgs -c "import struct, sys; print(f'{sys.version_info.major}.{sys.version_info.minor}|{struct.calcsize(`"P`") * 8}')" 2>$null
        $parts = [string]$probe -split "\|"
        $version = $parts[0]
        $bits = if ($parts.Count -gt 1) { $parts[1] } else { "" }
        if (
            $LASTEXITCODE -ne 0 -or
            $version -notin @("3.11", "3.12") -or
            $bits -ne "64"
        ) {
            return $null
        }
        return [PSCustomObject]@{
            File = $FilePath
            PrefixArgs = $PrefixArgs
            Version = $version
        }
    }
    catch {
        return $null
    }
}

function Find-CompatiblePython {
    $pyLauncher = Get-Command "py.exe" -ErrorAction SilentlyContinue
    if ($null -ne $pyLauncher) {
        foreach ($versionArg in @("-3.12", "-3.11")) {
            $candidate = Test-PythonCandidate $pyLauncher.Source @($versionArg)
            if ($null -ne $candidate) { return $candidate }
        }
    }

    $pythonCommand = Get-Command "python.exe" -ErrorAction SilentlyContinue
    if ($null -ne $pythonCommand) {
        $candidate = Test-PythonCandidate $pythonCommand.Source @()
        if ($null -ne $candidate) { return $candidate }
    }

    $knownRoots = @(
        (Join-Path $env:LOCALAPPDATA "Programs\Python\Python312\python.exe"),
        (Join-Path $env:LOCALAPPDATA "Programs\Python\Python311\python.exe")
    )
    foreach ($knownPath in $knownRoots) {
        if (Test-Path $knownPath) {
            $candidate = Test-PythonCandidate $knownPath @()
            if ($null -ne $candidate) { return $candidate }
        }
    }
    return $null
}

function Install-CompatiblePython {
    $winget = Get-Command "winget.exe" -ErrorAction SilentlyContinue
    if ($null -eq $winget) {
        throw "没有找到 Python 3.11/3.12，也没有找到 winget。请先从 python.org 安装 64 位 Python 3.12，再重新双击启动。"
    }

    Write-Step "首次运行：正在安装 Python 3.12（仅当前用户）"
    & $winget.Source install --id Python.Python.3.12 -e --scope user --silent `
        --accept-package-agreements --accept-source-agreements --disable-interactivity
    if ($LASTEXITCODE -ne 0) {
        throw "Python 自动安装失败。请手动安装 64 位 Python 3.12 后重试。"
    }
}

function Ensure-Dependencies {
    if ((Test-Path $VenvPython) -and (Test-Path $DependencyMarker)) {
        return
    }

    $python = Find-CompatiblePython
    if ($null -eq $python) {
        Install-CompatiblePython
        $python = Find-CompatiblePython
    }
    if ($null -eq $python) {
        throw "Python 3.12 已请求安装，但当前终端仍无法找到它。请关闭窗口后重新双击启动。"
    }

    New-Item -ItemType Directory -Force -Path $RuntimeDir | Out-Null
    if (-not (Test-Path $VenvPython)) {
        Write-Step "首次运行：正在创建独立运行环境"
        $prefixArgs = @($python.PrefixArgs)
        & $python.File @prefixArgs -m venv $VenvDir
        if ($LASTEXITCODE -ne 0) { throw "创建 Python 运行环境失败。" }
    }

    Write-Step "首次运行：正在安装训练器依赖（通常需要 3–8 分钟）"
    & $VenvPython -m pip install --disable-pip-version-check --upgrade pip
    if ($LASTEXITCODE -ne 0) { throw "pip 更新失败。" }
    & $VenvPython -m pip install --disable-pip-version-check -e $BackendDir
    if ($LASTEXITCODE -ne 0) { throw "训练器依赖安装失败。" }
    Set-Content -Path $DependencyMarker -Value "ready" -Encoding ASCII
}

function Ensure-LocalModels {
    $checkCode = @"
from app.providers.asr.sherpa_local import local_finalizer_ready, local_model_ready
raise SystemExit(0 if local_model_ready() and local_finalizer_ready() else 1)
"@
    & $VenvPython -c $checkCode
    if ($LASTEXITCODE -eq 0) { return }

    Write-Step "首次运行：正在下载本地语音模型（约 300 MB，解压后约 410 MB）"
    & $VenvPython (Join-Path $BackendDir "scripts\install_local_asr.py")
    if ($LASTEXITCODE -ne 0) {
        throw "本地语音模型安装失败。请检查网络后重新双击启动，已下载完成的文件会自动校验。"
    }
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
        Set-Content -Path $LaunchStatusFile -Value "ready" -Encoding ASCII
        return
    }

    if (Test-Path (Join-Path $BackendDir ".env")) {
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
    $process = Start-Process -FilePath $VenvPython -ArgumentList $arguments `
        -WorkingDirectory $BackendDir -WindowStyle Hidden -PassThru `
        -RedirectStandardOutput $StdoutLog -RedirectStandardError $StderrLog
    Set-Content -Path $PidFile -Value $process.Id -Encoding ASCII

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
        Remove-Item $PidFile -Force -ErrorAction SilentlyContinue
        $tail = if (Test-Path $StderrLog) { Get-Content $StderrLog -Tail 20 } else { @() }
        throw "启动失败。最近日志：`n$($tail -join "`n")"
    }

    Write-Host ""
    Write-Host "启动成功：$AppUrl" -ForegroundColor Green
    Write-Host "关闭浏览器不会停止服务；需要停止时双击「停止训练器.cmd」。"
    Start-Process $AppUrl
    Set-Content -Path $LaunchStatusFile -Value "ready" -Encoding ASCII
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
