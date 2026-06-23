# Windows Feishu Daily Quiz

This directory contains the Windows MVP for the Feishu daily quiz automation.

## Requirements

- Windows 10 or Windows 11.
- Python 3.10+.
- Feishu Windows client installed and logged in.
- Chrome or Edge installed and logged in to `chintsso.feishu.cn`.
- The backup machine may briefly receive foreground window, keyboard, and mouse focus during execution.

## Install

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install uiautomation playwright
python -m playwright install chromium
Copy-Item config.example.json config.json
```

Edit `config.json` and confirm `feishu_group`, `browser_channel`, `log_dir`, and `state_file`.

## Manual Debug

```powershell
.\feishu_quiz_now_windows.ps1 --preflight
.\feishu_quiz_now_windows.ps1 --dump-uia
.\feishu_quiz_now_windows.ps1 --phase answer
.\feishu_quiz_now_windows.ps1 --phase open-form
.\feishu_quiz_now_windows.ps1 --phase submit --answer C
.\feishu_quiz_now_windows.ps1
```

The first live run should use `--preflight`, `--dump-uia`, and `--phase answer` before submitting.

## Scheduled Run

Create a Windows Task Scheduler task:

- Trigger: daily at 08:00.
- Action: `powershell.exe`.
- Arguments: `-ExecutionPolicy Bypass -File C:\path\to\feishu-daily-quiz\windows\feishu_quiz_windows.ps1`.
- Run only when the backup machine is logged in.

## Exit Codes

- `0`: success.
- `1`: fatal environment, login, permission, or configuration failure.
- `2`: answer not found yet.
- `3`: recoverable open-form or submission failure.

## Troubleshooting

- If UIA cannot read Feishu, keep Feishu logged in and visible once, then rerun `--dump-uia`.
- If the form opens to a login page, log in to `chintsso.feishu.cn` manually.
- If submission cannot find `.ud__tag`, Feishu form UI may have changed.
- If the scheduled task does nothing, run the manual wrapper in PowerShell first and inspect logs.
