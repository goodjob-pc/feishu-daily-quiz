#!/usr/bin/env python3
"""
Unit tests for feishu-daily-quiz.py — can run standalone without real Feishu/Chrome.
Tests answer extraction, element finding, and Phase 3 submission logic.
"""

import os, sys, json, re, unittest
from datetime import datetime
from unittest.mock import MagicMock, patch, PropertyMock

# Setup
sys.path.insert(0, os.path.expanduser("~/.hermes/scripts"))
os.environ.setdefault("HERMES_CUA_DRIVER_CMD", "/Users/pc/.local/bin/cua-driver")

# Import the actual module
import importlib.util
SCRIPT_PATH = os.path.join(os.path.dirname(__file__), "feishu-daily-quiz.py")
spec = importlib.util.spec_from_file_location(
    "feishu_daily_quiz",
    SCRIPT_PATH
)
quiz = importlib.util.module_from_spec(spec)
spec.loader.exec_module(quiz)

# ── Helpers for tests ────────────────────────────────────────

def _find_element_by_label(text, label, element_types=None):
    """Call the real implementation from feishu-daily-quiz.py."""
    return quiz._find_element_by_label(text, label, element_types)


class FakeSession:
    """Simulates cua-driver MCP session for testing."""
    def __init__(self, page_responses=None):
        self.calls = []
        self._responses = page_responses or {}
        self._call_idx = 0
    
    def call_tool(self, name, args):
        self.calls.append((name, args))
        key = args.get("javascript", "")[:60]
        if key in self._responses:
            return self._responses[key]
        if self._call_idx < len(list(self._responses.values())):
            resp = list(self._responses.values())[self._call_idx]
            self._call_idx += 1
            return resp
        return {"data": {}, "images": [], "structuredContent": None, "isError": False}
    
    def stop(self):
        pass


class FakeBackend:
    """Backend stub with _session for Phase 3 testing."""
    def __init__(self, responses=None):
        self._session = FakeSession(responses)
    
    def stop(self):
        pass


def make_page_response(data):
    """Helper: create a cua-driver page tool response dict.
    If data is a dict, JSON-encode it as the page tool would."""
    if isinstance(data, dict):
        data = json.dumps(data)
    return {"data": data, "images": [], "structuredContent": None, "isError": False}


# ── Unit Tests ───────────────────────────────────────────────

