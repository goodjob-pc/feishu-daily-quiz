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
            "每日一题 今日答案：C\n"
            "x" * 500
            + "2026/06/23 每日一题来啦！问题内容\n"
            + "A.选项\nB.选项\nC.选项\n"
        )

        self.assertEqual(
            quiz.extract_answer_from_text(text, date(2026, 6, 23), window_chars=2000),
            "C",
        )

    def test_rejects_undated_history_answer(self):
        text = "08:10\n每日一题 今日答案：B\n"

        self.assertIsNone(
            quiz.extract_answer_from_text(text, date(2026, 6, 23), window_chars=2000)
        )

    def test_rejects_answer_outside_search_window(self):
        text = "2026/06/23 每日一题来啦！" + ("x" * 2100) + "每日一题 今日答案：A"

        self.assertIsNone(
            quiz.extract_answer_from_text(text, date(2026, 6, 23), window_chars=2000)
        )


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


if __name__ == "__main__":
    unittest.main(verbosity=2)
