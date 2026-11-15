#requires -Version 5.1
<#
准备桌面版内置 Python 运行时（embeddable distribution）。

用法：
  pwsh -File build/prepare_runtime.ps1
  pwsh -File build/prepare_runtime.ps1 -Python E:\code\py_venv\.venv\Scripts\python.exe
  pwsh -File build/prepare_runtime.ps1 -Force          # 重新下载并解压（删旧运行时前有边界断言）
  pwsh -File build/prepare_runtime.ps1 -Upgrade        # 允许 pip --upgrade（默认不加，避免偏离 requirements 钉死版本）
  pwsh -File build/prepare_runtime.ps1 -PruneBytecode  # 额外剔除 *.pyc/__pycache__（体积更小，但每次启动要重编译）
  pwsh -File build/prepare_runtime.ps1 -SkipPrune      # 保留 site-packages 里的 tests 目录（默认剔除）

产物：apps/desktop/runtime/python/（已 gitignore，不进仓库）
所有路径由脚本自身位置推导，不含硬编码盘符。

安全边界：任何 Remove-Item -Recurse 之前都过 Assert-SafeDelete，
要求目标位于 $DesktopDir 之下且目录结构为 runtime/python，变量算错时直接 throw。
#>
[CmdletBinding()]
param(
    [string]$Python = $(if ($env:YIZHI_PYTHON) { $env:YIZHI_PYTHON } else { "python" }),
    [string]$PythonVersion = "3.14.2",
    [switch]$Force,
    [switch]$Upgrade,
    [switch]$PruneBytecode,
    [switch]$SkipPrune
)

$ErrorActionPreference = "Stop"

# ---- 常量 ----
$MinArchiveBytes = 8MB          # 低于这个大小基本是错误页/半截文件
$DownloadAttempts = 3
$DownloadRetrySleepSec = 2
$PruneTestDirNames = @("tests", "test")
$RuntimeLeafName = "python"
$RuntimeParentLeafName = "runtime"

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

function Write-Warn2([string]$Message) {
    Write-Host "!!  $Message" -ForegroundColor Yellow
}

