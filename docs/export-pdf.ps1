# 一纸成课 · 技术文档 PDF 导出与页数实测
#
# 用法（在仓库任意位置执行均可）：
#   pwsh -File docs/export-pdf.ps1
#   pwsh -File docs/export-pdf.ps1 -Input docs/技术文档.md -Output docs/技术文档.pdf
#   pwsh -File docs/export-pdf.ps1 -RenderMermaid      # 定稿时把 mermaid 渲染成图（默认按代码块计）
#
# 版式：A4 / 10 pt / 1.35 倍行距 / 页边距 2.2 cm / 宋体正文 + 黑体标题。
# 依赖：pandoc、xelatex（TeX Live）、python + pypdf；-RenderMermaid 另需 mmdc 与本机 Chrome/Edge。
# 工具的查找顺序：-Pandoc/-XeLatex/-Python 参数 > PATH > 报错并给出安装提示（不写死盘符）。

param(
    [Alias("Input")]
    [string]$InputMd = (Join-Path $PSScriptRoot "技术文档.md"),
    [string]$Output  = (Join-Path $PSScriptRoot "技术文档.pdf"),
    [string]$Pandoc  = "",
    [string]$XeLatex = "",
    [string]$Python  = "",
    [switch]$RenderMermaid,
    [switch]$Toc,
    [double]$MaxPages = 30
)

$ErrorActionPreference = "Stop"

# ---- 路径（相对脚本自身推导）----
$DocsDir  = $PSScriptRoot
$StyleTex = Join-Path $DocsDir "pdf-style.tex"
$FigDir   = Join-Path $DocsDir "figs"
$TempDir  = Join-Path $env:TEMP "yizhi-doc-export"

# ---- 版式参数 ----
$FontSize     = "10pt"
$Margin       = "2.2cm"
$LineStretch  = "1.35"
$CJKMainFont  = "SimSun"      # 正文中文
$CJKSansFont  = "SimHei"      # 标题中文
$CJKMonoFont  = "SimSun"      # 代码块里的中文
$MonoFont     = "Consolas"    # 西文等宽
$MermaidWidth = "11.5cm"
$SequenceWidth = "13cm"

function Resolve-Tool {
    param([string]$Explicit, [string]$Name, [string]$Hint)
    if ($Explicit) {
        if (-not (Test-Path $Explicit)) { throw "指定的 $Name 不存在：$Explicit" }
        return (Resolve-Path -LiteralPath $Explicit).Path
    }
    $cmd = Get-Command $Name -ErrorAction SilentlyContinue | Select-Object -First 1
    if ($cmd) { return $cmd.Source }
    throw "找不到 $Name —— $Hint"
}

$PandocPath  = Resolve-Tool $Pandoc  "pandoc"  "安装 pandoc 并加入 PATH，或用 -Pandoc 指定可执行文件"
$XeLatexPath = Resolve-Tool $XeLatex "xelatex" "安装 TeX Live（含 xelatex）并加入 PATH，或用 -XeLatex 指定"
$PythonPath  = Resolve-Tool $Python  "python"  "安装 Python 并加入 PATH，或用 -Python 指定"

# ---- 输入检查 ----
if (-not (Test-Path $InputMd)) { throw "找不到源文件：$InputMd" }
$InputMd = (Resolve-Path -LiteralPath $InputMd).Path
$Output  = [IO.Path]::GetFullPath($Output)

& $PythonPath -c "import pypdf" 2>$null
if ($LASTEXITCODE -ne 0) { throw "python 缺少 pypdf：pip install pypdf" }