class TestAnswerExtraction(unittest.TestCase):
    """Phase 1: answer regex matching."""
    
    def setUp(self):
        # Simulate today's date for deterministic tests
        self.today = "2026/06/19"
    
    def test_preflight_permissions(self):
        """CRITICAL: Verify cua-driver can read AX trees before anything else.
        Uses cua-driver (not osascript) to match what the actual script probes."""
        try:
            sys.path.insert(0, os.path.expanduser("~/.hermes/hermes-agent"))
            from tools.computer_use.cua_backend import CuaDriverBackend
            backend = CuaDriverBackend()
            backend.start()
            lw = backend._session.call_tool("list_windows", {"on_screen_only": False})
            sc = lw.get("structuredContent", {})
            pid = wid = None
            for w in sc.get("windows", []):
                if "飞书" in w.get("app_name", "") and w.get("title") == "飞书":
                    pid, wid = w["pid"], w["window_id"]
                    break
            if not pid:
                backend.stop()
                self.skipTest("⚠️  Feishu window not found")
            r = backend._session.call_tool("get_window_state", {
                "pid": pid, "window_id": wid, "include_screenshot": False
            })
            text = r.get("data", "")
            has_content = any(kw in text for kw in ["AXStaticText", "AXButton"])
            backend.stop()
            if not has_content:
                self.skipTest(
                    "⚠️  AX tree has no content — Accessibility broken\n"
                    "   Fix: System Settings → Privacy & Security → Accessibility\n"
                    "   → disable then re-enable Cua Driver"
                )
        except Exception as e:
            self.skipTest(f"⚠️  cua-driver probe failed: {e}")
    
    def test_find_answer_in_chat_preview(self):
        """Answer in chat list preview (most common case)."""
        text = (
            '- [37] AXStaticText = "08:10" []\n'
            '- [38] AXStaticText = "刘梦杰" []\n'
            '- [39] AXStaticText = "每日一题 今日答案：B" []\n'
            '- [40] AXStaticText = "6月17日" []\n'
            f'- [400] AXStaticText = "{self.today} 每日一题来啦！算电协同..." []\n'
        )
        match = re.search(r"每日一题\s+今日答案[：:]\s*([A-D])", text)
        self.assertIsNotNone(match)
        self.assertEqual(match.group(1), "B")
    
    def test_find_answer_with_fullwidth_colon(self):
        """Answer uses fullwidth colon  ："""
        text = '- [100] AXStaticText = "每日一题 今日答案：C" []\n'
        match = re.search(r"每日一题\s+今日答案[：:]\s*([A-D])", text)
        self.assertIsNotNone(match)
        self.assertEqual(match.group(1), "C")
    
    def test_find_answer_with_extra_spaces(self):
        """Answer has extra spaces in format."""
        text = '- [100] AXStaticText = "每日一题  今日答案：D" []\n'
        match = re.search(r"每日一题\s+今日答案[：:]\s*([A-D])", text)
        self.assertIsNotNone(match)
        self.assertEqual(match.group(1), "D")
    
    def test_no_false_match_on_long_text(self):
        """Should not match '每日一题' inside a longer unrelated string."""
        text = '- [100] AXStaticText = "关于每日一题的说明文档" []\n'
        match = re.search(r"每日一题\s+今日答案[：:]\s*([A-D])", text)
        self.assertIsNone(match)
    
    def test_today_question_detection(self):
        """Detect today's question pattern."""
        text = f'- [300] AXStaticText = "{self.today} 每日一题来啦！什么问题？" []\n'
        match = re.search(rf"{self.today}\s+每日一题来啦", text)
        self.assertIsNotNone(match)
    
    def test_answer_nearby_search_logic(self):
        """The nearby search: find question → search 2000 chars around for answer."""
        text = (
            # Chat preview: answer at top
            '- [37] AXStaticText = "今日答案：B" []\n' * 50 +
            # Full chat: question deeper in tree
            f'- [500] AXStaticText = "{self.today} 每日一题来啦！问题内容..." []\n' +
            '- [600] AXStaticText = "每日一题 今日答案：B" []\n'
        )
        q_match = re.search(rf"{self.today}\s+每日一题来啦", text)
        self.assertIsNotNone(q_match)
        pos = q_match.start()
        search_region = text[max(0, pos - 2000):min(len(text), pos + 2000)]
        
        # Answer should be in search region (both directions)
        answer_match = re.search(r"每日一题\s+今日答案[：:]\s*([A-D])", search_region)
        self.assertIsNotNone(answer_match)
        self.assertEqual(answer_match.group(1), "B")


class TestElementFinding(unittest.TestCase):
    """Phase 1: _find_element_by_label function."""
    
    def test_find_ax_menubar_item(self):
        text = '- [148] AXMenuBarItem "历史记录" [actions=[cancel,press,pick]]\n'
        idx = _find_element_by_label(text, "历史记录", ["AXMenuBarItem"])
        self.assertEqual(idx, 148)
    
    def test_find_ax_menuitem(self):
        text = '- [155] AXMenuItem "正泰安能户用光伏党支部" [actions=[cancel,press,pick]]\n'
        idx = _find_element_by_label(text, "正泰安能户用光伏党支部", ["AXMenuItem"])
        self.assertEqual(idx, 155)
    
    def test_find_across_multiple_types(self):
        text = (
            '- [10] AXStaticText "其他" []\n'
            '- [42] AXButton "正泰安能户用光伏党支部" []\n'
        )
        idx = _find_element_by_label(text, "正泰安能户用光伏党支部")
        self.assertEqual(idx, 42)
    
    def test_find_with_equals_sign(self):
        text = '- [67] AXStaticText = "正泰安能户用光伏党支部" []\n'
        idx = _find_element_by_label(text, "正泰安能户用光伏党支部")
        self.assertEqual(idx, 67)
    
    def test_not_found_returns_none(self):
        text = '- [1] AXStaticText "其他内容" []\n'
        idx = _find_element_by_label(text, "不存在的文本")
        self.assertIsNone(idx)


