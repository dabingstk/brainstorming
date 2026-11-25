# 每小时由任务计划程序调用：随机抖动 -> ego 截图 -> 本地 OCR -> 上报
$ErrorActionPreference = 'Stop'
$root = 'C:\shop-ocr'
$logDir = Join-Path $root 'logs'
New-Item -ItemType Directory -Force -Path $logDir | Out-Null
$log = Join-Path $logDir ("run-{0:yyyyMMdd-HHmm}.log" -f (Get-Date))

# 随机抖动 0-25 分钟，避免固定整点节奏
Start-Sleep -Seconds (Get-Random -Minimum 0 -Maximum 1500)

$js = Get-Content -Raw (Join-Path $root 'collect.mjs')
$js | & ego-browser nodejs *>&1 | Tee-Object -FilePath $log
if ($LASTEXITCODE -ne 0) {
    Add-Content -Path $log -Value "[ERROR] ego-browser 执行失败，退出码 $LASTEXITCODE"
    exit $LASTEXITCODE
}

$meta = Get-ChildItem (Join-Path $root 'shots') -Filter *.json |
    Where-Object { $_.Name -notlike '*.payload.json' } |
    Sort-Object LastWriteTime -Descending |
    Select-Object -First 1
if (-not $meta) {
    Add-Content -Path $log -Value '[ERROR] 未找到截图元数据'
    exit 1
}

python (Join-Path $root 'ocr_parse.py') --meta $meta.FullName --config (Join-Path $root 'config.json') *>&1 |
    Tee-Object -FilePath $log -Append
exit $LASTEXITCODE
