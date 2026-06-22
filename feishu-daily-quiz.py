#!/usr/bin/env python3
"""
飞书每日答题自动化脚本
- 从飞书群读取答案 → 浏览器填表提交
- 每天 9:00 自动运行, 失败每 30 分钟重试到 16:00
- 日志: ~/.hermes/logs/feishu-quiz/YYYY-MM-DD.log
"""

import os, sys, json, time, re, subprocess, signal
from datetime import datetime

# ── Config ──────────────────────────────────────────────────
FEISHU_GROUP = "正泰安能户用光伏党支部"
CHROME_APP = "Google Chrome"
FORM_URL = "https://chintsso.feishu.cn/share/base/form/shrcnawUqcbQpUCI6mzv61wpatd"
DEADLINE_HOUR = 16
JS_TIMEOUT_SECONDS = 20
LOG_DIR = os.path.expanduser("~/.hermes/logs/feishu-quiz")
STATE_FILE = os.path.expanduser("~/.hermes/cache/feishu-quiz-state.json")

# Runtime state (discovered during execution)
feishu_pid = None
feishu_wid = None

# ── Setup ───────────────────────────────────────────────────
os.environ.setdefault("HERMES_CUA_DRIVER_CMD", "/Users/pc/.local/bin/cua-driver")
sys.path.insert(0, os.path.expanduser("~/.hermes/hermes-agent"))

def get_log_file():
    """Daily rotating log file."""
    today = datetime.now().strftime("%Y-%m-%d")
    os.makedirs(LOG_DIR, exist_ok=True)
    return os.path.join(LOG_DIR, f"{today}.log")

def log(msg, level="INFO"):
    ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    line = f"[{ts}] [{level}] {msg}"
    print(line, flush=True)
    with open(get_log_file(), "a") as f:
        f.write(line + "\n")

def log_section(title):
    log("=" * 60)
    log(f"  {title}")
    log("=" * 60)

def log_ax_snapshot(text, keyword, context_lines=3):
    """Log relevant AX tree lines around a keyword match."""
    lines = text.split("\n")
    for i, line in enumerate(lines):
        if keyword in line:
            start = max(0, i - context_lines)
            end = min(len(lines), i + context_lines + 1)
            log(f"  --- AX context for '{keyword}' (line {i}) ---")
            for j in range(start, end):
                marker = ">>>" if j == i else "   "
                log(f"  {marker} {lines[j][:150]}")
            return
    log(f"  (keyword '{keyword}' not found in AX tree)", "WARN")

# ── Checkpoint / Resume ──────────────────────────────────────

def load_state():
    """Load saved progress for today. Returns dict or None."""
    try:
        if not os.path.exists(STATE_FILE):
            return None
        with open(STATE_FILE) as f:
            state = json.load(f)
        today = datetime.now().strftime("%Y-%m-%d")
        if state.get("date") != today:
            os.remove(STATE_FILE)
            return None
        return state
    except Exception:
        return None

def save_state(**kwargs):
    """Save progress checkpoint."""
    kwargs["date"] = datetime.now().strftime("%Y-%m-%d")
    kwargs["saved_at"] = datetime.now().strftime("%H:%M:%S")
    os.makedirs(os.path.dirname(STATE_FILE), exist_ok=True)
    with open(STATE_FILE, "w") as f:
        json.dump(kwargs, f, ensure_ascii=False)

def clear_state():
    """Remove checkpoint file after success."""
    try:
        os.remove(STATE_FILE)
    except OSError:
        pass

# ── Core logic ───────────────────────────────────────────────

def get_backend():
    from tools.computer_use.cua_backend import CuaDriverBackend
    backend = CuaDriverBackend()
    backend.start()
    return backend