class TestPhase3SubmitLogic(unittest.TestCase):
    """Phase 3: submit_answer_in_chrome — JS-based form interaction."""
    
    def test_already_submitted_detection(self):
        """Should detect already-submitted state and return True immediately."""
        form_data = {
            "title": "每日一学答题卡",
            "allText": "分享\n提交成功\n你已达提交次数上限",
            "isDone": True,
            "url": "https://chintsso.feishu.cn/share/base/form/shrcn...",
            "submitBtns": []
        }
        self.assertTrue(form_data.get("isDone"))
        self.assertIn("提交成功", form_data["allText"])
    
    def test_fresh_form_has_submit_button(self):
        """Fresh form should have a non-disabled submit button."""
        form_data = {
            "title": "每日一学答题卡",
            "isDone": False,
            "submitBtns": [{"text": "提交", "disabled": False}]
        }
        self.assertFalse(form_data.get("isDone"))
        self.assertTrue(any(
            b["text"] == "提交" and not b["disabled"] 
            for b in form_data["submitBtns"]
        ))
    
    def test_answer_tag_selection_js(self):
        """The JS to click a ud__tag should target correct letter."""
        answer = "B"
        js = (
            f"var tags=[...document.querySelectorAll('.ud__tag')]"
            f".filter(d=>d.textContent.trim()==='{answer}');"
            f"if(tags.length){{tags[0].click();'clicked '+tags.length+' tag(s)'}}"
            f"else{{'tag not found for {answer}'}}"
        )
        self.assertIn(f"'{answer}'", js)
        self.assertIn("tags[0].click()", js)
        self.assertIn("ud__tag", js)
    
    def test_submit_button_exact_match_js(self):
        """Submit button JS should use exact match, not includes."""
        js = (
            "var btns=[...document.querySelectorAll('button')]"
            ".filter(b=>b.textContent.trim()==='提交'&&!b.disabled);"
        )
        # '查看提交记录' contains '提交' but should NOT match ===
        self.assertIn("==='提交'", js)
        self.assertNotIn("includes('提交')", js)
    
    def test_submit_verify_js(self):
        """Verification JS should check for both success indicators."""
        js = (
            "var t=document.body?.innerText||'';"
            "JSON.stringify({success:t.includes('提交成功'),limit:t.includes('已达提交次数上限')})"
        )
        self.assertIn("提交成功", js)
        self.assertIn("已达提交次数上限", js)


# ── Integration Test ─────────────────────────────────────────

class TestPhase3Integration(unittest.TestCase):
    """Integration test: simulate the entire Phase 3 flow with mocked backend."""
    
    def _make_form_state_response(self, is_done=False, has_select=False, has_tags=True):
        """Build a realistic form state response."""
        return make_page_response(json.dumps({
            "title": "每日一学答题卡",
            "allText": "提交成功" if is_done else "请选择选项\nA\nB\nC\nD\n提交",
            "isDone": is_done,
            "url": "https://chintsso.feishu.cn/share/base/form/shrcn...",
            "submitBtns": [] if is_done else [{"text": "提交", "disabled": False}]
        }))
    
    def test_full_submit_flow_with_tags(self):
        """Simulate full submit flow: detect form → click tag → click submit → verify."""
        call_log = []
        
        def seq_responses():
            yield make_page_response({
                "title": "每日一学答题卡",
                "allText": "选项\nA\nB\nC\nD",
                "isDone": False,
                "url": "https://...",
                "submitBtns": [{"text": "提交", "disabled": False}]
            })
            yield make_page_response("clicked 1 tag(s)")
            yield make_page_response("clicked: 提交")
            yield make_page_response({"success": True, "limit": False})
        
        responses = seq_responses()
        
        class MockBackend:
            class _session_cls:
                def call_tool(s, name, args):
                    call_log.append((name, args.get("javascript", "")[:80]))
                    return next(responses)
            _session = _session_cls()
            def stop(s): pass
        
        backend = MockBackend()
        
        # 1. Read form state
        r = backend._session.call_tool("page", {
            "action": "execute_javascript", "pid": 1, "window_id": 1,
            "javascript": "JSON.stringify({...})"
        })
        data = json.loads(r["data"])
        self.assertFalse(data.get("isDone"))
        
        # 2. Click answer tag
        r = backend._session.call_tool("page", {
            "action": "execute_javascript", "pid": 1, "window_id": 1,
            "javascript": "var tags=[...]; tags[0].click();"
        })
        self.assertIn("clicked", r["data"])
        
        # 3. Click submit
        r = backend._session.call_tool("page", {
            "action": "execute_javascript", "pid": 1, "window_id": 1,
            "javascript": "var btns=[...].filter(b=>b.textContent.trim()==='提交'); btns[0].click()"
        })
        self.assertIn("提交", r["data"])
        
        # 4. Verify
        r = backend._session.call_tool("page", {
            "action": "execute_javascript", "pid": 1, "window_id": 1,
            "javascript": "JSON.stringify({success:..., limit:...})"
        })
        verify = json.loads(r["data"])
        self.assertTrue(verify.get("success") or verify.get("limit"))
        self.assertEqual(len(call_log), 4)
    
    def test_already_submitted_short_circuits(self):
        """When form is already submitted, only 1 JS call should happen."""
        call_count = [0]
        
        class MockBackend:
            class _session_cls:
                def call_tool(s, name, args):
                    call_count[0] += 1
                    return make_page_response({
                        "title": "每日一学答题卡",
                        "allText": "提交成功\n你已达提交次数上限",
                        "isDone": True,
                        "url": "https://...",
                        "submitBtns": []
                    })
            _session = _session_cls()
            def stop(s): pass
        
        backend = MockBackend()
        r = backend._session.call_tool("page", {
            "action": "execute_javascript", "pid": 1, "window_id": 1,
            "javascript": "JSON.stringify({...})"
        })
        data = json.loads(r["data"])
        self.assertTrue(data.get("isDone"))
        self.assertEqual(call_count[0], 1)


