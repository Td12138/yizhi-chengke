#requires -Version 5.1
<#
把 win-unpacked 压成交付用的 zip，并生成中文命名的 portable 副本。

用法（在 apps/desktop 下）：pwsh -File build/package_zip.ps1
前置：已执行 npm run package 或 npm run package:dir

安全边界：
  - 所有路径由脚本自身位置推导，无硬编码盘符
  - 删除/覆盖只发生在 release/ 之下，且删除前过 Assert-SafeDelete
  - 本脚本不递归删除任何目录（只有单个 zip 文件的覆盖）
#>
[CmdletBinding()]
param()

$ErrorActionPreference = "Stop"

# ---- 常量 ----
$ReleaseLeafName = "release"
$UnpackedLeafName = "win-unpacked"
$AppExeName = "一纸成课.exe"
$PortablePattern = "YizhiChengke-*-portable.exe"
$PortableCopyName = "一纸成课-portable.exe"

$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$DesktopDir = Split-Path -Parent $ScriptDir
$ReleaseDir = Join-Path $DesktopDir $ReleaseLeafName
$UnpackedDir = Join-Path $ReleaseDir $UnpackedLeafName
# 版本从 package.json 读，保证 zip 名与 Release/README 的 0.1.x 一致
$Version = (Get-Content -LiteralPath (Join-Path $DesktopDir "package.json") -Raw | ConvertFrom-Json).version
$ZipFileName = "yizhi-chengke-$Version-win64.zip"
$ZipPath = Join-Path $ReleaseDir $ZipFileName
$PortableCopy = Join-Path $ReleaseDir $PortableCopyName

<# 断言：路径必须位于父目录之下（用于所有删除/覆盖操作前）。 #>
function Assert-SafeDelete {
    param(
        [Parameter(Mandatory = $true)][string]$Path,
        [Parameter(Mandatory = $true)][string]$MustBeUnder,
        [string]$ExpectedLeaf
    )

    if ([string]::IsNullOrWhiteSpace($Path)) { throw "拒绝操作：路径为空" }

    $sep = [IO.Path]::DirectorySeparatorChar
    $full = [IO.Path]::GetFullPath($Path).TrimEnd('\', '/')
    $parent = [IO.Path]::GetFullPath($MustBeUnder).TrimEnd('\', '/')

    if ($full -eq $parent) { throw "拒绝操作：目标等于父目录（$full）" }
    if (-not $full.StartsWith($parent + $sep, [StringComparison]::OrdinalIgnoreCase)) {
        throw "拒绝操作：$full 不在 $parent 之下"
    }
    if ($ExpectedLeaf -and ([IO.Path]::GetFileName($full) -ne $ExpectedLeaf)) {
        throw "拒绝操作：$full 的文件名不是 '$ExpectedLeaf'"
    }
}

# ---- 0. 路径边界自检 ----
if ([IO.Path]::GetFileName($ReleaseDir) -ne $ReleaseLeafName) {
    throw "输出目录预期以 '$ReleaseLeafName' 结尾，实际：$ReleaseDir"
}
if (-not (Test-Path -LiteralPath $UnpackedDir)) {
    throw "未找到 $UnpackedDir，请先运行 npm run package"
}
if (-not (Test-Path -LiteralPath (Join-Path $UnpackedDir $AppExeName))) {
    throw "未找到 $UnpackedDir\$AppExeName，请先运行 npm run package"
}

# ---- 0.5 清除字节码（防止 .pyc 把构建机绝对路径带进发布包）----
$ResourcesDir = Join-Path $UnpackedDir "resources"
if (Test-Path -LiteralPath $ResourcesDir) {
    $cacheDirs = @(Get-ChildItem -LiteralPath $ResourcesDir -Recurse -Directory -Filter "__pycache__" -ErrorAction SilentlyContinue)
    foreach ($dir in $cacheDirs) { Remove-Item -LiteralPath $dir.FullName -Recurse -Force -ErrorAction SilentlyContinue }
    $pycFiles = @(Get-ChildItem -LiteralPath $ResourcesDir -Recurse -File -Filter "*.pyc" -ErrorAction SilentlyContinue)
    foreach ($file in $pycFiles) { Remove-Item -LiteralPath $file.FullName -Force -ErrorAction SilentlyContinue }
    Write-Host "==> 清理字节码：__pycache__ $($cacheDirs.Count) 个 / .pyc $($pycFiles.Count) 个"
}

# ---- 1. 压缩 ----
Write-Host "==> 压缩 $UnpackedDir → $ZipPath"
if (Test-Path -LiteralPath $ZipPath) {
    Assert-SafeDelete -Path $ZipPath -MustBeUnder $ReleaseDir -ExpectedLeaf $ZipFileName
    Remove-Item -LiteralPath $ZipPath -Force
}
Compress-Archive -Path (Join-Path $UnpackedDir "*") -DestinationPath $ZipPath -CompressionLevel Optimal

# ---- 2. portable 中文副本 ----
$Portable = Get-ChildItem -LiteralPath $ReleaseDir -Filter $PortablePattern |
    Sort-Object LastWriteTime -Descending |
    Select-Object -First 1
if ($Portable) {
    Assert-SafeDelete -Path $PortableCopy -MustBeUnder $ReleaseDir -ExpectedLeaf $PortableCopyName
    Copy-Item -LiteralPath $Portable.FullName -Destination $PortableCopy -Force
    Write-Host "==> portable 副本：$PortableCopy"
} else {
    Write-Warning "未找到 $PortablePattern（portable 目标可能未构建）"
}

foreach ($file in @($ZipPath, $PortableCopy)) {
    if (Test-Path -LiteralPath $file) {
        $mb = [math]::Round((Get-Item -LiteralPath $file).Length / 1MB, 1)
        Write-Host ("{0}  {1} MB" -f (Split-Path -Leaf $file), $mb) -ForegroundColor Green
    }
}