def activate_feishu():
    """Switch to Feishu Space and ensure it's frontmost.
    
    Primary: open -a (NSWorkspace, more reliable than AppleScript).
    Fallback: AppleScript tell process (requires Accessibility permissions)."""
    log("Activating Feishu...")
    # Primary: NSWorkspace activation (works even when AppleScript hangs)
    try:
        subprocess.run(["open", "-a", "Lark"], capture_output=True, timeout=10)
        time.sleep(2)
        log("  Activated via open -a Lark")
        return
    except Exception as e:
        log(f"  open -a failed: {e}, trying AppleScript...")
    
    # Fallback: AppleScript (requires Accessibility permissions intact)
    try:
        subprocess.run([
            "osascript", "-e",
            'tell application "System Events"\n'
            '  tell process "飞书"\n'
            '    set visible to true\n'
            '    set frontmost to true\n'
            '  end tell\n'
            'end tell'
        ], capture_output=True, timeout=10)
        time.sleep(2)
        log("  Activated via AppleScript")
    except Exception:
        log("  WARNING: Both activation methods failed; proceeding anyway", "WARN")
        time.sleep(1)

def find_answer_in_feishu(backend):
    """Navigate Feishu to the group and extract today's answer."""
    log_section("Phase 1: 飞书群获取答案")
    
    # Auto-discover Feishu PID/Window
    global feishu_pid, feishu_wid
    lw = backend._session.call_tool("list_windows", {"on_screen_only": False})
    sc = lw.get("structuredContent", {})
    for w in sc.get("windows", []):
        if "飞书" in w.get("app_name", "") and w.get("title") == "飞书":
            feishu_pid = w["pid"]
            feishu_wid = w["window_id"]
            log(f"Auto-discovered Feishu: pid={feishu_pid}, wid={feishu_wid}")
            break
    
    # Navigate to group — Primary: History menu (most reliable)
    # Fallback: Cmd+K search (works even if history is cleared)
    
    # Strategy 1: History menu
    log("Opening group via History menu...")
    r = backend._session.call_tool("get_window_state", {
        "pid": feishu_pid, "window_id": feishu_wid, "include_screenshot": False
    })
    text = r.get("data", "")
    
    history_idx = _find_element_by_label(text, "历史记录", ["AXMenuBarItem"])
    if history_idx:
        log(f"Clicking History menu [{history_idx}]...")
        backend._session.call_tool("click", {
            "pid": feishu_pid, "window_id": feishu_wid, "element_index": history_idx
        })
        time.sleep(0.6)
        
        r = backend._session.call_tool("get_window_state", {
            "pid": feishu_pid, "window_id": feishu_wid, "include_screenshot": False
        })
        text = r.get("data", "")
        group_idx = _find_element_by_label(text, FEISHU_GROUP, ["AXMenuItem"])
        if group_idx:
            log(f"Clicking group '{FEISHU_GROUP}' [{group_idx}]...")
            backend._session.call_tool("click", {
                "pid": feishu_pid, "window_id": feishu_wid, "element_index": group_idx
            })
            time.sleep(2)
    
    if not history_idx or not group_idx:
        # Strategy 2: Cmd+K search (fallback)
        log("History menu failed. Trying Cmd+K search...")
        backend._session.call_tool("hotkey", {
            "pid": feishu_pid, "keys": ["cmd", "k"]
        })
        time.sleep(0.8)
        
        backend._session.call_tool("type_text", {
            "pid": feishu_pid, "text": FEISHU_GROUP
        })
        time.sleep(1.0)
        
        # Press ArrowDown then Enter to select first result
        backend._session.call_tool("press_key", {"pid": feishu_pid, "key": "down"})
        time.sleep(0.3)
        backend._session.call_tool("press_key", {"pid": feishu_pid, "key": "return"})
        time.sleep(2)
    
    # Step 2: Get AX tree and search for answer
    log("Reading chat AX tree...")
    r = backend._session.call_tool("get_window_state", {
        "pid": feishu_pid, "window_id": feishu_wid, "include_screenshot": False
    })
    text = r.get("data", "")
    log(f"  AX tree: {len(text.split(chr(10)))} lines, {len(text)} chars")
    
    # Log answer-related lines
    for kw in ["每日一题", "前去答题", "今日答案"]:
        log_ax_snapshot(text, kw)
    
    today = datetime.now().strftime("%Y/%m/%d")
    log(f"Searching for answer dated {today}...")
    
    # Strategy: find all today's questions, search near each for answer
    for q_match in re.finditer(rf"{today}\s+每日一题来啦", text):
        pos = q_match.start()
        # Search 2000 chars BEFORE and AFTER the question
        start = max(0, pos - 2000)
        end = min(len(text), pos + 2000)
        search_region = text[start:end]
        answer_match = re.search(r"每日一题\s+今日答案[：:]\s*([A-D])(?!\d)", search_region)
        if answer_match:
            answer = answer_match.group(1)
            log(f"✅ Found answer: {answer}", "SUCCESS")
            log(f"  Question at pos {pos}, answer at offset {answer_match.start()}")
            return answer
    
    # Fallback: find the chronologically last answer for today
    # In chat list preview, today's answer typically appears near "08:" or "07:" timestamps
    # with the format: "每日一题 今日答案：X"
    answers = list(re.finditer(r"每日一题\s+今日答案[：:]\s*([A-D])(?!\d)", text))
    if answers:
        # Take the last one (most recent in the tree = closest to top of chat)
        answer = answers[-1].group(1)
        log(f"✅ Found answer (last in tree): {answer}", "SUCCESS")
        return answer
    
    for m in re.finditer(r"每日一题[^\n]*", text):
        log(f"  DEBUG match: {m.group()[:120]}")
    
    log("❌ Answer not found in AX tree", "ERROR")
    return None

