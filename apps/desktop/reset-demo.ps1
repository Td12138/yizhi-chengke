# 一键还原演示初始状态（桌面版）
#
# 作用：杀掉正在运行的程序，删除 Electron 的用户数据目录
#       （%APPDATA%\yizhi-chengke-desktop：数据库、上传的原图、日志、
#        新手引导完成标记、窗口尺寸等），下次启动就是全新状态：
#        - 没有卡片、没有题、没有历史
#        - 第一屏自动弹出新手引导（?tour=1）
#
# 用法：双击同目录的「一键还原演示状态.cmd」，或
#       pwsh -File reset-demo.ps1
#
# 注意：这是**不可撤销**的删除；如果只想清数据但保留引导完成状态，请改用
#       源码模式的 python apps/server/scripts/reset_demo.py。

param([switch]$Force)

$ErrorActionPreference = 'Stop'
$USER_DATA = Join-Path $env:APPDATA 'yizhi-chengke-desktop'
$APP_NAMES = @('一纸成课', 'YizhiChengke-0.1.5-portable', 'YizhiChengke-0.1.6-portable', 'YizhiChengke-0.1.7-portable')

Write-Host '== 一纸成课 · 还原演示初始状态 ==' -ForegroundColor Cyan

# 1. 停掉正在运行的桌面版（只杀本应用的进程，不碰系统里其他 python）
$stopped = 0
foreach ($name in $APP_NAMES) {
    Get-Process -Name $name -ErrorAction SilentlyContinue | ForEach-Object {
        Stop-Process -Id $_.Id -Force -ErrorAction SilentlyContinue
        $stopped++
    }
}
Get-Process -Name python, pythonw -ErrorAction SilentlyContinue | Where-Object {
    $_.Path -and ($_.Path -like '*yizhi-chengke-desktop*' -or $_.Path -like '*win-unpacked*')
} | ForEach-Object {
    Stop-Process -Id $_.Id -Force -ErrorAction SilentlyContinue
    $stopped++
}
if ($stopped -gt 0) { Write-Host "已停止 $stopped 个进程" -ForegroundColor Yellow }
Start-Sleep -Seconds 1

# 2. 删用户数据目录
if (Test-Path $USER_DATA) {
    if (-not $Force) {
        Write-Host "将删除：$USER_DATA" -ForegroundColor Yellow
        Write-Host '（数据库 / 原图 / 日志 / 新手引导标记，全部清空）' -ForegroundColor DarkGray
        $answer = Read-Host '确认删除？输入 y 继续'
        if ($answer -ne 'y' -and $answer -ne 'Y') {
            Write-Host '已取消。' -ForegroundColor Red
            exit 1
        }
    }
    Remove-Item -LiteralPath $USER_DATA -Recurse -Force
    Write-Host '已删除用户数据目录。' -ForegroundColor Green
} else {
    Write-Host '用户数据目录本来就不存在，已是初始状态。' -ForegroundColor Green
}

# 3. 说明
Write-Host ''
Write-Host '完成。下次双击「一纸成课.exe」时：' -ForegroundColor Cyan
Write-Host '  1) 数据为空（0 卡片 / 0 题）'
Write-Host '  2) 第一屏自动播放新手引导（相当于 ?tour=1）'
Write-Host '  3) 录完引导后，点首页「示例板书」胶囊即可开始演示'
Write-Host ''
Write-Host '源码模式请改用：python apps/server/scripts/reset_demo.py（只清库，不删代码）' -ForegroundColor DarkGray
