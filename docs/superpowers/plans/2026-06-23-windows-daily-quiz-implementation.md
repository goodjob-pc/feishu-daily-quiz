# Windows Daily Quiz Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a first Windows MVP that can run the Feishu daily quiz automation independently on a backup Windows machine, with scheduled and manual triggers.

**Architecture:** Add a new `windows/` implementation beside the existing macOS scripts. The Python script owns config, state, answer extraction, Feishu UI Automation, browser submission, and CLI phases; PowerShell wrappers provide scheduled retry and manual debug entry points.

**Tech Stack:** Python 3.10+, Windows UI Automation (`uiautomation`), Playwright, PowerShell, Windows Task Scheduler, `unittest`.

---

## File Structure

- Create `windows/feishu_daily_quiz_windows.py`: main Windows implementation, written as one focused script with clear function boundaries.
- Create `windows/feishu_quiz_windows.ps1`: scheduled wrapper with random delay, retry semantics, and deadline cutoff.
- Create `windows/feishu_quiz_now_windows.ps1`: manual immediate wrapper that passes debug args through to Python.
- Create `windows/config.example.json`: deployable config template.
- Create `windows/README_WINDOWS.md`: setup, manual debug, Task Scheduler, and troubleshooting docs.
- Create `windows/test_feishu_quiz_windows.py`: logic tests runnable on macOS and Windows.

---

### Task 1: Config, State, Logging, And Answer Extraction

**Files:**
- Create: `windows/feishu_daily_quiz_windows.py`
- Create: `windows/config.example.json`
- Create: `windows/test_feishu_quiz_windows.py`

- [ ] **Step 1: Write failing tests for config, state, and safe answer extraction**

Add this initial test file:

```python
import json
import os
import tempfile
import unittest
from datetime import date
from pathlib import Path

import feishu_daily_quiz_windows as quiz


class TestConfig(unittest.TestCase):
    def test_load_config_expands_windows_env_paths(self):
        with tempfile.TemporaryDirectory() as tmp:
            os.environ["LOCALAPPDATA"] = tmp
            path = Path(tmp) / "config.json"
            path.write_text(json.dumps({
                "feishu_group": "正泰安能户用光伏党支部",
                "deadline_hour": 16,
                "browser": "chrome",
                "browser_channel": "chrome",
                "log_dir": "%LOCALAPPDATA%\\\\FeishuDailyQuiz\\\\logs",
                "state_file": "%LOCALAPPDATA%\\\\FeishuDailyQuiz\\\\state.json",
                "random_delay_seconds": 60,
                "answer_search_window_chars": 2000,
                "js_timeout_seconds": 20,
                "form_url_marker": "feishu.cn/share/base/form"
            }), encoding="utf-8")

            config = quiz.load_config(path)

            self.assertEqual(config.feishu_group, "正泰安能户用光伏党支部")
            self.assertIn(str(Path(tmp)), str(config.log_dir))
            self.assertIn(str(Path(tmp)), str(config.state_file))

    def test_load_config_requires_group(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "config.json"
            path.write_text(json.dumps({
                "deadline_hour": 16,
                "browser": "chrome",
                "log_dir": str(Path(tmp) / "logs"),
                "state_file": str(Path(tmp) / "state.json"),
                "form_url_marker": "feishu.cn/share/base/form"
            }), encoding="utf-8")

            with self.assertRaises(ValueError):
                quiz.load_config(path)


class TestState(unittest.TestCase):
    def test_state_round_trip_and_clear(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "state.json"
            today = date(2026, 6, 23)

            quiz.save_state(path, today, answer="C", phase="answer_found")
            state = quiz.load_state(path, today)

            self.assertEqual(state["answer"], "C")
            self.assertEqual(state["phase"], "answer_found")

            quiz.clear_state(path)
            self.assertIsNone(quiz.load_state(path, today))

    def test_old_state_is_removed(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "state.json"
            path.write_text(json.dumps({"date": "2026-06-22", "answer": "B"}), encoding="utf-8")

            self.assertIsNone(quiz.load_state(path, date(2026, 6, 23)))
            self.assertFalse(path.exists())


class TestAnswerExtraction(unittest.TestCase):
    def test_extracts_answer_near_todays_question(self):
        text = (
            "每日一题 今日答案：C\\n"
            "x" * 500
            + "2026/06/23 每日一题来啦！问题内容\\n"
            + "A.选项\\nB.选项\\nC.选项\\n"
        )

        self.assertEqual(
            quiz.extract_answer_from_text(text, date(2026, 6, 23), window_chars=2000),
            "C",
        )

    def test_rejects_undated_history_answer(self):
        text = "08:10\\n每日一题 今日答案：B\\n"

        self.assertIsNone(
            quiz.extract_answer_from_text(text, date(2026, 6, 23), window_chars=2000)
        )

    def test_rejects_answer_outside_search_window(self):
        text = "2026/06/23 每日一题来啦！" + ("x" * 2100) + "每日一题 今日答案：A"

        self.assertIsNone(
            quiz.extract_answer_from_text(text, date(2026, 6, 23), window_chars=2000)
        )


if __name__ == "__main__":
    unittest.main(verbosity=2)
```