def _find_element_by_label(text, label, element_types=None):
    """Find element index by its label text. Returns index or None.
    Searches across multiple element types (AXStaticText, AXMenuItem, AXCell, etc.)"""
    if element_types is None:
        element_types = ["AXStaticText", "AXMenuItem", "AXCell", "AXButton", "AXMenuBarItem"]
    
    escaped = re.escape(label)
    for et in element_types:
        # Match patterns like: [N] AXType "label" or [N] AXType = "label"
        pattern = rf"\[\s*(\d+)\s*\].*{et}.*[\"=]([^\"]*{escaped}[^\"]*)[\"]"
        for m in re.finditer(pattern, text):
            idx = int(m.group(1))
            return idx
    
    # Last resort: just search the label in any element
    pattern = rf"\[\s*(\d+)\s*\][^\[]*{escaped}"
    m = re.search(pattern, text)
    if m:
        return int(m.group(1))
    return None

def click_go_answer(backend):
    """Click '前去答题' button to open form in Chrome."""
    log_section("Phase 2: 点击前去答题")
    
    r = backend._session.call_tool("get_window_state", {
        "pid": feishu_pid, "window_id": feishu_wid, "include_screenshot": False
    })
    text = r.get("data", "")
    
    today_short = datetime.now().strftime("%Y/%m/%d")
    log(f"Looking for 前去答题 near {today_short}...")
    
    button_indices = []
    lines = text.split("\n")
    for i, line in enumerate(lines):
        if "AXButton" in line and "前去答题" in line:
            m = re.search(r"\[(\d+)\]\s+AXButton", line)
            if m:
                idx = int(m.group(1))
                context = "\n".join(lines[max(0,i-10):i+5])
                if today_short in context:
                    button_indices.append(idx)
                    log(f"  Found 前去答题 [{idx}] near today's date")
                    break
    
    if not button_indices:
        log("  No 前去答题 near today's date, using fallback...")
        for i, line in enumerate(lines):
            if "AXButton" in line and "前去答题" in line:
                m = re.search(r"\[(\d+)\]\s+AXButton", line)
                if m:
                    button_indices.append(int(m.group(1)))
                    log(f"  Fallback: 前去答题 [{button_indices[-1]}]")
                    break
    
    if not button_indices:
        log("❌ Could not find '前去答题' button", "ERROR")
        log_ax_snapshot(text, "前去答题")
        return False
    
    idx = button_indices[0]
    log(f"Clicking element [{idx}] 前去答题...")
    r = backend._session.call_tool("click", {
        "pid": feishu_pid, "window_id": feishu_wid, "element_index": idx
    })
    log(f"  Result: {r.get('data', '')[:200]}")
    
    # Wait for Chrome to open form — retry up to 10 seconds
    log("  Waiting for Chrome form...")
    for attempt in range(10):
        time.sleep(1)
        pid, wid = find_chrome_form_window(backend)
        if pid:
            log(f"  Chrome form appeared after {attempt+1}s")
            return True
    
    log("  Chrome form did not appear within 10s", "WARN")
    return False

