param([switch]$Configure, [switch]$Check)
$ErrorActionPreference = 'Stop'
try {
    $studioRoot = $PSScriptRoot
    $settingsFile = Join-Path $studioRoot 'settings.json'
    $settings = [pscustomobject]@{}
    $engineRoot = ''
    if (Test-Path -LiteralPath $settingsFile) {
        $settings = Get-Content -LiteralPath $settingsFile -Raw -Encoding UTF8 | ConvertFrom-Json
        if ($null -eq $settings) { $settings = [pscustomobject]@{} }
        $engineRoot = $settings.engine_root
        if ($engineRoot -and -not [IO.Path]::IsPathRooted($engineRoot)) { $engineRoot = Join-Path $studioRoot $engineRoot }
    }
    if (-not $engineRoot) {
        $candidates = @(Get-ChildItem -LiteralPath (Split-Path $studioRoot) -Directory | Where-Object {
            (Test-Path -LiteralPath (Join-Path $_.FullName 'api_v2.py')) -and
            (Test-Path -LiteralPath (Join-Path $_.FullName 'runtime\python.exe'))
        })
        if ($candidates.Count -eq 1) { $engineRoot = $candidates[0].FullName }
    }
    function Test-Engine([string]$folder) {
        return $folder -and (Test-Path -LiteralPath (Join-Path $folder 'api_v2.py')) -and (Test-Path -LiteralPath (Join-Path $folder 'runtime\python.exe'))
    }
    if ($Check) {
        if (-not (Test-Engine $engineRoot)) { throw '未找到引擎，请双击“配置引擎.bat”选择 GPT-SoVITS 整合包目录。' }
        Write-Output "引擎目录有效：$engineRoot"
        exit 0
    }
    if ($Configure -or -not (Test-Engine $engineRoot)) {
        Add-Type -AssemblyName System.Windows.Forms
        $picker = New-Object System.Windows.Forms.FolderBrowserDialog
        $picker.Description = '选择 GPT-SoVITS 整合包根目录（含 api_v2.py 和 runtime 文件夹）'
        $picker.ShowNewFolderButton = $false
        if ($engineRoot -and (Test-Path -LiteralPath $engineRoot)) { $picker.SelectedPath = $engineRoot }
        try {
            if ($picker.ShowDialog() -ne [Windows.Forms.DialogResult]::OK) { exit 0 }
            $engineRoot = $picker.SelectedPath
        } finally { $picker.Dispose() }
    }
    if (-not (Test-Engine $engineRoot)) { throw '目录不正确：需要 api_v2.py 和 runtime\python.exe。请选择 Windows GPT-SoVITS 整合包根目录。' }
    $settings | Add-Member -NotePropertyName engine_root -NotePropertyValue $engineRoot -Force
    $settings | ConvertTo-Json -Depth 16 | Set-Content -LiteralPath $settingsFile -Encoding UTF8
    if ($Configure) { Write-Output '配置已保存。请先退出已运行的工坊，再双击启动。'; exit 0 }
    Set-Location -LiteralPath $studioRoot
    & (Join-Path $engineRoot 'runtime\python.exe') -I (Join-Path $studioRoot 'app.py')
    exit $LASTEXITCODE
} catch {
    Write-Host ('启动失败：' + $_.Exception.Message) -ForegroundColor Red
    exit 1
}