# ---- 可选：mermaid 代码块 → PNG（默认不渲染，按代码块计入页数）----
$sourceForPandoc = $InputMd
if ($RenderMermaid) {
    $Mmdc = (Get-Command mmdc -ErrorAction SilentlyContinue | Select-Object -First 1).Source
    if (-not $Mmdc) { throw "找不到 mmdc —— npm i -g @mermaid-js/mermaid-cli" }
    if (-not $env:PUPPETEER_EXECUTABLE_PATH) {
        $browsers = @(
            (Join-Path $env:ProgramFiles "Google\Chrome\Application\chrome.exe"),
            (Join-Path ${env:ProgramFiles(x86)} "Google\Chrome\Application\chrome.exe"),
            (Join-Path ${env:ProgramFiles(x86)} "Microsoft\Edge\Application\msedge.exe"),
            (Join-Path $env:ProgramFiles "Microsoft\Edge\Application\msedge.exe")
        )
        $found = $browsers | Where-Object { $_ -and (Test-Path $_) } | Select-Object -First 1
        if ($found) { $env:PUPPETEER_EXECUTABLE_PATH = $found }
    }
    New-Item -ItemType Directory -Force -Path $FigDir, $TempDir | Out-Null

    $text = Get-Content -LiteralPath $InputMd -Raw
    $pattern = [regex]'(?s)```mermaid\r?\n(.*?)```'
    $index = 0
    $text = $pattern.Replace($text, {
        param($match)
        $script:index++
        $code = $match.Groups[1].Value
        $isSequence = $code -match 'sequenceDiagram'
        $png = Join-Path $FigDir ("fig-{0}.png" -f $script:index)
        $mmd = Join-Path $TempDir ("fig-{0}.mmd" -f $script:index)
        Set-Content -LiteralPath $mmd -Value $code -Encoding utf8
        & $Mmdc -i $mmd -o $png -b white -s 2 | Out-Null
        if (-not (Test-Path $png)) { throw "mermaid 渲染失败：fig-$index" }
        $width = if ($isSequence) { $SequenceWidth } else { $MermaidWidth }
        $alt = if ($isSequence) { "命题—质检时序图" } else { "总架构图" }
        "![$alt](figs/fig-$($script:index).png){width=$width}"
    })
    $sourceForPandoc = Join-Path $TempDir "技术文档.rendered.md"
    Set-Content -LiteralPath $sourceForPandoc -Value $text -Encoding utf8
}

# ---- pandoc + xelatex ----
$env:PATH = (Split-Path $XeLatexPath) + ";" + $env:PATH
$pandocArgs = @(
    $sourceForPandoc
    "-o", $Output
    "--from", "markdown+pipe_tables+tex_math_dollars"
    "--pdf-engine", $XeLatexPath
    "--resource-path", $DocsDir
    "-V", "documentclass=article"
    "-V", "fontsize=$FontSize"
    "-V", "geometry:margin=$Margin"
    "-V", "linestretch=$LineStretch"
    "-V", "CJKmainfont=$CJKMainFont"
    "-V", "CJKsansfont=$CJKSansFont"
    "-V", "CJKmonofont=$CJKMonoFont"
    "-V", "monofont=$MonoFont"
    "-V", "colorlinks=true"
    "-V", "linkcolor=black"
    "-V", "urlcolor=black"
    "-V", "toccolor=black"
    "--syntax-highlighting=tango"
)
if (Test-Path $StyleTex) { $pandocArgs += @("--include-in-header", $StyleTex) }
if ($Toc) { $pandocArgs += @("--toc", "--toc-depth=2", "-V", "toc-title=目录") }

& $PandocPath @pandocArgs
if ($LASTEXITCODE -ne 0) { throw "pandoc 导出失败（退出码 $LASTEXITCODE）" }

# ---- 页数、体积、字数密度 ----
$pages  = & $PythonPath -c "from pypdf import PdfReader; import sys; print(len(PdfReader(sys.argv[1]).pages))" $Output
$sizeKb = [math]::Round((Get-Item -LiteralPath $Output).Length / 1KB, 1)
$chars  = (Get-Content -LiteralPath $InputMd -Raw).Length
$per1k  = if ($pages -gt 0) { [math]::Round([double]$pages / ($chars / 1000), 3) } else { 0 }

Write-Host ""
Write-Host "输入：$InputMd"
Write-Host "输出：$Output"
Write-Host "页数：$pages / 上限 $MaxPages    体积：$sizeKb KB    源字符数：$chars    每千字页数：$per1k"
if (-not $RenderMermaid) { Write-Host "注：mermaid 按代码块计入（定稿可加 -RenderMermaid 换成图片）" }
if ($pages -gt $MaxPages) {
    Write-Warning "超出页数上限"
    exit 1
}