def find_chrome_form_window(backend):
    """Find the Chrome window showing the quiz form.
    Searches by window title AND address bar URL."""
    lw = backend._session.call_tool("list_windows", {"on_screen_only": True})
    sc = lw.get("structuredContent", {})
    
    candidates = []
    for w in sc.get("windows", []):
        if w["app_name"] != CHROME_APP:
            continue
        title = w.get("title", "")
        pid = w["pid"]
        wid = w["window_id"]
        
        if "答题" in title or "答题卡" in title:
            log(f"  Found by title: '{title[:60]}' pid={pid} wid={wid}")
            return pid, wid
        
        try:
            r = backend._session.call_tool("get_window_state", {
                "pid": pid, "window_id": wid, "include_screenshot": False
            })
            text = r.get("data", "")
            if "feishu.cn/share/base/form" in text:
                log(f"  Found by URL: pid={pid} wid={wid}")
                return pid, wid
        except Exception as e:
            log(f"  (skipping pid={pid}: {e})")
        
        candidates.append((pid, wid, title[:60]))
    
    log("  Chrome windows checked:")
    for pid, wid, title in candidates:
        log(f"    pid={pid} wid={wid} title='{title}'")
    
    return None, None

def submit_answer_in_chrome(backend, answer):
    """Fill and submit the quiz form using JavaScript injection.
    
    The form uses custom Feishu UI components (ud__tag divs for answer
    selection, not standard <select> or <input type=radio>).  We drive it
    entirely via page.execute_javascript — the Chrome AX tree does not
    expose web content for this form type.
    """
    log_section(f"Phase 3: Chrome 表单提交 (答案: {answer})")
    
    pid, wid = find_chrome_form_window(backend)
    if not pid:
        log("❌ Chrome form window not found", "ERROR")
        lw = backend._session.call_tool("list_windows", {"on_screen_only": True})
        sc = lw.get("structuredContent", {})
        for w in sc.get("windows", []):
            if w["app_name"] == CHROME_APP:
                log(f"  Chrome: title='{w.get('title','')[:60]}' pid={w['pid']} wid={w['window_id']}")
        return False
    
    log(f"Chrome form: pid={pid}, wid={wid}")
    
    def js(code):
        """Run JavaScript in the form page, return parsed result.
        Returns (parsed_value, raw_string, is_error)."""
        def _on_timeout(signum, frame):
            raise TimeoutError("Chrome JavaScript execution timed out")

        old_handler = signal.signal(signal.SIGALRM, _on_timeout)
        signal.alarm(JS_TIMEOUT_SECONDS)
        try:
            r = backend._session.call_tool("page", {
                "action": "execute_javascript",
                "pid": pid,
                "window_id": wid,
                "javascript": code
            })
        except TimeoutError as e:
            log(f"❌ {e} after {JS_TIMEOUT_SECONDS}s", "ERROR")
            return None, str(e), True
        finally:
            signal.alarm(0)
            signal.signal(signal.SIGALRM, old_handler)

        raw = r.get("data", "")
        # Detect Chrome JS permission errors
        if isinstance(raw, str):
            if "JavaScript 的功能已关闭" in raw:
                log("❌ Chrome JS from Apple Events is DISABLED", "FATAL")
                log("   Fix: Chrome → 查看 → 开发者 → 允许 Apple 事件中的 JavaScript")
                return None, raw, True
            if "不能获得" in raw and "window" in raw:
                log("❌ Chrome window not ready for JS", "ERROR")
                return None, raw, True
            if raw.strip().startswith("{"):
                try:
                    return json.loads(raw), raw, False
                except json.JSONDecodeError:
                    pass
        return raw, raw, False
    
    # ── Step 1: Read form state ──────────────────────────────
    state, raw, is_err = js(
        "JSON.stringify({"
        "isDone:!!(document.body?.innerText||'').match(/提交成功|已达提交次数上限/),"
        "hasSelect:!!document.querySelector('select'),"
        "hasTags:!!document.querySelector('.ud__tag'),"
        "submitBtn:!!([...document.querySelectorAll('button')]"
        "  .find(b=>b.textContent.trim()==='提交'&&!b.disabled)),"
        "url:location.href"
        "})"
    )
    log(f"  Form state: {json.dumps(state, ensure_ascii=False)[:400]}")
    
    if is_err:
        return False
    if not isinstance(state, dict):
        log(f"❌ Could not read form state via JS: {str(raw)[:200]}", "ERROR")
        return False
    
    if state.get("isDone"):
        log("✅ Already submitted. Skipping.", "SUCCESS")
        return True
    
    # ── Step 2: Select the answer ────────────────────────────
    if state.get("hasSelect"):
        # Standard <select> dropdown
        opt_index = {"A": 0, "B": 1, "C": 2, "D": 3}.get(answer, 0)
        result, _, is_err = js(
            f"var s=document.querySelector('select');"
            f"s.selectedIndex={opt_index};"
            f"s.dispatchEvent(new Event('change',{{bubbles:true}}));"
            f"'selected option '+s.options[{opt_index}].text"
        )
    elif state.get("hasTags"):
        # Feishu custom tag (ud__tag div)
        result, _, is_err = js(
            f"var tags=[...document.querySelectorAll('.ud__tag')]"
            f".filter(d=>d.textContent.trim()==='{answer}');"
            f"if(tags.length){{tags[0].click();'clicked '+tags.length+' tag(s)'}}"
            f"else{{'no tag for {answer}'}}"
        )
    else:
        log("❌ No answer input found (no select, no tags)", "ERROR")
        return False
    
    if is_err:
        return False
    log(f"  Select answer: {result}")
    if isinstance(result, str) and "no tag" in result:
        log(f"❌ Could not find answer tag '{answer}'", "ERROR")
        return False
    time.sleep(0.5)
    
    # ── Step 3: Click submit ─────────────────────────────────
    if not state.get("submitBtn"):
        log("❌ Submit button not found or disabled", "ERROR")
        return False
    
    result, _, is_err = js(
        "var b=[...document.querySelectorAll('button')]"
        ".find(x=>x.textContent.trim()==='提交'&&!x.disabled);"
        "if(b){b.click();'clicked: '+b.textContent.trim()}"
        "else{'submit button not clickable'}"
    )
    if is_err:
        return False
    log(f"  Submit: {result}")
    time.sleep(2)
    
    # ── Step 4: Verify ───────────────────────────────────────
    verify, _, is_err = js(
        "var t=document.body?.innerText||'';"
        "JSON.stringify({"
        "success:t.includes('提交成功'),"
        "limit:t.includes('已达提交次数上限'),"
        "text:t.substring(0,150)"
        "})"
    )
    log(f"  Verify: {json.dumps(verify, ensure_ascii=False)[:300]}")
    
    if isinstance(verify, dict) and (verify.get("success") or verify.get("limit")):
        log(f"✅ Quiz submitted! Answer: {answer}", "SUCCESS")
        
        # Close the form tab after success (cleanup)
        try:
            js("setTimeout(function(){window.close();},500);'closing'")
            log("  Tab close requested")
        except Exception:
            pass
        
        return True
    
    log("⚠️ Submission not confirmed", "WARN")
    return False