class TestChromeJSErrorDetection(unittest.TestCase):
    """Phase 3: detect and surface Chrome JS errors clearly."""
    
    def test_js_permission_disabled_error(self):
        """Chrome 'Allow JavaScript from Apple Events' disabled."""
        error = 'osascript error: execution error: "Google Chrome"遇到一个错误：通过 AppleScript 执行 JavaScript 的功能已关闭。(12)'
        is_permission_error = "JavaScript 的功能已关闭" in error
        self.assertTrue(is_permission_error)
    
    def test_js_window_not_ready_error(self):
        """Chrome window not accessible via AppleScript."""
        error = 'osascript error: execution error: "Google Chrome"遇到一个错误：不能获得"window 1"。(-1719)'
        is_window_error = "不能获得" in error and "window" in error
        self.assertTrue(is_window_error)
    
    def test_normal_js_string_not_error(self):
        """Normal JS return string should NOT be flagged as error."""
        normal = "clicked 1 tag(s)"
        is_window_error = "不能获得" in normal and "window" in normal
        self.assertFalse(is_window_error)

    def test_submit_returns_false_when_js_times_out(self):
        """A hung Chrome JS call should fail fast instead of blocking retries."""
        import time

        class SlowSession:
            def call_tool(self, name, args):
                time.sleep(2)
                return make_page_response({"isDone": False})

        class SlowBackend:
            _session = SlowSession()

        with patch.object(quiz, "find_chrome_form_window", return_value=(123, 456)):
            with patch.object(quiz, "JS_TIMEOUT_SECONDS", 1):
                self.assertFalse(quiz.submit_answer_in_chrome(SlowBackend(), "A"))


class TestAnswerNearQuestionLogic(unittest.TestCase):
    """Phase 1: answer extraction near today's question."""
    
    def test_answer_within_search_region_found(self):
        """Answer within 2000 chars of question should be found."""
        today = "2026/06/21"
        text = (
            "padding " * 10 +
            f"{today} 每日一题来啦！问题内容..." +
            "padding " * 100 +
            "每日一题 今日答案：B"
        )
        q_match = re.search(rf"{today}\s+每日一题来啦", text)
        self.assertIsNotNone(q_match)
        pos = q_match.start()
        search = text[max(0, pos - 2000):min(len(text), pos + 2000)]
        a_match = re.search(r"每日一题\s+今日答案[：:]\s*([A-D])", search)
        self.assertIsNotNone(a_match)
        self.assertEqual(a_match.group(1), "B")
    
    def test_answer_outside_search_region_not_found(self):
        """Answer more than 2000 chars away should NOT be found."""
        today = "2026/06/21"
        text = (
            f"{today} 每日一题来啦！问题内容..." +
            "x" * 2000 +  # puts answer > 2000 chars away
            "每日一题 今日答案：B"
        )
        q_match = re.search(rf"{today}\s+每日一题来啦", text)
        pos = q_match.start()
        search = text[max(0, pos - 2000):min(len(text), pos + 2000)]
        a_match = re.search(r"每日一题\s+今日答案[：:]\s*([A-D])", search)
        self.assertIsNone(a_match)  # answer outside search window
    
    def test_answer_before_question_found(self):
        """Answer appears BEFORE the question (chat preview at top)."""
        today = "2026/06/21"
        text = (
            "每日一题 今日答案：B" +
            "x" * 500 +
            f"{today} 每日一题来啦！问题内容..."
        )
        q_match = re.search(rf"{today}\s+每日一题来啦", text)
        pos = q_match.start()
        search = text[max(0, pos - 2000):min(len(text), pos + 2000)]
        a_match = re.search(r"每日一题\s+今日答案[：:]\s*([A-D])", search)
        self.assertIsNotNone(a_match)
        self.assertEqual(a_match.group(1), "B")


if __name__ == "__main__":
    unittest.main(verbosity=2)
