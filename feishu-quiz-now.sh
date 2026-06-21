#!/bin/bash
# 飞书每日答题 - 手动触发（无随机延迟，立即执行）
export HERMES_CUA_DRIVER_CMD="/Users/pc/.local/bin/cua-driver"
export PATH="$HOME/.local/bin:$PATH"
~/.hermes/hermes-agent/venv/bin/python3 ~/.hermes/scripts/feishu-daily-quiz.py
