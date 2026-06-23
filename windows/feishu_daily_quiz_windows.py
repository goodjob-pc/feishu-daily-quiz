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
