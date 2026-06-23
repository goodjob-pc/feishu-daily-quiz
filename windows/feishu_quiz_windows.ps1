$ErrorActionPreference = "Stop"

$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$PythonScript = Join-Path $ScriptDir "feishu_daily_quiz_windows.py"
$ConfigPath = Join-Path $ScriptDir "config.json"
$DeadlineHour = 16
$MaxRetries = 14
$Delay = Get-Random -Minimum 0 -Maximum 61

Write-Host "Feishu quiz scheduled run starts after ${Delay}s"
Start-Sleep -Seconds $Delay

for ($i = 1; $i -le $MaxRetries; $i++) {
    if ((Get-Date).Hour -ge $DeadlineHour) {
        Write-Host "Deadline reached; stop retrying"
        exit 1
    }

    python $PythonScript --config $ConfigPath
    $Code = $LASTEXITCODE

    if ($Code -eq 0) {
        Write-Host "Feishu quiz succeeded"
        exit 0
    } elseif ($LASTEXITCODE -eq 1) {
        Write-Host "Fatal error; stop retrying"
        exit 1
    } elseif ($LASTEXITCODE -eq 2) {
        Write-Host "Answer not found; retry in 30 minutes"
        Start-Sleep -Seconds 1800
    } elseif ($LASTEXITCODE -eq 3) {
        Write-Host "Recoverable failure; retry in 60 seconds"
        Start-Sleep -Seconds 60
    } else {
        Write-Host "Unknown exit code $Code; retry in 30 minutes"
        Start-Sleep -Seconds 1800
    }
}

Write-Host "Max retries reached"
exit 1
