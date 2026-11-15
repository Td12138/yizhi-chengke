#requires -Version 5.1
<#
准备桌面版内置 Python 运行时（embeddable distribution）。

用法：
  pwsh -File build/prepare_runtime.ps1
  pwsh -File build/prepare_runtime.ps1 -Python E:\code\py_venv\.venv\Scripts\python.exe
  pwsh -File build/prepare_runtime.ps1 -Force        # 重新下载解压

产物：apps/desktop/runtime/python/（已 gitignore，不进仓库）
所有路径由脚本自身位置推导，不含硬编码盘符。
#>
[CmdletBinding()]
param(
    [string]$Python = $(if ($env:YIZHI_PYTHON) { $env:YIZHI_PYTHON } else { "python" }),
    [string]$PythonVersion = "3.14.2",
    [switch]$Force
)

$ErrorActionPreference = "Stop"

$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$DesktopDir = Split-Path -Parent $ScriptDir
$AppsDir = Split-Path -Parent $DesktopDir
$RepoRoot = Split-Path -Parent $AppsDir

$RuntimeRoot = Join-Path $DesktopDir "runtime"
$RuntimeDir = Join-Path $RuntimeRoot "python"
$SitePackages = Join-Path $RuntimeDir "Lib\site-packages"
$CacheDir = Join-Path $ScriptDir ".cache"
$Archive = Join-Path $CacheDir "python-$PythonVersion-embed-amd64.zip"
$DownloadUrl = "https://www.python.org/ftp/python/$PythonVersion/python-$PythonVersion-embed-amd64.zip"
$Requirements = Join-Path $RepoRoot "apps\server\requirements.txt"

function Write-Step([string]$Message) {
    Write-Host "==> $Message" -ForegroundColor Cyan
}

if ($Force -and (Test-Path $RuntimeDir)) {
    Write-Step "清理旧运行时 $RuntimeDir"
    Remove-Item -Recurse -Force $RuntimeDir
}

if (-not (Test-Path $Archive)) {
    New-Item -ItemType Directory -Force -Path $CacheDir | Out-Null
    Write-Step "下载 $DownloadUrl"
    Invoke-WebRequest -Uri $DownloadUrl -OutFile $Archive
}

if (-not (Test-Path (Join-Path $RuntimeDir "python.exe"))) {
    Write-Step "解压到 $RuntimeDir"
    New-Item -ItemType Directory -Force -Path $RuntimeDir | Out-Null
    Expand-Archive -Path $Archive -DestinationPath $RuntimeDir -Force
}

# ---- 放开 site 并加入 site-packages ----
$PthFile = Get-ChildItem -Path $RuntimeDir -Filter "python*._pth" | Select-Object -First 1
if (-not $PthFile) {
    throw "未找到 python*._pth，解压可能不完整：$RuntimeDir"
}
$PthLines = Get-Content -LiteralPath $PthFile.FullName -Encoding UTF8
$NewLines = @()
foreach ($line in $PthLines) {
    if ($line -match '^\s*#\s*import\s+site\s*$') {
        $NewLines += "import site"
    } else {
        $NewLines += $line
    }
}
if ($NewLines -notcontains "Lib\site-packages") {
    $NewLines += "Lib\site-packages"
}
Set-Content -LiteralPath $PthFile.FullName -Value $NewLines -Encoding UTF8
Write-Step "已更新 $($PthFile.Name)：import site + Lib\site-packages"

# ---- 安装后端依赖到内置运行时 ----
Write-Step "安装依赖到 $SitePackages"
New-Item -ItemType Directory -Force -Path $SitePackages | Out-Null
& $Python -m pip install --disable-pip-version-check --no-warn-script-location --upgrade `
    --target $SitePackages -r $Requirements
if ($LASTEXITCODE -ne 0) {
    throw "pip install 失败（退出码 $LASTEXITCODE）"
}

# ---- 自检 ----
Write-Step "自检：内置 Python 版本与关键依赖"
$EmbeddedPython = Join-Path $RuntimeDir "python.exe"
& $EmbeddedPython -c "import sys, fastapi, uvicorn, sympy, pydantic; print(sys.version.split()[0], fastapi.__version__, uvicorn.__version__, sympy.__version__, pydantic.VERSION)"
if ($LASTEXITCODE -ne 0) {
    throw "内置 Python 自检失败"
}

$SizeMb = [math]::Round(((Get-ChildItem $RuntimeDir -Recurse -File | Measure-Object Length -Sum).Sum / 1MB), 1)
Write-Host ""
Write-Host "运行时就绪：$RuntimeDir" -ForegroundColor Green
Write-Host "体积：$SizeMb MB" -ForegroundColor Green