- [ ] **Step 2: Run tests and verify they fail because the module does not exist**

Run:

```bash
cd /Users/pc/Documents/feishu-daily-quiz/windows
python3 -m unittest test_feishu_quiz_windows.py
```

Expected: FAIL or ERROR with `ModuleNotFoundError: No module named 'feishu_daily_quiz_windows'`.

- [ ] **Step 3: Add config template**

Create `windows/config.example.json`:

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

- [ ] **Step 4: Implement minimal config, state, logging, and extraction logic**

Create `windows/feishu_daily_quiz_windows.py` with:

```python
#!/usr/bin/env python3
import argparse
import json
import os
import platform
import re
import sys
import time
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path


EXIT_SUCCESS = 0
EXIT_FATAL = 1
EXIT_ANSWER_NOT_FOUND = 2
EXIT_RETRYABLE = 3


@dataclass
class Config:
    feishu_group: str
    deadline_hour: int
    browser: str
    browser_channel: str
    log_dir: Path
    state_file: Path
    random_delay_seconds: int
    answer_search_window_chars: int
    js_timeout_seconds: int
    form_url_marker: str


def expand_path(value: str) -> Path:
    return Path(os.path.expandvars(value)).expanduser()


def load_config(path: Path) -> Config:
    raw = json.loads(Path(path).read_text(encoding="utf-8"))
    required = ["feishu_group", "deadline_hour", "browser", "log_dir", "state_file", "form_url_marker"]
    missing = [key for key in required if not raw.get(key)]
    if missing:
        raise ValueError(f"Missing required config field(s): {', '.join(missing)}")
    return Config(
        feishu_group=raw["feishu_group"],
        deadline_hour=int(raw["deadline_hour"]),
        browser=raw["browser"],
        browser_channel=raw.get("browser_channel", raw["browser"]),
        log_dir=expand_path(raw["log_dir"]),
        state_file=expand_path(raw["state_file"]),
        random_delay_seconds=int(raw.get("random_delay_seconds", 60)),
        answer_search_window_chars=int(raw.get("answer_search_window_chars", 2000)),
        js_timeout_seconds=int(raw.get("js_timeout_seconds", 20)),
        form_url_marker=raw["form_url_marker"],
    )


def log(config: Config, message: str, level: str = "INFO") -> None:
    config.log_dir.mkdir(parents=True, exist_ok=True)
    line = f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] [{level}] {message}"
    print(line, flush=True)
    log_file = config.log_dir / f"{date.today().isoformat()}.log"
    with log_file.open("a", encoding="utf-8") as f:
        f.write(line + "\n")


def load_state(path: Path, today: date):
    if not path.exists():
        return None
    try:
        state = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        path.unlink(missing_ok=True)
        return None
    if state.get("date") != today.isoformat():
        path.unlink(missing_ok=True)
        return None
    return state


def save_state(path: Path, today: date, **kwargs) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = dict(kwargs)
    payload["date"] = today.isoformat()
    payload["saved_at"] = datetime.now().strftime("%H:%M:%S")
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def clear_state(path: Path) -> None:
    path.unlink(missing_ok=True)


def extract_answer_from_text(text: str, today: date, window_chars: int = 2000):
    today_text = today.strftime("%Y/%m/%d")
    answer_re = re.compile(r"每日一题\s+今日答案[：:]\s*([A-D])(?!\d)")
    for question in re.finditer(rf"{re.escape(today_text)}\s+每日一题来啦", text):
        start = max(0, question.start() - window_chars)
        end = min(len(text), question.start() + window_chars)
        match = answer_re.search(text[start:end])
        if match:
            return match.group(1)
    return None
```

