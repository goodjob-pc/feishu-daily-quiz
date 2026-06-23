# 飞书每日答题自动化 — 代码审查交接文档

> **版本** v1.1 | **日期** 2026-06-21 | **平台** macOS 专属  
> **仓库** https://github.com/goodjob-pc/feishu-daily-quiz  
> **用途** 交付同事或其他 AI 进行需求理解、代码审查和优化建议

---

## 目录

1. [项目背景与需求](#1-项目背景与需求)
2. [技术架构](#2-技术架构)
3. [代码结构详解](#3-代码结构详解)
4. [测试策略与覆盖](#4-测试策略与覆盖)
5. [部署与运维](#5-部署与运维)
6. [已知问题与约束](#6-已知问题与约束)
7. [后续优化方向](#7-后续优化方向)

---

## 1. 项目背景与需求

### 1.1 业务背景

正泰安能户用光伏党支部飞书群每日发布一道选择题（选项 A~D），成员需点击飞书云文档链接，在表单中选择正确答案并提交。纯手工操作重复度高、容易遗忘。

### 1.2 核心目标

全自动完成以下流程，无需人工介入：

```
读取答案 → 打开飞书云文档表单 → 选择正确选项 → 提交
```

### 1.3 约束条件

| 约束 | 说明 |
|------|------|
| 无 API | 飞书非管理员，无法使用开放平台 API |
| 自定义 UI | 答题表单使用飞书私有组件（`ud__tag`），非标准 HTML |
| 答案时间不定 | 每日答案发布时间在 7:00~8:30 波动 |
| 截止时间 | 当日 16:00 后不可提交 |
| 后台运行 | 不可干扰用户日常 macOS 办公 |
| SSO 认证 | 表单 URL 需通过 `chintsso.feishu.cn` 单点登录 |

### 1.4 用户环境

- macOS 26.x，多显示器
- 飞书桌面客户端（中文版，进程名 "飞书"）
- Google Chrome（主力浏览器，已登录飞书 SSO）
- cua-driver（macOS 桌面自动化驱动）

---

## 2. 技术架构

### 2.1 整体架构图

```
┌─────────────────────────────────────────────┐
│  Hermes Cronjob (no_agent, 零 token)         │
│  Schedule: 0 8 * * * (每天 08:00)            │
│  Script: feishu-daily-quiz.sh                │
└─────────────────┬───────────────────────────┘
                  │
                  ▼
┌─────────────────────────────────────────────┐
│  feishu-daily-quiz.sh                        │
│  ├─ 随机延迟 0~60s                            │
│  ├─ 循环重试 (最多 14 次)                      │
│  │   ├─ exit 2 → sleep 1800s (答案未发布)     │
│  │   ├─ exit 3 → sleep 60s   (提交失败)       │
│  │   └─ exit 1 → 终止                         │
│  └─ 超时 16:00 → 放弃                         │
└─────────────────┬───────────────────────────┘
                  │
                  ▼
┌─────────────────────────────────────────────┐
│  feishu-daily-quiz.py (658 行)               │
│                                              │
│  Pre-flight: cua-driver AX 权限检查          │
│                                              │
│  Phase 1 — 获取答案 (AX Tree 解析)           │
│  ├─ activate_feishu()                        │
│  │   ├─ Primary: open -a Lark (NSWorkspace)  │
│  │   └─ Fallback: osascript (Accessibility)  │
│  ├─ 导航到群聊                               │
│  │   ├─ Primary: History 菜单 → AXMenuItem   │
│  │   └─ Fallback: Cmd+K 搜索                 │
│  ├─ 读 AX Tree                               │
│  ├─ 正则提取 每日一题 今日答案：[A-D]         │
│  └─ save_state(answer=X) ← 断点              │
│                                              │
│  Phase 2 — 打开表单 (AX 点击)                 │
│  ├─ click_go_answer()                        │
│  │   ├─ 找到 AXButton "前去答题"              │
│  │   ├─ 点击 → 等待 Chrome 打开              │
│  │   └─ find_chrome_form_window()            │
│  └─ save_state(chrome_pid=X) ← 断点          │
│                                              │
│  Phase 3 — 提交答案 (Chrome JS 注入)          │
│  ├─ submit_answer_in_chrome()                │
│  │   ├─ JS: 检测表单状态 (isDone/hasTags/...) │
│  │   ├─ JS: 点击 ud__tag 选择答案            │
│  │   ├─ JS: 点击 "提交" 按钮                  │
│  │   ├─ JS: 验证提交结果                      │
│  │   ├─ JS: window.close() 清理              │
│  │   └─ JS 超时保护: SIGALRM 20s             │
│  └─ clear_state() ← 成功                     │
└─────────────────────────────────────────────┘
```

### 2.2 技术选型

| 组件 | 选择 | 理由 |
|------|------|------|
| 桌面自动化 | [cua-driver](https://github.com/nousresearch/hermes-agent) MCP | macOS Accessibility API，后台运行不抢用户焦点、不切 Space |
| 飞书 UI 交互 | AX Tree 文本解析 + 正则 | 飞书不暴露 DOM/HTML，只能通过 Accessibility 接口读取 |
| Chrome 表单操作 | `page.execute_javascript` (Apple Events) | 飞书自定义 `ud__tag` 组件 AX 不可交互，只能注入 JS |
| 调度 | Hermes cronjob (`no_agent: true`) | 零 LLM token 消耗，脚本直接执行 |
| 状态持久化 | JSON 文件断点续跑 | 进程崩溃后从中间阶段恢复，避免重复答题 |
| 飞书激活 | `open -a Lark` (NSWorkspace) | 比 AppleScript 更可靠 |

### 2.3 退出码语义

| 退出码 | 含义 | Shell 行为 | State 处理 |
|--------|------|-----------|------------|
| 0 | 成功提交 | 退出循环，成功 | 清除 state |
| 1 | 致命错误（权限/环境） | 退出循环，失败 | 清除 state |
| 2 | 答案未发布 | sleep 1800s 后重试 | 保留 state |
| 3 | 前置完成但提交失败 | sleep 60s 后重试 | 保留 state |

### 2.4 断点续跑机制

```python
state.json 结构:
{
    "date": "2026-06-21",
    "answer": "B",             # Phase 1 产出
    "phase": "form_opened",    # 当前阶段
    "chrome_pid": 12345,       # Phase 2 产出
    "chrome_wid": 678,
    "saved_at": "08:15:30"
}
```

重启时：
- `state.date == today` → 恢复
- `state.answer` 存在 → 跳过 Phase 1
- `state.chrome_pid` 存在 + 窗口仍开着 → 跳过 Phase 2
- 否则从 Phase 1 开始

---

## 3. 代码结构详解

### 3.1 文件清单

| 文件 | 行数 | 职责 |
|------|------|------|
| `feishu-daily-quiz.py` | 658 | 主逻辑：pre-flight + 三阶段执行 + 断点续跑 |
| `feishu-daily-quiz.sh` | 64 | Shell 包装：随机延迟 + 退出码驱动重试 |
| `feishu-quiz-now.sh` | 5 | 手动触发入口（无延迟） |
| `test_feishu_quiz.py` | 463 | 单元测试 + 集成测试 (19+1 用例) |

### 3.2 主脚本 `feishu-daily-quiz.py` 关键函数

#### 配置区 (L9-18)

```python
FEISHU_GROUP = "正泰安能户用光伏党支部"     # 群名（用于 AX 匹配）
DEADLINE_HOUR = 16                            # 截止时间
JS_TIMEOUT_SECONDS = 20                       # Chrome JS 超时
```

#### Pre-flight (L501-539)

```python
main():
    # 1. 启动 cua-driver，探测飞书窗口 AX 权限
    backend = get_backend()
    r = call_tool("get_window_state", pid, wid)
    # 2. 检查 AX Tree 是否包含可交互元素
    if not any(kw in text for kw in ["AXStaticText", "AXButton"]):
        sys.exit(1)  # 权限缺失
    # 3. 检查是否超过截止时间
```

#### Phase 1 — 飞书群获取答案

**`activate_feishu()`** (L100-130)
- Primary: `subprocess.run(["open", "-a", "Lark"])` — NSWorkspace 激活
- Fallback: `osascript` tell process "飞书" set frontmost

**`find_answer_in_feishu(backend)`** (L132-239)
- 自动发现飞书 PID/Window（`list_windows → filter "飞书"`）
- 导航策略：
  - History 菜单：`_find_element_by_label(text, "历史记录")` → 点击 → `_find_element_by_label(text, FEISHU_GROUP)`
  - Cmd+K 兜底：`hotkey(["cmd","k"])` → `type_text(FEISHU_GROUP)` → `press_key("down")` → `press_key("return")`
- 答案提取：
  - 优先：找 `YYYY/MM/DD 每日一题来啦` → 前后 2000 字符搜索 `每日一题\s+今日答案[：:]\s*([A-D])`
  - 兜底：取 AX Tree 中最后一个匹配

**`_find_element_by_label(text, label)`** (L241-260)
- 多类型 AX 元素索引查找
- 支持 `AXStaticText`、`AXMenuItem`、`AXCell`、`AXButton`、`AXMenuBarItem`
- 正则：`\[\s*(\d+)\s*\].*{TYPE}.*["=]([^"]*{LABEL}[^"]*)["] `

#### Phase 2 — 点击"前去答题"

**`click_go_answer(backend)`** (L262-319)
- 读 AX Tree，找 `AXButton "前去答题"`
- 优先匹配今日日期附近的按钮
- 等待 Chrome 窗口出现（最多 10s）

**`find_chrome_form_window(backend)`** (L321-356)
- 遍历所有 Chrome 窗口
- 匹配标题含 "答题" / "答题卡"
- 匹配地址栏含 `feishu.cn/share/base/form`

#### Phase 3 — Chrome JS 提交

**`submit_answer_in_chrome(backend, answer)`** (L358-499)
- 内嵌 `js(code)` 闭包：调用 `page.execute_javascript`，返回 `(parsed_value, raw_string, is_error)`
- **JS 超时保护**：每次调用包装 `signal.SIGALRM`，20s 无响应 → 捕获 `TimeoutError` 返回 is_error
- 步骤：
  1. **读状态**：`JSON.stringify({isDone, hasSelect, hasTags, submitBtn, url})`
  2. **已提交短路**：`isDone` → 直接返回 True
  3. **选答案**：
     - `<select>` → `s.selectedIndex = {0,1,2,3}` + `dispatchEvent('change')`
     - `ud__tag` → `[...document.querySelectorAll('.ud__tag')].filter(d=>d.textContent.trim()==='{answer}')[0].click()`
  4. **提交**：`[...document.querySelectorAll('button')].find(x=>x.textContent.trim()==='提交'&&!x.disabled).click()` — 注意用 `===` 而非 `includes`，防止匹配"查看提交记录"
  5. **验证**：检查 `document.body.innerText` 包含 "提交成功" 或 "已达提交次数上限"
  6. **清理**：`setTimeout(()=>window.close(), 500)`

**Chrome JS 错误检测** (L389-404)
- `"JavaScript 的功能已关闭"` → Chrome 权限缺失
- `"不能获得" + "window"` → Chrome 窗口未就绪

---

## 4. 测试策略与覆盖

### 4.1 运行测试

```bash
cd ~/Documents/feishu-daily-quiz
python3 test_feishu_quiz.py
```

或指定单个类：

```bash
python3 -m unittest test_feishu_quiz.TestAnswerExtraction
```

### 4.2 测试覆盖矩阵

| 测试类 | 用例 | 覆盖内容 |
|--------|------|---------|
| `TestAnswerExtraction` | 6 | 正则匹配：中文冒号 `：`、英文冒号 `:`、多余空格、误匹配防护、今日题目检测、前后 2000 字符临近搜索 |
| `TestElementFinding` | 5 | AX 元素索引查找：MenuBarItem、MenuItem、跨类型、等号格式、不存在返回 None |
| `TestPhase3SubmitLogic` | 5 | 表单 JS 逻辑：已提交检测、未提交有按钮、标签选择 JS 生成、提交按钮 `===` 精确匹配、验证 JS 正确性 |
| `TestPhase3Integration` | 2 | 完整流程模拟（4 步 JS 调用 + 验证）、已提交短路（1 步 JS 即返回） |
| `TestChromeJSErrorDetection` | 4 | JS 权限错误识别、窗口未就绪识别、正常字符串不误判、**JS 超时保护** |
| `TestAnswerNearQuestionLogic` | 3 | 答案在搜索窗口内被找到、超出搜索窗口不被找到、答案在题目之前被找到 |
| `test_preflight_permissions` | 1 | **集成测试**：真实 cua-driver 权限探测（需真实环境，或自动 skip） |
| **总计** | **19+1** | |

### 4.3 测试设计原则

- **可独立运行**：所有测试可在无飞书/Chrome 环境下运行（除 pre-flight）
- **Mock 驱动**：使用 `FakeSession`/`FakeBackend` 模拟 cua-driver MCP 响应
- **真实函数导入**：`test_feishu_quiz.py` 通过 `importlib` 导入真实的 `_find_element_by_label` 和 `submit_answer_in_chrome`，而非 copy-paste
- **无副作用**：测试不写入日志文件、不修改 state.json

---

## 5. 部署与运维

### 5.1 Herems Cron 配置

```yaml
Job ID:   f4c80e659608
Name:     飞书每日答题
Schedule: 0 8 * * *      # 每天 08:00
Script:   feishu-daily-quiz.sh
Mode:     no_agent         # 零 token 消耗
Deliver:  all              # 推送到所有已连接平台（需 /sethome）
```

### 5.2 环境前置条件

| 序号 | 条件 | 检查方法 | 失败后果 |
|------|------|---------|---------|
| 1 | cua-driver Accessibility 权限 | `python3 test_feishu_quiz.py TestAnswerExtraction.test_preflight_permissions` | Phase 1/2 无法工作 |
| 2 | Chrome "允许 Apple 事件中的 JavaScript" | 手动：Chrome → 查看 → 开发者 → 勾选 | Phase 3 无法工作 |
| 3 | 飞书已登录，目标群在最近聊天 | 打开飞书确认群聊可见 | 导航失败（Cmd+K 兜底） |
| 4 | Chrome `chintsso.feishu.cn` 已 SSO 登录 | 从飞书群点击"前去答题"确认可进入表单 | 表单打开后可能显示登录页 |

### 5.3 日志与监控

```
~/.hermes/logs/feishu-quiz/YYYY-MM-DD.log   # 每日详细日志
~/.hermes/cache/feishu-quiz-state.json       # 断点续跑状态（成功后自动清除）
```

日志格式：
```
[2026-06-21 08:15:30] [INFO] ============================================================
[2026-06-21 08:15:30] [INFO]   飞书每日答题 - 2026-06-21 08:15:30
[2026-06-21 08:15:30] [SUCCESS] ✅ Found answer: B
[2026-06-21 08:15:45] [SUCCESS] 🎉 DONE! Answer: B
[2026-06-21 08:15:45] [INFO] 📊 SUMMARY: {"status":"ok","answer":"B","duration_s":15.4,...}
```

### 5.4 手动操作

```bash
# 立即触发（无延迟）
bash ~/.hermes/scripts/feishu-quiz-now.sh

# 查看今日日志
tail -f ~/.hermes/logs/feishu-quiz/$(date +%Y-%m-%d).log

# 清除断点（强制从头开始）
rm ~/.hermes/cache/feishu-quiz-state.json
```

---

## 6. 已知问题与约束

| # | 问题 | 触发条件 | 影响 | 缓解措施 |
|---|------|---------|------|---------|
| 1 | 飞书 AX Tree 结构变化 | 飞书版本更新 | History 菜单/群名无法定位 | Cmd+K 搜索兜底 |
| 2 | Chrome JS 权限被关闭 | Chrome 设置变更/更新 | Phase 3 完全无法工作 | pre-flight 日志提示修正方法 |
| 3 | SSO 登录态过期 | Cookie 过期 | 表单页面显示登录页 | 需手动重新登录 chintsso.feishu.cn |
| 4 | 飞书自定义组件改版 | 飞书表单 UI 更新 | `ud__tag` 选择器失效 | 无兜底，需更新 JS 选择器 |
| 5 | 非 macOS 平台不可用 | 跨平台需求 | 完全无法运行 | 设计约束，cua-driver 仅 macOS |
| 6 | Chrome JS 卡死无响应 | Chrome 渲染进程 hang | 阻塞重试循环 | SIGALRM 20s 超时保护 |
| 7 | 多显示器 Space 切换 | 飞书/Chrome 在不同 Space | open -a 可能激活错误 Space | 方案 A：允许短暂 Space 切换（用户已接受） |

---

## 7. 后续优化方向

| 优先级 | 方向 | 描述 | 改动量 |
|--------|------|------|--------|
| 🔴 P0 | 飞书消息推送 | 对接 Hermes Gateway，结果推到飞书 Bot（当前 `deliver: all` 待设 home channel） | 小 |
| 🟡 P1 | 成功率仪表 | 汇总每日答题成功率、失败原因、耗时趋势 | 中 |
| 🟡 P1 | Chrome JS 权限探测 | 启动时自动检测 Chrome JS 是否可用，不可用直接告警 | 小 |
| 🟢 P2 | 多群支持 | 支持多个飞书群各自答题（当前仅一个群） | 中 |
| 🟢 P2 | 状态通知增强 | 区分更多失败原因：登录态过期 vs UI 变更 vs 网络问题 | 小 |
| 🔵 P3 | Windows 移植 | 使用 Win32 Accessibility API 替代 macOS AX | 大 |
| 🔵 P3 | 配置外置 | `FEISHU_GROUP`、截止时间等从配置文件读取 | 小 |

---

## 附录 A：快速排查指南

### A.1 脚本完全没跑

```bash
# 检查 cron 日志
cronjob action=list | grep f4c80e

# 手动跑看报错
bash ~/.hermes/scripts/feishu-quiz-now.sh
```

### A.2 Phase 1 失败（找不到答案）

1. 确认飞书已登录，群聊在最近聊天列表
2. 手动打开飞书群，确认今日答案格式：`每日一题 今日答案：X`
3. 查看日志中的 AX 上下文：`grep "AX context" ~/.hermes/logs/feishu-quiz/$(date +%Y-%m-%d).log`

### A.3 Phase 3 失败（Chrome JS 无响应）

1. 检查 Chrome → 查看 → 开发者 → **允许 Apple 事件中的 JavaScript** 是否勾选
2. 从飞书群手动点击"前去答题"，确认 Chrome 能进入表单且未停在登录页
3. 如果页面显示登录页 → 手动登录一次

### A.4 重置断点

```bash
rm ~/.hermes/cache/feishu-quiz-state.json
```

---

## 附录 B：依赖清单

| 依赖 | 版本 | 用途 |
|------|------|------|
| Python | 3.11+ | 主脚本运行环境 |
| cua-driver | latest | macOS 桌面自动化 MCP 驱动 |
| Hermes Agent | latest | cron 调度 + `hermes-agent` Python 包 |
| 飞书 (Lark) | macOS 桌面版 | 被控应用 |
| Google Chrome | macOS 版 | 表单载体 |

> **审查提示**：本项目的核心挑战不是代码复杂度，而是**对抗不可控的外部 UI**（飞书 AX Tree 不稳定、飞书自定义组件无标准 DOM、Chrome JS 权限可能被关闭）。建议审查时重点关注错误处理路径、重试策略是否合理、以及断点续跑的状态一致性。
