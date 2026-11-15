#requires -Version 5.1
<#
把 win-unpacked 压成交付用的 zip，并生成中文命名的 portable 副本。

用法（在 apps/desktop 下）：pwsh -File build/package_zip.ps1
前置：已执行 npm run package 或 npm run package:dir
#>
[CmdletBinding()]
param()

$ErrorActionPreference = "Stop"

$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$DesktopDir = Split-Path -Parent $ScriptDir
$ReleaseDir = Join-Path $DesktopDir "release"
$UnpackedDir = Join-Path $ReleaseDir "win-unpacked"
$ZipPath = Join-Path $ReleaseDir "一纸成课-win64.zip"

if (-not (Test-Path (Join-Path $UnpackedDir "一纸成课.exe"))) {
    throw "未找到 $UnpackedDir\一纸成课.exe，请先运行 npm run package"
}

Write-Host "==> 压缩 $UnpackedDir → $ZipPath"
if (Test-Path $ZipPath) { Remove-Item -Force $ZipPath }
Compress-Archive -Path (Join-Path $UnpackedDir "*") -DestinationPath $ZipPath -CompressionLevel Optimal

$Portable = Get-ChildItem -Path $ReleaseDir -Filter "YizhiChengke-*-portable.exe" |
    Sort-Object LastWriteTime -Descending |
    Select-Object -First 1
if ($Portable) {
    $Target = Join-Path $ReleaseDir "一纸成课-portable.exe"
    Copy-Item -Force $Portable.FullName $Target
    Write-Host "==> portable 副本：$Target"
} else {
    Write-Warning "未找到 YizhiChengke-*-portable.exe（portable 目标可能未构建）"
}

foreach ($file in @($ZipPath, (Join-Path $ReleaseDir "一纸成课-portable.exe"))) {
    if (Test-Path $file) {
        $mb = [math]::Round((Get-Item $file).Length / 1MB, 1)
        Write-Host ("{0}  {1} MB" -f (Split-Path -Leaf $file), $mb) -ForegroundColor Green
    }
}