- [ ] **Step 5: Run tests and verify they pass**

Run:

```bash
cd /Users/pc/Documents/feishu-daily-quiz/windows
python3 -m unittest test_feishu_quiz_windows.py
```

Expected: all tests in Task 1 pass.

- [ ] **Step 6: Commit Task 1**

```bash
git add windows/feishu_daily_quiz_windows.py windows/config.example.json windows/test_feishu_quiz_windows.py
git commit -m "feat: add Windows quiz core utilities"
```

---

### Task 2: CLI, Preflight, And Phase Dispatch Skeleton

**Files:**
- Modify: `windows/feishu_daily_quiz_windows.py`
- Modify: `windows/test_feishu_quiz_windows.py`

- [ ] **Step 1: Add failing tests for CLI parsing and preflight**

Append to `windows/test_feishu_quiz_windows.py`:

```python
class TestCliAndPreflight(unittest.TestCase):
    def test_parse_args_accepts_phase_submit_answer(self):
        args = quiz.parse_args(["--phase", "submit", "--answer", "C"])
        self.assertEqual(args.phase, "submit")
        self.assertEqual(args.answer, "C")

    def test_preflight_rejects_non_windows(self):
        with tempfile.TemporaryDirectory() as tmp:
            config = quiz.Config(
                feishu_group="正泰安能户用光伏党支部",
                deadline_hour=16,
                browser="chrome",
                browser_channel="chrome",
                log_dir=Path(tmp) / "logs",
                state_file=Path(tmp) / "state.json",
                random_delay_seconds=60,
                answer_search_window_chars=2000,
                js_timeout_seconds=20,
                form_url_marker="feishu.cn/share/base/form",
            )
            ok, reason = quiz.preflight(config, system_name="Darwin", check_imports=False)
            self.assertFalse(ok)
            self.assertIn("Windows", reason)
```

- [ ] **Step 2: Run the new tests and verify they fail**

Run:

```bash
cd /Users/pc/Documents/feishu-daily-quiz/windows
python3 -m unittest test_feishu_quiz_windows.TestCliAndPreflight -v
```

Expected: FAIL or ERROR because `parse_args` and `preflight` are not implemented.

- [ ] **Step 3: Implement CLI and preflight skeleton**

Append these functions to `windows/feishu_daily_quiz_windows.py`:

```python
def parse_args(argv=None):
    parser = argparse.ArgumentParser(description="Windows Feishu daily quiz automation")
    parser.add_argument("--config", default="config.json")
    parser.add_argument("--preflight", action="store_true")
    parser.add_argument("--phase", choices=["all", "answer", "open-form", "submit"], default="all")
    parser.add_argument("--answer", choices=["A", "B", "C", "D"])
    parser.add_argument("--dump-uia", action="store_true")
    return parser.parse_args(argv)


def preflight(config: Config, system_name=None, check_imports=True):
    system_name = system_name or platform.system()
    if system_name != "Windows":
        return False, f"Windows is required, got {system_name}"
    try:
        config.log_dir.mkdir(parents=True, exist_ok=True)
        config.state_file.parent.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        return False, f"Cannot create log/state directory: {exc}"
    if check_imports:
        try:
            import uiautomation  # noqa: F401
        except Exception as exc:
            return False, f"Missing uiautomation dependency: {exc}"
        try:
            import playwright.sync_api  # noqa: F401
        except Exception as exc:
            return False, f"Missing playwright dependency: {exc}"
    return True, "ok"
```

- [ ] **Step 4: Add main skeleton**

Append:

```python
def main(argv=None):
    args = parse_args(argv)
    config_path = Path(args.config)
    try:
        config = load_config(config_path)
    except Exception as exc:
        print(f"FATAL: {exc}", file=sys.stderr)
        return EXIT_FATAL

    ok, reason = preflight(config)
    if args.preflight:
        log(config, f"Preflight: {reason}", "SUCCESS" if ok else "FATAL")
        return EXIT_SUCCESS if ok else EXIT_FATAL
    if not ok:
        log(config, f"Preflight failed: {reason}", "FATAL")
        return EXIT_FATAL

    log(config, "Windows implementation skeleton is ready; UI automation not executed yet")
    return EXIT_RETRYABLE


if __name__ == "__main__":
    sys.exit(main())
```

- [ ] **Step 5: Run tests and syntax check**

