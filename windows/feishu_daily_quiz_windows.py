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
