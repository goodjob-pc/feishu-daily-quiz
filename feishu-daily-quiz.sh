#!/bin/bash
# 飞书每日答题 - Shell wrapper
# 设置环境, 运行 Python 脚本, 带重试逻辑

export HERMES_CUA_DRIVER_CMD="/Users/pc/.local/bin/cua-driver"
export PATH="$HOME/.local/bin:$PATH"
HERMES_VENV="$HOME/.hermes/hermes-agent/venv/bin/python3"
SCRIPT="$HOME/.hermes/scripts/feishu-daily-quiz.py"
STATE_FILE="$HOME/.hermes/cache/feishu-quiz-state.json"
DEADLINE=16
MAX_RETRIES=14
ATTEMPT=0
START_TIME=$(date +%s)

# ── Random delay 0-60 seconds ──────────────────────────────
DELAY=$((RANDOM % 60))
echo "🚀 飞书每日答题启动 ($(date '+%H:%M:%S'), 随机延迟 ${DELAY}s)"

# Notification: startup (only on first attempt, not retries)
echo "📬 飞书答题 | 启动 | $(date '+%H:%M') | 预计 ${DELAY}s 后开始"

sleep "$DELAY"

for i in $(seq 1 $MAX_RETRIES); do
    ATTEMPT=$i
    HOUR=$(date +%H)
    
    if [ "$HOUR" -ge "$DEADLINE" ]; then
        ELAPSED=$((($(date +%s) - START_TIME) / 60))
        echo "📬 飞书答题 | ❌ 超时 | $(date '+%H:%M') | ${DEADLINE}:00 截止，共尝试 ${ATTEMPT} 次，耗时 ${ELAPSED} 分钟"
        rm -f "$STATE_FILE"
        exit 1
    fi
    
    echo "--- 第 $i 次尝试 ($(date '+%H:%M:%S')) ---"
    
    if "$HERMES_VENV" "$SCRIPT"; then
        ELAPSED=$((($(date +%s) - START_TIME) / 60))
        echo "📬 飞书答题 | ✅ 成功 | $(date '+%H:%M') | 第 ${ATTEMPT} 次尝试 | 总耗时 ${ELAPSED} 分钟"
        rm -f "$STATE_FILE"
        exit 0
    fi
    
    EXIT_CODE=$?
    if [ $EXIT_CODE -eq 2 ]; then
        echo "📬 飞书答题 | ⏳ 重试 | $(date '+%H:%M') | 答案未发布，30 分钟后第 $((i+1)) 次尝试"
        sleep 1800
    elif [ $EXIT_CODE -eq 3 ]; then
        echo "📬 飞书答题 | ⚠️ 重试 | $(date '+%H:%M') | 前置完成，60 秒后第 $((i+1)) 次尝试"
        sleep 60
    else
        # Consecutive failures > 5 trigger an alert
        if [ $i -gt 5 ]; then
            echo "📬 飞书答题 | ⚠️ 连续失败 | $(date '+%H:%M') | 已失败 ${i} 次，exit=${EXIT_CODE}"
        fi
        echo "📬 飞书答题 | ❌ 重试 | $(date '+%H:%M') | exit=${EXIT_CODE}，30 分钟后第 $((i+1)) 次尝试"
        sleep 1800
    fi
done

ELAPSED=$((($(date +%s) - START_TIME) / 60))
echo "📬 飞书答题 | ❌ 放弃 | $(date '+%H:%M') | ${MAX_RETRIES} 次全部失败，耗时 ${ELAPSED} 分钟"
rm -f "$STATE_FILE"
exit 1
