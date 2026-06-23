# Windows Daily Quiz Automation Design

## Context

The existing macOS implementation automates the daily Feishu quiz by reading the answer from a Feishu group, clicking the in-message "前去答题" button, and submitting the opened Feishu form in Chrome. It depends on macOS-only pieces such as `cua-driver`, macOS Accessibility, and Chrome Apple Events.

The Windows version must run independently on a backup Windows machine. It may briefly take over the foreground window, keyboard, and mouse during execution. It must not depend on macOS, Hermes, `cua-driver`, or a fixed form URL.

## Goals

- Run independently on Windows through Task Scheduler and manual triggers.
- Read today's answer from the Feishu Windows client.
- Open the form only by clicking the Feishu message button, not by fixed URL.
- Submit the form through Chrome or Edge browser automation.
- Keep the same safety rules as the macOS version:
  - Do not use undated historical answers.
  - Do not submit unless the browser page is confirmed to be a Feishu form.
  - Treat environment, login, and permission failures as fatal.
- Provide fast manual debug entry points for the backup machine.

## Non-Goals

- Fully background operation without foreground takeover.
- Supporting non-Windows platforms in the Windows script.
- Using a fixed Feishu form URL as fallback.
- Building a GUI.
- Supporting multiple groups in the first version.

## Recommended Approach

Use a hybrid Windows automation stack:

- Windows UI Automation for the Feishu desktop client.
- Playwright or Chrome DevTools for browser page control.
- PowerShell wrappers for scheduled and manual execution.
- Python for the main orchestration script.

This avoids the brittleness of coordinate-first automation while still accepting limited foreground takeover when Feishu or the browser needs focus.

## File Layout

```text
windows/
  feishu_daily_quiz_windows.py
  feishu_quiz_windows.ps1
  feishu_quiz_now_windows.ps1
  config.example.json
  README_WINDOWS.md
  test_feishu_quiz_windows.py
```

The first version can keep the Python implementation in one file while keeping clear internal function boundaries.

## Configuration

`config.example.json`:

```json
{
  "feishu_group": "正泰安能户用光伏党支部",
  "deadline_hour": 16,
  "browser": "chrome",
  "browser_channel": "chrome",
  "log_dir": "%LOCALAPPDATA%\\FeishuDailyQuiz\\logs",
  "state_file": "%LOCALAPPDATA%\\FeishuDailyQuiz\\state.json",
  "random_delay_seconds": 60,
  "answer_search_window_chars": 2000,
  "js_timeout_seconds": 20,
  "form_url_marker": "feishu.cn/share/base/form"
}
```

The deployed machine copies this to `windows/config.json`.

## Main Components

### `config`

Loads and validates `config.json`. Expands Windows environment variables such as `%LOCALAPPDATA%`.

Required fields:

- `feishu_group`
- `deadline_hour`
- `browser`
- `log_dir`
- `state_file`
- `form_url_marker`

Invalid or missing configuration exits with code `1`.

### `state`

Persists progress for the current date:

```json
{
  "date": "2026-06-23",
  "phase": "form_opened",
  "answer": "C",
  "saved_at": "08:48:21"
}
```

State is ignored and removed when the date is not today. Successful completion clears state.

### `logging`

Writes daily logs under `log_dir` using the same style as the macOS version:

```text
[2026-06-23 08:48:34] [SUCCESS] DONE! Answer: C
```

### `answer_extractor`

Extracts answers from a plain text UI tree.

Rules:

- Find today's `YYYY/MM/DD 每日一题来啦`.
- Search within `answer_search_window_chars` before and after the question.
- Match `每日一题\s+今日答案[：:]\s*([A-D])`.
- Return `None` if only undated answers are present.
- Return `None` if today's question is not visible.

### `feishu_uia`

Controls Feishu on Windows through UI Automation.

Responsibilities:

- Find or start the Feishu client.
- Bring Feishu to the foreground.
- Open the target group:
  - Prefer recent/history navigation if available.
  - Fall back to `Ctrl+K`, type group name, press `Enter`.
- Read the current UIA text tree.
- Find the "前去答题" button near today's question.
- Invoke/click that button.

Keyboard fallback is allowed for group search, but coordinate clicking should only be a last resort.

### `browser_controller`

Controls Chrome or Edge.

Responsibilities:

- Detect the browser page opened by Feishu.
- Confirm the page URL or accessible page data contains `feishu.cn/share/base/form`.
- Detect SSO/login pages and treat them as fatal.
- Execute JS to:
  - Check whether the form is already submitted.
  - Find `select` or `.ud__tag` answer controls.
  - Click the answer.
  - Click a button whose trimmed text is exactly `提交`.
  - Verify `提交成功` or `已达提交次数上限`.
- Close the form page after success.

The browser layer must not submit on a page that is only title-matched as "答题"; the form URL marker must be confirmed.

### `runner`

Coordinates the phases and maps results to exit codes.

Exit codes:

- `0`: success.
- `1`: fatal environment, login, permission, or configuration problem.
- `2`: answer not found yet.
- `3`: form opening or submission failed after a recoverable action.

## Runtime Flow

### Phase 0: Pre-flight

Check:

- Running on Windows.
- Python version is supported.
- `config.json` is valid.
- Log and state directories are writable.
- Feishu is installed or startable.
- Chrome or Edge is installed or startable.
- Playwright or DevTools control is available.

Fatal failures exit `1`.

### Phase 1: Read Answer

1. Activate Feishu.
2. Open the target group.
3. Read the UIA text tree.
4. Extract today's answer using the safe answer extraction rules.
5. Save `answer_found` state when an answer is found.
6. Exit `2` when no safe answer is found.

### Phase 2: Open Form

1. Locate the "前去答题" button near today's question.
2. Invoke/click the button.
3. Wait for Chrome or Edge to open the form.
4. Confirm `form_url_marker`.
5. Save `form_opened` state.

Recoverable failures exit `3`. Login/SSO failures exit `1`.

### Phase 3: Submit

1. Connect to the confirmed form page.
2. Read form state.
3. If already submitted, return success.
4. Select the answer.
5. Click the exact `提交` button.
6. Verify success or submission limit text.
7. Close the page and clear state.

Unconfirmed submission exits `3`.

## PowerShell Wrappers

### Scheduled Entry

`feishu_quiz_windows.ps1`:

- Adds a random delay from `0` to `random_delay_seconds`.
- Runs the Python script.
- Handles retry behavior:
  - `0`: success, stop.
  - `1`: fatal, stop.
  - `2`: sleep 30 minutes, retry.
  - `3`: sleep 60 seconds, retry.
- Stops after `deadline_hour`.

### Manual Entry

`feishu_quiz_now_windows.ps1`:

- No random delay.
- Passes all arguments through to Python.
- Intended for fast validation on the backup Windows machine.

Supported debug commands:

```powershell
.\feishu_quiz_now_windows.ps1
.\feishu_quiz_now_windows.ps1 --preflight
.\feishu_quiz_now_windows.ps1 --phase answer
.\feishu_quiz_now_windows.ps1 --phase open-form
.\feishu_quiz_now_windows.ps1 --phase submit --answer C
.\feishu_quiz_now_windows.ps1 --dump-uia
```

## Testing Strategy

### Logic Tests Runnable on macOS

`test_feishu_quiz_windows.py` should cover:

- Config validation.
- State load/save/clear.
- Date-scoped answer extraction.
- Rejection of undated historical answers.
- JS generation or JS behavior checks for exact `提交` matching.
- Browser page matching requiring `form_url_marker`.
- PowerShell retry contract where possible through text/static checks.

### Windows Acceptance Tests

On the backup Windows machine:

1. `--preflight` validates dependencies and writable paths.
2. `--dump-uia` confirms Feishu UIA text is readable.
3. `--phase answer` confirms today's answer can be extracted.
4. `--phase open-form` confirms the in-message button opens the form.
5. `--phase submit --answer X` confirms browser submission flow.
6. Task Scheduler triggers `feishu_quiz_windows.ps1` once and writes expected logs.

## Deployment Notes

`README_WINDOWS.md` must document:

- Python installation.
- Dependency installation.
- Browser choice and setup.
- Feishu login requirement.
- `config.json` setup.
- Manual trigger commands.
- Task Scheduler setup.
- Log and state file locations.
- Common failure modes.

## First-Version Completion Criteria

- Windows script runs without macOS or Hermes dependencies.
- Manual trigger exists and supports phase-level debugging.
- Scheduled PowerShell wrapper implements retry semantics.
- Fixed form URL is not used.
- Historical undated answers are rejected.
- Browser page is confirmed as a Feishu form before submission.
- Logic tests pass on macOS.
- Windows manual acceptance checklist is documented.
