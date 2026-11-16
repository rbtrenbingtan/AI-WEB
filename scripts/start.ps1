param([switch]$Install, [ValidateRange(1,65535)][int]$Port = 8000)
$ErrorActionPreference = 'Stop'
Set-Location (Split-Path $PSScriptRoot -Parent)
if (-not (Test-Path '.venv/Scripts/python.exe')) {
    $pythonCandidates = @()
    if (Get-Command py -ErrorAction SilentlyContinue) {
        foreach ($version in @('-3.11','-3.12','-3.13','-3.14','-3')) {
            $pythonCandidates += ,@('py', $version)
        }
    }
    if (Get-Command python -ErrorAction SilentlyContinue) {
        $pythonCandidates += ,@('python')
    }
    $pythonRunner = $null
    foreach ($candidate in $pythonCandidates) {
        $executable = $candidate[0]
        $prefixArguments = @($candidate | Select-Object -Skip 1)
        & $executable @prefixArguments -c 'import sys; sys.exit(0 if sys.version_info >= (3,11) else 1)' 2>$null
        if ($LASTEXITCODE -eq 0) { $pythonRunner = $candidate; break }
    }
    if ($null -eq $pythonRunner) { throw '需要 Python 3.11+。请安装并启用 Python Launcher 或加入 PATH。' }
    $executable = $pythonRunner[0]
    $prefixArguments = @($pythonRunner | Select-Object -Skip 1)
    & $executable @prefixArguments -m venv .venv
    if ($LASTEXITCODE -ne 0) { throw '需要 Python 3.11+。请安装后重新运行。' }
    $Install = $true
}
& ./.venv/Scripts/python.exe -c 'import sys; sys.exit(0 if sys.version_info >= (3,11) else 1)'
if ($LASTEXITCODE -ne 0) { throw '当前 .venv 的 Python 版本过旧，需要 Python 3.11+ 环境。' }
if ($Install) {
    & ./.venv/Scripts/python.exe -m pip install -r requirements-lock.txt
    if ($LASTEXITCODE -ne 0) { throw 'Python 依赖安装失败' }
}
if ($Install -or -not (Test-Path 'frontend/dist/index.html')) {
    Push-Location frontend
    try {
        npm.cmd ci
        if ($LASTEXITCODE -ne 0) { throw '前端依赖安装失败' }
        npm.cmd run build
        if ($LASTEXITCODE -ne 0) { throw '前端构建失败' }
    } finally { Pop-Location }
}
Write-Host "知证学伴 http://127.0.0.1:$Port （按 Ctrl+C 停止）"
& ./.venv/Scripts/python.exe -m uvicorn backend.main:app --host 127.0.0.1 --port $Port