def main():
    log_section(f"飞书每日答题 - {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    log(f"PID: {os.getpid()}, Python: {sys.executable}")
    log(f"Log: {get_log_file()}")
    
    # ── Pre-flight: permission check ────────────────────────────
    log("Pre-flight: checking cua-driver AX permissions...")
    try:
        backend = get_backend()
        # Auto-discover Feishu window for probe
        lw = backend._session.call_tool("list_windows", {"on_screen_only": False})
        sc = lw.get("structuredContent", {})
        probe_pid = probe_wid = None
        for w in sc.get("windows", []):
            if "飞书" in w.get("app_name", "") and w.get("title") == "飞书":
                probe_pid = w["pid"]
                probe_wid = w["window_id"]
                break
        if not probe_pid:
            log("❌ Feishu window not found in window list", "FATAL")
            backend.stop()
            sys.exit(1)
        
        r = backend._session.call_tool("get_window_state", {
            "pid": probe_pid, "window_id": probe_wid, "include_screenshot": False
        })
        text = r.get("data", "")
        has_content = any(
            kw in text for kw in ["AXStaticText", "AXButton", "AXTextField"]
        )
        if not has_content:
            log("❌ AX tree has no content elements — Accessibility broken", "FATAL")
            backend.stop()
            sys.exit(1)
        log(f"  ✅ AX tree OK ({len(text.split(chr(10)))} lines)")
        backend.stop()
    except Exception as e:
        log(f"❌ cua-driver AX probe failed: {e}", "FATAL")
        sys.exit(1)
    
    now = datetime.now()
    if now.hour >= DEADLINE_HOUR:
        log(f"⏰ After deadline ({DEADLINE_HOUR}:00). Skipping.", "WARN")
        clear_state()
        return
    
    # ── Checkpoint restore ────────────────────────────────────
    saved = load_state()
    answer = saved.get("answer") if saved else None
    chrome_pid = saved.get("chrome_pid") if saved else None
    chrome_wid = saved.get("chrome_wid") if saved else None
    
    if answer:
        log(f"📌 Resuming from checkpoint: answer={answer}, phase={saved.get('phase')}")
    
    phase_times = {}
    try:
        t0 = time.time()
        activate_feishu()
        phase_times["activate"] = time.time() - t0
        
        backend = get_backend()
        
        # ── Phase 1: Get answer (skip if restored) ────────────
        if not answer:
            t0 = time.time()
            answer = find_answer_in_feishu(backend)
            phase_times["find_answer"] = time.time() - t0
            if not answer:
                log("No answer found yet. Will retry later.")
                backend.stop()
                sys.exit(2)
            save_state(answer=answer, phase="answer_found")
        else:
            log(f"⏭️  Phase 1 skipped (restored answer: {answer})")
        
        # ── Phase 2: Click 前去答题 in Feishu ──────────────────
        form_already_open = False
        if chrome_pid and chrome_wid:
            existing_pid, existing_wid = find_chrome_form_window(backend)
            if existing_pid == chrome_pid and existing_wid == chrome_wid:
                log(f"⏭️  Phase 2 skipped (form already open: pid={chrome_pid})")
                form_already_open = True
        
        if not form_already_open:
            t0 = time.time()
            if not click_go_answer(backend):
                log("Failed to click 前去答题", "ERROR")
                backend.stop()
                sys.exit(3)
            time.sleep(1)
            new_pid, new_wid = find_chrome_form_window(backend)
            phase_times["click_go"] = time.time() - t0
            
            if new_pid:
                save_state(answer=answer, phase="form_opened",
                          chrome_pid=new_pid, chrome_wid=new_wid)
                chrome_pid, chrome_wid = new_pid, new_wid
            else:
                log("❌ Form window not found after click", "ERROR")
                backend.stop()
                sys.exit(3)
        
        # ── Phase 3: Submit ────────────────────────────────────
        t0 = time.time()
        success = submit_answer_in_chrome(backend, answer)
        phase_times["submit"] = time.time() - t0
        
        backend.stop()
        
        log_section("Timing Summary")
        for phase, elapsed in phase_times.items():
            log(f"  {phase}: {elapsed:.1f}s")
        log(f"  TOTAL: {sum(phase_times.values()):.1f}s")
        
        if success:
            clear_state()
            total_s = sum(phase_times.values())
            log(f"🎉 DONE! Answer: {answer}", "SUCCESS")
            # Daily summary (machine-readable for external tools)
            summary = {
                "status": "ok",
                "answer": answer,
                "date": datetime.now().strftime("%Y-%m-%d"),
                "time": datetime.now().strftime("%H:%M:%S"),
                "duration_s": round(total_s, 1),
                "phases": {k: round(v, 1) for k, v in phase_times.items()}
            }
            log(f"📊 SUMMARY: {json.dumps(summary, ensure_ascii=False)}")
            sys.exit(0)
        else:
            log("❌ Submission failed", "ERROR")
            sys.exit(3)  # exit 3 = keep checkpoint, quick retry
            
    except Exception as e:
        log(f"❌ Fatal error: {e}", "ERROR")
        import traceback
        log(traceback.format_exc(), "ERROR")
        sys.exit(1)

if __name__ == "__main__":
    main()