Run:

```bash
cd /Users/pc/Documents/feishu-daily-quiz/windows
python3 -m unittest test_feishu_quiz_windows.py
python3 -m py_compile feishu_daily_quiz_windows.py
```

Expected: tests pass; compile exits 0.

- [ ] **Step 6: Commit Task 2**

```bash
git add windows/feishu_daily_quiz_windows.py windows/test_feishu_quiz_windows.py
git commit -m "feat: add Windows quiz CLI skeleton"
```

---

### Task 3: Browser Controller JS And Page Matching

**Files:**
- Modify: `windows/feishu_daily_quiz_windows.py`
- Modify: `windows/test_feishu_quiz_windows.py`

- [ ] **Step 1: Write failing tests for browser helpers**

Append:

```python
class TestBrowserHelpers(unittest.TestCase):
    def test_is_form_url_requires_marker(self):
        config = quiz.Config(
            feishu_group="正泰安能户用光伏党支部",
            deadline_hour=16,
            browser="chrome",
            browser_channel="chrome",
            log_dir=Path("logs"),
            state_file=Path("state.json"),
            random_delay_seconds=60,
            answer_search_window_chars=2000,
            js_timeout_seconds=20,
            form_url_marker="feishu.cn/share/base/form",
        )
        self.assertTrue(quiz.is_form_url("https://chintsso.feishu.cn/share/base/form/abc", config))
        self.assertFalse(quiz.is_form_url("https://example.com/答题", config))

    def test_submit_js_uses_exact_submit_text(self):
        js = quiz.build_submit_js("C")
        self.assertIn("textContent.trim()==='提交'", js)
        self.assertNotIn("includes('提交')", js)
        self.assertIn(".ud__tag", js)
```

- [ ] **Step 2: Run tests and verify failure**

Run:

```bash
cd /Users/pc/Documents/feishu-daily-quiz/windows
python3 -m unittest test_feishu_quiz_windows.TestBrowserHelpers -v
```

Expected: FAIL or ERROR because helper functions do not exist.

- [ ] **Step 3: Implement browser helper functions**

Append:

```python
def is_form_url(url: str, config: Config) -> bool:
    return bool(url and config.form_url_marker in url)


def build_form_state_js() -> str:
    return (
        "JSON.stringify({"
        "isDone:!!(document.body?.innerText||'').match(/提交成功|已达提交次数上限/),"
        "hasSelect:!!document.querySelector('select'),"
        "hasTags:!!document.querySelector('.ud__tag'),"
        "submitBtn:!!([...document.querySelectorAll('button')]"
        ".find(b=>b.textContent.trim()==='提交'&&!b.disabled)),"
        "url:location.href,"
        "text:(document.body?.innerText||'').substring(0,200)"
        "})"
    )


def build_submit_js(answer: str) -> str:
    option_index = {"A": 0, "B": 1, "C": 2, "D": 3}[answer]
    return (
        "var state={};"
        "var s=document.querySelector('select');"
        f"if(s){{s.selectedIndex={option_index};s.dispatchEvent(new Event('change',{{bubbles:true}}));state.selected='select';}}"
        "else{"
        f"var tags=[...document.querySelectorAll('.ud__tag')].filter(d=>d.textContent.trim()==='{answer}');"
        "if(tags.length){tags[0].click();state.selected='tag';}"
        "else{state.error='answer option not found';}"
        "}"
        "if(!state.error){"
        "var b=[...document.querySelectorAll('button')].find(x=>x.textContent.trim()==='提交'&&!x.disabled);"
        "if(b){b.click();state.submitted=true;}else{state.error='submit button not found';}"
        "}"
        "JSON.stringify(state)"
    )
```

- [ ] **Step 4: Add a stub browser controller interface**

Append:

```python
def submit_answer_in_browser(config: Config, answer: str):
    try:
        from playwright.sync_api import sync_playwright
    except Exception as exc:
        raise RuntimeError(f"Playwright is not available: {exc}") from exc

    with sync_playwright() as p:
        browser = p.chromium.launch(channel=config.browser_channel, headless=False)
        try:
            pages = []
            for context in browser.contexts:
                pages.extend(context.pages)
            for page in pages:
                if is_form_url(page.url, config):
                    state = json.loads(page.evaluate(build_form_state_js()))
                    if state.get("isDone"):
                        return True
                    result = json.loads(page.evaluate(build_submit_js(answer)))
                    if result.get("error"):
                        return False
                    time.sleep(2)
                    verify_text = page.evaluate("document.body?.innerText||''")
                    return "提交成功" in verify_text or "已达提交次数上限" in verify_text
            return False
        finally:
            browser.close()
```