<#
边界断言：只允许删除「位于指定父目录之下、且（可选）指定层级名称」的路径。
传错变量时直接 throw，绝不落到 Remove-Item。
#>
function Assert-SafeDelete {
    param(
        [Parameter(Mandatory = $true)][string]$Path,
        [Parameter(Mandatory = $true)][string]$MustBeUnder,
        [string]$ExpectedLeaf,
        [string]$ExpectedParentLeaf
    )

    if ([string]::IsNullOrWhiteSpace($Path)) { throw "拒绝删除：路径为空" }

    $sep = [IO.Path]::DirectorySeparatorChar
    $full = [IO.Path]::GetFullPath($Path).TrimEnd('\', '/')
    $parent = [IO.Path]::GetFullPath($MustBeUnder).TrimEnd('\', '/')

    if ([string]::IsNullOrWhiteSpace($full) -or [string]::IsNullOrWhiteSpace($parent)) {
        throw "拒绝删除：路径解析失败（$Path）"
    }
    if ($full -eq $parent) { throw "拒绝删除：目标等于父目录（$full）" }

    $prefix = $parent + $sep
    if (-not $full.StartsWith($prefix, [StringComparison]::OrdinalIgnoreCase)) {
        throw "拒绝删除：$full 不在 $parent 之下"
    }
    if ($ExpectedLeaf -and ([IO.Path]::GetFileName($full) -ne $ExpectedLeaf)) {
        throw "拒绝删除：$full 的目录名不是 '$ExpectedLeaf'"
    }
    if ($ExpectedParentLeaf) {
        $parentOfTarget = [IO.Path]::GetDirectoryName($full)
        if ([IO.Path]::GetFileName($parentOfTarget) -ne $ExpectedParentLeaf) {
            throw "拒绝删除：$full 的父目录不是 '$ExpectedParentLeaf'"
        }
    }
}

function Remove-SafePath {
    param(
        [Parameter(Mandatory = $true)][string]$Path,
        [Parameter(Mandatory = $true)][string]$MustBeUnder,
        [string]$ExpectedLeaf,
        [string]$ExpectedParentLeaf,
        [switch]$Recurse
    )
    if (-not (Test-Path -LiteralPath $Path)) { return }
    Assert-SafeDelete -Path $Path -MustBeUnder $MustBeUnder -ExpectedLeaf $ExpectedLeaf -ExpectedParentLeaf $ExpectedParentLeaf
    Write-Step "删除 $Path"
    Remove-Item -LiteralPath $Path -Recurse:$Recurse -Force
}

<# 校验 embeddable 压缩包：大小合理 + 真的能解开且含 python.exe。 #>
function Test-PythonArchive {
    param([Parameter(Mandatory = $true)][string]$Path)

    if (-not (Test-Path -LiteralPath $Path)) { return $false }
    $size = (Get-Item -LiteralPath $Path).Length
    if ($size -lt $MinArchiveBytes) {
        Write-Warn2 ("压缩包只有 {0:N1} MB，低于 {1} MB，视为损坏：{2}" -f ($size / 1MB), ($MinArchiveBytes / 1MB), $Path)
        return $false
    }

    $probe = Join-Path ([IO.Path]::GetTempPath()) ("yizhi-runtime-probe-" + [Guid]::NewGuid().ToString("N"))
    try {
        New-Item -ItemType Directory -Force -Path $probe | Out-Null
        Expand-Archive -LiteralPath $Path -DestinationPath $probe -Force -ErrorAction Stop
        if (-not (Test-Path -LiteralPath (Join-Path $probe "python.exe"))) {
            Write-Warn2 "压缩包解压后没有 python.exe，视为无效"
            return $false
        }
        return $true
    } catch {
        Write-Warn2 "压缩包无法解压：$($_.Exception.Message)"
        return $false
    } finally {
        if (Test-Path -LiteralPath $probe) {
            Remove-Item -LiteralPath $probe -Recurse -Force -ErrorAction SilentlyContinue
        }
    }
}

<# 下载并校验，最多 $DownloadAttempts 次，间隔 $DownloadRetrySleepSec 秒。 #>
function Get-PythonArchive {
    param(
        [Parameter(Mandatory = $true)][string]$Uri,
        [Parameter(Mandatory = $true)][string]$OutFile
    )

    New-Item -ItemType Directory -Force -Path (Split-Path -Parent $OutFile) | Out-Null

    for ($attempt = 1; $attempt -le $DownloadAttempts; $attempt += 1) {
        Write-Step "下载（第 $attempt/$DownloadAttempts 次）$Uri"
        try {
            if (Test-Path -LiteralPath $OutFile) { Remove-Item -LiteralPath $OutFile -Force }
            Invoke-WebRequest -Uri $Uri -OutFile $OutFile -UseBasicParsing -ErrorAction Stop
            if (Test-PythonArchive -Path $OutFile) {
                Write-Step "下载完成并校验通过：$(Split-Path -Leaf $OutFile)"
                return
            }
            throw "校验失败（大小或内容不完整）"
        } catch {
            Write-Warn2 "第 $attempt 次下载失败：$($_.Exception.Message)"
            if (Test-Path -LiteralPath $OutFile) {
                Remove-Item -LiteralPath $OutFile -Force -ErrorAction SilentlyContinue
            }
            if ($attempt -lt $DownloadAttempts) {
                Start-Sleep -Seconds $DownloadRetrySleepSec
            }
        }
    }

    throw "下载失败：$Uri（已重试 $DownloadAttempts 次），请检查网络或手动放入 $OutFile"
}

<# 读 site-packages 里已安装的版本（只看 *.dist-info，不启动 pip，速度快）。 #>
function Get-InstalledVersions {
    param([Parameter(Mandatory = $true)][string]$SitePackagesDir)

    $map = @{}
    if (-not (Test-Path -LiteralPath $SitePackagesDir)) { return $map }
    foreach ($dir in Get-ChildItem -LiteralPath $SitePackagesDir -Directory -Filter "*.dist-info") {
        if ($dir.Name -match '^(?<name>.+)-(?<ver>[0-9][^-]*)\.dist-info$') {
            $name = $Matches['name'].ToLowerInvariant() -replace '[-_.]+', '-'
            $map[$name] = $Matches['ver']
        }
    }
    return $map
}

<# requirements.txt 里钉死 == 的版本与已安装版本比对，只告警不中断。 #>
function Test-RequirementsMatch {
    param(
        [Parameter(Mandatory = $true)][string]$RequirementsPath,
        [Parameter(Mandatory = $true)][string]$SitePackagesDir
    )

    if (-not (Test-Path -LiteralPath $RequirementsPath)) {
        Write-Warn2 "找不到 $RequirementsPath，跳过版本比对"
        return
    }

    $installed = Get-InstalledVersions -SitePackagesDir $SitePackagesDir
    $mismatch = @()
    foreach ($line in Get-Content -LiteralPath $RequirementsPath) {
        $trimmed = ($line -replace '#.*$', '').Trim()
        if (-not $trimmed) { continue }
        if ($trimmed -notmatch '^(?<name>[A-Za-z0-9_.\-]+)\s*==\s*(?<ver>[^\s;]+)$') { continue }
        $name = $Matches['name'].ToLowerInvariant() -replace '[-_.]+', '-'
        $want = $Matches['ver']
        $have = $installed[$name]
        if (-not $have) {
            $mismatch += "$name 未安装（requirements=$want）"
        } elseif ($have -ne $want) {
            $mismatch += "$name 已装 $have ≠ requirements $want"
        }
    }

    if ($mismatch.Count -gt 0) {
        Write-Warn2 "内置运行时与 requirements.txt 钉死版本不一致（用 -Force 或 -Upgrade 重建可修正）："
        foreach ($item in $mismatch) { Write-Warn2 "  - $item" }
    } else {
        Write-Step "版本比对通过：所有 == 钉死项与 requirements.txt 一致"
    }
}

<# 剔除 site-packages 里无用的大目录（tests/ 等），以及可选的字节码。 #>
function Invoke-Prune {
    param([Parameter(Mandatory = $true)][string]$SitePackagesDir)

    $before = (Get-ChildItem -LiteralPath $SitePackagesDir -Recurse -File -ErrorAction SilentlyContinue |
        Measure-Object Length -Sum).Sum

    if (-not $SkipPrune) {
        $removed = 0
        $testDirs = Get-ChildItem -LiteralPath $SitePackagesDir -Recurse -Directory -ErrorAction SilentlyContinue |
            Where-Object { $PruneTestDirNames -contains $_.Name.ToLowerInvariant() }
        foreach ($dir in $testDirs) {
            # 只删 site-packages 之下的 tests 目录
            Assert-SafeDelete -Path $dir.FullName -MustBeUnder $SitePackagesDir
            Remove-Item -LiteralPath $dir.FullName -Recurse -Force -ErrorAction SilentlyContinue
            $removed += 1
        }
        Write-Step "剔除测试目录 $removed 个"
    } else {
        Write-Step "按 -SkipPrune 保留测试目录"
    }

    # pip 生成的 bin/*.exe 启动器内嵌构建机 Python 绝对路径；应用不需要这些命令，删掉
    $binDir = Join-Path $SitePackagesDir "bin"
    if (Test-Path -LiteralPath $binDir) {
        Remove-Item -LiteralPath $binDir -Recurse -Force -ErrorAction SilentlyContinue
        Write-Step "剔除 pip 启动器目录 site-packages/bin"
    }

    if ($PruneBytecode) {
        $pyc = Get-ChildItem -LiteralPath $SitePackagesDir -Recurse -File -Filter "*.pyc" -ErrorAction SilentlyContinue
        $pycCount = @($pyc).Count
        foreach ($file in $pyc) {
            Remove-Item -LiteralPath $file.FullName -Force -ErrorAction SilentlyContinue
        }
        $cacheDirs = Get-ChildItem -LiteralPath $SitePackagesDir -Recurse -Directory -Filter "__pycache__" -ErrorAction SilentlyContinue
        foreach ($dir in $cacheDirs) {
            Remove-Item -LiteralPath $dir.FullName -Recurse -Force -ErrorAction SilentlyContinue
        }
        Write-Step "剔除字节码 $pycCount 个 + __pycache__ $(@($cacheDirs).Count) 个"
    }

    $after = (Get-ChildItem -LiteralPath $SitePackagesDir -Recurse -File -ErrorAction SilentlyContinue |
        Measure-Object Length -Sum).Sum
    $saved = [math]::Round((($before - $after) / 1MB), 1)
    Write-Step "site-packages：$([math]::Round($before / 1MB, 1)) MB → $([math]::Round($after / 1MB, 1)) MB（省 $saved MB）"
}

# ---- 0. 路径边界自检（防变量算错）----
Assert-SafeDelete -Path $RuntimeDir -MustBeUnder $DesktopDir -ExpectedLeaf $RuntimeLeafName -ExpectedParentLeaf $RuntimeParentLeafName
if ([IO.Path]::GetFileName($RuntimeRoot) -ne "runtime") {
    throw "运行时根目录预期以 runtime 结尾，实际：$RuntimeRoot"
}

# ---- 1. 可选清理旧运行时 ----
if ($Force -and (Test-Path -LiteralPath $RuntimeDir)) {
    Remove-SafePath -Path $RuntimeDir -MustBeUnder $DesktopDir -ExpectedLeaf $RuntimeLeafName -ExpectedParentLeaf $RuntimeParentLeafName -Recurse
}

# ---- 2. 下载（带重试与完整性校验）----
if (Test-PythonArchive -Path $Archive) {
    Write-Step "复用缓存压缩包 $Archive"
} else {
    if (Test-Path -LiteralPath $Archive) {
        Write-Warn2 "已有压缩包不可用，重新下载"
        Remove-SafePath -Path $Archive -MustBeUnder $CacheDir -ExpectedLeaf (Split-Path -Leaf $Archive)
    }
    Get-PythonArchive -Uri $DownloadUrl -OutFile $Archive
}

# ---- 3. 解压 ----
if (-not (Test-Path -LiteralPath (Join-Path $RuntimeDir "python.exe"))) {
    Write-Step "解压到 $RuntimeDir"
    New-Item -ItemType Directory -Force -Path $RuntimeDir | Out-Null
    Expand-Archive -LiteralPath $Archive -DestinationPath $RuntimeDir -Force
} else {
    Write-Step "复用已解压运行时 $RuntimeDir"
}

# ---- 4. 放开 site 并加入 site-packages ----
$PthFile = Get-ChildItem -LiteralPath $RuntimeDir -Filter "python*._pth" | Select-Object -First 1
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

# ---- 5. 安装后端依赖（默认不加 --upgrade，保持 requirements 钉死版本）----
Write-Step "安装依赖到 $SitePackages"
New-Item -ItemType Directory -Force -Path $SitePackages | Out-Null
$pipArgs = @(
    "-m", "pip", "install",
    "--disable-pip-version-check",
    "--no-warn-script-location",
    "--target", $SitePackages,
    "-r", $Requirements
)
if ($Upgrade) { $pipArgs += "--upgrade" }
& $Python @pipArgs
if ($LASTEXITCODE -ne 0) {
    throw "pip install 失败（退出码 $LASTEXITCODE）"
}

# ---- 6. 版本一致性比对（钉死版本 vs 实际安装）----
Test-RequirementsMatch -RequirementsPath $Requirements -SitePackagesDir $SitePackages

# ---- 7. 剔除无用大目录 ----
Invoke-Prune -SitePackagesDir $SitePackages

# ---- 8. 自检 ----
Write-Step "自检：内置 Python 版本与关键依赖"
$EmbeddedPython = Join-Path $RuntimeDir "python.exe"
& $EmbeddedPython -c "import sys, fastapi, uvicorn, sympy, pydantic; print(sys.version.split()[0], fastapi.__version__, uvicorn.__version__, sympy.__version__, pydantic.VERSION)"
if ($LASTEXITCODE -ne 0) {
    throw "内置 Python 自检失败"
}

$SizeMb = [math]::Round(((Get-ChildItem -LiteralPath $RuntimeDir -Recurse -File | Measure-Object Length -Sum).Sum / 1MB), 1)
Write-Host ""
Write-Host "运行时就绪：$RuntimeDir" -ForegroundColor Green
Write-Host "体积：$SizeMb MB" -ForegroundColor Green
