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
    def replace_percent_var(match):
        return os.environ.get(match.group(1), match.group(0))

    expanded = re.sub(r"%([^%]+)%", replace_percent_var, value)
    return Path(os.path.expandvars(expanded)).expanduser()


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
        window = auto.GetForegroundControl()
        candidates = []

        def walk(control):
            try:
                name = control.Name or ""
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

    if args.phase == "open-form":
        feishu = WindowsFeishuClient(config)
        if not feishu.activate_group(config.feishu_group):
            return EXIT_FATAL
        if not feishu.click_go_answer_near_today(today):
            return EXIT_RETRYABLE
        return EXIT_SUCCESS if WindowsBrowserClient(config).wait_for_form() else EXIT_RETRYABLE

    if args.phase == "submit":
        if not args.answer:
            log(config, "--phase submit requires --answer", "FATAL")
            return EXIT_FATAL
        return EXIT_SUCCESS if WindowsBrowserClient(config).submit(args.answer) else EXIT_RETRYABLE

    return run_all(config, today=today)


if __name__ == "__main__":
    sys.exit(main())