This first implementation is intentionally minimal; Task 6 documentation will instruct Windows validation and may refine connection strategy if `launch` does not see the Feishu-opened tab.

- [ ] **Step 5: Run tests**

Run:

```bash
cd /Users/pc/Documents/feishu-daily-quiz/windows
python3 -m unittest test_feishu_quiz_windows.py
```

Expected: all tests pass.

- [ ] **Step 6: Commit Task 3**

```bash
git add windows/feishu_daily_quiz_windows.py windows/test_feishu_quiz_windows.py
git commit -m "feat: add Windows browser submit helpers"
```

---

### Task 4: Feishu UIA Skeleton And Phase Runner

**Files:**
- Modify: `windows/feishu_daily_quiz_windows.py`
- Modify: `windows/test_feishu_quiz_windows.py`

- [ ] **Step 1: Write failing tests for phase behavior using fakes**

Append:

```python
class FakeFeishuClient:
    def __init__(self, text, clicked=True):
        self.text = text
        self.clicked = clicked

    def activate_group(self, group):
        return True

    def read_text_tree(self):
        return self.text

    def click_go_answer_near_today(self, today):
        return self.clicked


class FakeBrowserClient:
    def __init__(self, submitted=True):
        self.submitted = submitted

    def wait_for_form(self):
        return True

    def submit(self, answer):
        return self.submitted


class TestPhaseRunner(unittest.TestCase):
    def _config(self, tmp):
        return quiz.Config(
            feishu_group="正泰安能户用光伏党支部",
            deadline_hour=16,
            browser="chrome",
            browser_channel="chrome",
            log_dir=Path(tmp) / "logs",
            state_file=Path(tmp) / "state.json",
            random_delay_seconds=60,
            answer_search_window_chars=2000,
            js_timeout_seconds=20,
            form_url_marker="feishu.cn/share/base/form",
        )

    def test_run_all_success(self):
        text = "每日一题 今日答案：C\\n2026/06/23 每日一题来啦！"
        with tempfile.TemporaryDirectory() as tmp:
            code = quiz.run_all(
                self._config(tmp),
                today=date(2026, 6, 23),
                feishu=FakeFeishuClient(text),
                browser=FakeBrowserClient(True),
            )
            self.assertEqual(code, quiz.EXIT_SUCCESS)

    def test_run_all_answer_not_found(self):
        with tempfile.TemporaryDirectory() as tmp:
            code = quiz.run_all(
                self._config(tmp),
                today=date(2026, 6, 23),
                feishu=FakeFeishuClient("每日一题 今日答案：B"),
                browser=FakeBrowserClient(True),
            )
            self.assertEqual(code, quiz.EXIT_ANSWER_NOT_FOUND)
```

- [ ] **Step 2: Run tests and verify failure**

Run:

```bash
cd /Users/pc/Documents/feishu-daily-quiz/windows
python3 -m unittest test_feishu_quiz_windows.TestPhaseRunner -v
```

Expected: FAIL or ERROR because `run_all` is not implemented.

- [ ] **Step 3: Implement phase runner and client interfaces**

Append:

```python
class WindowsFeishuClient:
    def __init__(self, config: Config):
        self.config = config

    def activate_group(self, group: str) -> bool:
        try:
            import uiautomation as auto
        except Exception as exc:
            raise RuntimeError(f"uiautomation is not available: {exc}") from exc
        window = auto.WindowControl(searchDepth=1, Name="飞书")
        if not window.Exists(3):
            window = auto.WindowControl(searchDepth=1, Name="Lark")
        if not window.Exists(3):
            return False
        window.SetActive()
        time.sleep(1)
        auto.SendKeys("{Ctrl}k")
        time.sleep(0.5)
        auto.SendKeys(group)
        time.sleep(0.5)
        auto.SendKeys("{Enter}")
        time.sleep(2)
        return True

    def read_text_tree(self) -> str:
        import uiautomation as auto
        window = auto.GetForegroundControl()
        lines = []

        def walk(control, depth=0):
            try:
                name = control.Name
                control_type = control.ControlTypeName
            except Exception:
                return
            if name:
                lines.append(f"{'  ' * depth}{control_type}: {name}")
            for child in control.GetChildren():
                walk(child, depth + 1)

        walk(window)
        return "\n".join(lines)

    def click_go_answer_near_today(self, today: date) -> bool:
        import uiautomation as auto
        today_text = today.strftime("%Y/%m/%d")
        window = auto.GetForegroundControl()
        candidates = []

        def walk(control):
            try:
                name = control.Name or ""
                control_type = control.ControlTypeName
            except Exception:
                return
            if "前去答题" in name:
                candidates.append(control)
            for child in control.GetChildren():
                walk(child)

        walk(window)
        if not candidates:
            return False
        candidates[0].Click()
        time.sleep(3)
        return True


class WindowsBrowserClient:
    def __init__(self, config: Config):
        self.config = config

    def wait_for_form(self) -> bool:
        time.sleep(3)
        return True

    def submit(self, answer: str) -> bool:
        return submit_answer_in_browser(self.config, answer)


def run_all(config: Config, today=None, feishu=None, browser=None):
    today = today or date.today()
    feishu = feishu or WindowsFeishuClient(config)
    browser = browser or WindowsBrowserClient(config)

    if not feishu.activate_group(config.feishu_group):
        log(config, "Could not activate Feishu group", "FATAL")
        return EXIT_FATAL

    text = feishu.read_text_tree()
    answer = extract_answer_from_text(text, today, config.answer_search_window_chars)
    if not answer:
        log(config, "Answer not found yet")
        return EXIT_ANSWER_NOT_FOUND
    save_state(config.state_file, today, answer=answer, phase="answer_found")

    if not feishu.click_go_answer_near_today(today):
        log(config, "Could not click 前去答题", "ERROR")
        return EXIT_RETRYABLE

    if not browser.wait_for_form():
        log(config, "Form did not open", "ERROR")
        return EXIT_RETRYABLE
    save_state(config.state_file, today, answer=answer, phase="form_opened")

    if browser.submit(answer):
        clear_state(config.state_file)
        log(config, f"DONE! Answer: {answer}", "SUCCESS")
        return EXIT_SUCCESS
    log(config, "Submission failed", "ERROR")
    return EXIT_RETRYABLE
```

- [ ] **Step 4: Wire main to phases**

Replace the placeholder body after preflight with:

```python
    today = date.today()
    if args.dump_uia:
        feishu = WindowsFeishuClient(config)
        if not feishu.activate_group(config.feishu_group):
            log(config, "Could not activate Feishu group", "FATAL")
            return EXIT_FATAL
        print(feishu.read_text_tree())
        return EXIT_SUCCESS

    if args.phase == "answer":
        feishu = WindowsFeishuClient(config)
        if not feishu.activate_group(config.feishu_group):
            return EXIT_FATAL
        answer = extract_answer_from_text(
            feishu.read_text_tree(), today, config.answer_search_window_chars
        )
        if answer:
            log(config, f"Found answer: {answer}", "SUCCESS")
            return EXIT_SUCCESS
        return EXIT_ANSWER_NOT_FOUND

    if args.phase == "submit":
        if not args.answer:
            log(config, "--phase submit requires --answer", "FATAL")
            return EXIT_FATAL
        return EXIT_SUCCESS if WindowsBrowserClient(config).submit(args.answer) else EXIT_RETRYABLE

    return run_all(config, today=today)
```

- [ ] **Step 5: Run tests**

Run:

```bash
cd /Users/pc/Documents/feishu-daily-quiz/windows
python3 -m unittest test_feishu_quiz_windows.py
python3 -m py_compile feishu_daily_quiz_windows.py
```

Expected: tests pass and compile exits 0.

- [ ] **Step 6: Commit Task 4**

```bash
git add windows/feishu_daily_quiz_windows.py windows/test_feishu_quiz_windows.py
git commit -m "feat: add Windows quiz phase runner"
```

---

### Task 5: PowerShell Scheduled And Manual Wrappers

**Files:**
- Create: `windows/feishu_quiz_windows.ps1`
- Create: `windows/feishu_quiz_now_windows.ps1`
- Modify: `windows/test_feishu_quiz_windows.py`

- [ ] **Step 1: Add static tests for wrapper contracts**

Append:

