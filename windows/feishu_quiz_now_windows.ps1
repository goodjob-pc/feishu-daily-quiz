$ErrorActionPreference = "Stop"

$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$PythonScript = Join-Path $ScriptDir "feishu_daily_quiz_windows.py"
$ConfigPath = Join-Path $ScriptDir "config.json"

python $PythonScript --config $ConfigPath $args
exit $LASTEXITCODE