```python
class TestPowerShellWrappers(unittest.TestCase):
    def test_scheduled_wrapper_handles_exit_codes(self):
        script = Path(__file__).with_name("feishu_quiz_windows.ps1").read_text(encoding="utf-8")
        self.assertIn("$LASTEXITCODE -eq 1", script)
        self.assertIn("Start-Sleep -Seconds 1800", script)
        self.assertIn("Start-Sleep -Seconds 60", script)

    def test_manual_wrapper_has_no_random_delay(self):
        script = Path(__file__).with_name("feishu_quiz_now_windows.ps1").read_text(encoding="utf-8")
        self.assertNotIn("Get-Random", script)
        self.assertIn("$args", script)
```

- [ ] **Step 2: Run tests and verify failure**

Run:

```bash
cd /Users/pc/Documents/feishu-daily-quiz/windows
python3 -m unittest test_feishu_quiz_windows.TestPowerShellWrappers -v
```

Expected: ERROR because wrappers do not exist.

- [ ] **Step 3: Create scheduled wrapper**

Create `windows/feishu_quiz_windows.ps1`:

```powershell
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
```

- [ ] **Step 4: Create manual wrapper**

Create `windows/feishu_quiz_now_windows.ps1`:

```powershell
$ErrorActionPreference = "Stop"

$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$PythonScript = Join-Path $ScriptDir "feishu_daily_quiz_windows.py"
$ConfigPath = Join-Path $ScriptDir "config.json"

python $PythonScript --config $ConfigPath $args
exit $LASTEXITCODE
```

- [ ] **Step 5: Run wrapper tests**

Run:

```bash
cd /Users/pc/Documents/feishu-daily-quiz/windows
python3 -m unittest test_feishu_quiz_windows.TestPowerShellWrappers -v
```

Expected: tests pass.

- [ ] **Step 6: Commit Task 5**

```bash
git add windows/feishu_quiz_windows.ps1 windows/feishu_quiz_now_windows.ps1 windows/test_feishu_quiz_windows.py
git commit -m "feat: add Windows quiz PowerShell wrappers"
```

---

### Task 6: Windows README And Final Verification

**Files:**
- Create: `windows/README_WINDOWS.md`
- Modify if needed: `README.md`

- [ ] **Step 1: Create Windows README**

Create `windows/README_WINDOWS.md`:

```markdown
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
.\\.venv\\Scripts\\Activate.ps1
pip install uiautomation playwright
python -m playwright install chromium
Copy-Item config.example.json config.json
```

Edit `config.json` and confirm `feishu_group`, `browser_channel`, `log_dir`, and `state_file`.

## Manual Debug

```powershell
.\\feishu_quiz_now_windows.ps1 --preflight
.\\feishu_quiz_now_windows.ps1 --dump-uia
.\\feishu_quiz_now_windows.ps1 --phase answer
.\\feishu_quiz_now_windows.ps1 --phase open-form
.\\feishu_quiz_now_windows.ps1 --phase submit --answer C
.\\feishu_quiz_now_windows.ps1
```

The first live run should use `--preflight`, `--dump-uia`, and `--phase answer` before submitting.

## Scheduled Run

Create a Windows Task Scheduler task:

- Trigger: daily at 08:00.
- Action: `powershell.exe`.
- Arguments: `-ExecutionPolicy Bypass -File C:\\path\\to\\feishu-daily-quiz\\windows\\feishu_quiz_windows.ps1`.
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
```

- [ ] **Step 2: Optionally link Windows README from root README**

Add one line near the root README deployment section:

```markdown
Windows MVP documentation lives in [`windows/README_WINDOWS.md`](windows/README_WINDOWS.md).
```

- [ ] **Step 3: Run full local verification**

Run:

```bash
cd /Users/pc/Documents/feishu-daily-quiz/windows
python3 -m unittest test_feishu_quiz_windows.py
python3 -m py_compile feishu_daily_quiz_windows.py
cd /Users/pc/Documents/feishu-daily-quiz
git status --short
```

Expected:

- All logic tests pass.
- Python compile exits 0.
- `git status --short` shows only intended Windows MVP files and docs before commit.

- [ ] **Step 4: Commit Task 6**

```bash
git add README.md windows/README_WINDOWS.md
git commit -m "docs: add Windows quiz setup guide"
```

- [ ] **Step 5: Final review**

Run:

```bash
git log --oneline --decorate --max-count=8
git status --short
```

Expected:

- Recent commits include all Windows MVP tasks.
- Working tree is clean.

