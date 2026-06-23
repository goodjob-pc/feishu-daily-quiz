# 飞书每日答题自动化

> 版本 1.1 | 2026-06-21 | macOS 专属

## 1. 需求概述

### 1.1 背景

正泰安能户用光伏党支部飞书群每日发布答题（选择题 A~D），需在飞书云文档表单中选择正确答案并提交。纯手工操作重复、容易遗忘。

### 1.2 目标

全自动完成：**读取答案 → 打开表单 → 选择答案 → 提交**，工作日每天自动执行，失败可重试。

### 1.3 约束

- 飞书无可用 API（非管理员），只能通过桌面自动化
- 答题表单使用飞书自定义 UI 组件（非标准 HTML 表单）
- 答案发布时间不固定（通常在 7:00~8:30）
- 截止时间 16:00
- 用户日常使用 macOS，不可干扰办公

---

## 2. 技术方案

### 2.1 整体架构

```
cron (Hermes cronjob, no_agent)
  └── feishu-daily-quiz.sh (shell wrapper, 随机延迟 + 重试)
       └── feishu-daily-quiz.py (Python, cua-driver MCP 驱动)
            ├── Phase 1: 飞书群 AX Tree → 正则提取答案
            ├── Phase 2: AX 点击"前去答题" → Chrome 打开表单
            └── Phase 3: Chrome JS 注入 → 选择答案 + 提交
```

### 2.2 技术选型

| 组件 | 选择 | 理由 |
|------|------|------|
| 桌面自动化 | cua-driver MCP | macOS Accessibility API，后台运行不抢焦点 |
| 飞书交互 | AX Tree 解析 | 飞书不暴露 DOM，只能用 Accessibility |
| 表单提交 | Chrome JS 注入 | 飞书自定义组件（ud__tag），AX 不可交互 |
| 调度 | Hermes cronjob | no_agent 零 token 消耗，脚本直达 |
| 断点续跑 | JSON state file | 进程崩溃后可从答题阶段恢复 |

### 2.3 关键设计

#### 2.3.1 三阶段 + 断点续跑

```text
Phase 1 (find_answer)     → save_state(answer=A, phase=answer_found)
Phase 2 (click_go_answer)  → save_state(answer=A, phase=form_opened, chrome_pid=X)
Phase 3 (submit_answer)    → clear_state()

exit 0 = 成功 (清除 state)
exit 1 = 致命错误 (清除 state, 不重试)
exit 2 = 答案未找到 (保留 state, 30分钟后重试)
exit 3 = 前置完成但提交失败 (保留 state, 60秒后快速重试)
```

#### 2.3.2 飞书群导航双策略

1. **History 菜单**（优先）：点击 `AXMenuBarItem "历史记录"` → 找到 `AXMenuItem "正泰安能户用光伏党支部"` → 点击
2. **Cmd+K 搜索**（兜底）：`Cmd+K` → 输入群名 → `↓` + `Enter`

#### 2.3.3 答案提取

正则：`每日一题\s+今日答案[：:]\s*([A-D])`

策略：
- 优先：找到今日 `YYYY/MM/DD 每日一题来啦` → 搜索前后 2000 字符
- 若只有无日期答案候选，视为答案未发布，避免误提交历史答案

#### 2.3.4 Chrome 表单交互

使用 `page.execute_javascript` 注入 JS（非 AX 操作）：
- 检测表单状态（是否已提交、有无 select/tag/submit button）
- 点击 `ud__tag` 选择答案（飞书自定义组件）
- 点击"提交"按钮（精确匹配 `===`，避免误匹配"查看提交记录"）
- 验证结果（"提交成功" 或 "已达提交次数上限"）
- 成功后 `window.close()` 关闭标签页
- **JS 超时保护**：每次 JS 调用包裹 SIGALRM 20s 超时，防止 Chrome 卡死阻塞重试循环

#### 2.3.5 Shell 重试逻辑

- 08:00 触发，随机延迟 0~60 秒（避免所有重试同时启动）
- exit 2（答案未发布）→ 等待 30 分钟
- exit 3（提交失败）→ 等待 60 秒
- exit 1（致命错误）→ 停止重试
- 超过 16:00 → 放弃
- 连续失败 >5 次 → 输出醒目告警

#### 2.3.6 Pre-flight 检查

启动时立即探测 cua-driver AX 权限，避免跑了一堆才报权限错误。

---

## 3. 文件清单

| 文件 | 行数 | 说明 |
|------|------|------|
| `feishu-daily-quiz.py` | 658 | 主脚本：三阶段执行 + 断点续跑 + JS 超时保护 |
| `feishu-daily-quiz.sh` | 64 | Shell wrapper：随机延迟 + 重试循环 (最多 14 次) |
| `feishu-quiz-now.sh` | 5 | 手动立即触发（无延迟） |
| `test_feishu_quiz.py` | 463 | 单元测试 + 集成测试 (19 个用例) |

### 环境变量

| 变量 | 默认值 | 说明 |
|------|--------|------|
| `HERMES_CUA_DRIVER_CMD` | `/Users/pc/.local/bin/cua-driver` | cua-driver 路径 |

### 配置文件（脚本内硬编码）

```python
FEISHU_GROUP = "正泰安能户用光伏党支部"
DEADLINE_HOUR = 16
```

---

## 4. 测试

### 4.1 运行测试

```bash
cd ~/.hermes/scripts
python3 test_feishu_quiz.py
```

### 4.2 测试覆盖

| 测试类 | 用例数 | 覆盖范围 |
|--------|--------|---------|
| `TestAnswerExtraction` | 7 | 正则匹配：中文/英文冒号、多余空格、误匹配防护、临近搜索、历史答案防护 |
| `TestElementFinding` | 5 | AX 元素索引查找：MenuBarItem、MenuItem、跨类型搜索 |
| `TestPhase3SubmitLogic` | 6 | 表单 JS 交互：已提交检测、标签选择、提交按钮精确匹配、Chrome 窗口防误匹配 |
| `TestPhase3Integration` | 2 | 完整提交流程模拟、已提交短路 |
| `TestChromeJSErrorDetection` | 4 | JS 权限错误、窗口未就绪、正常返回不误判、JS 超时保护 |
| `TestAnswerNearQuestionLogic` | 3 | 答案在搜索窗口内/外/前 |
| `TestShellRetryContract` | 1 | exit 1 致命错误不重试 |
| **总计** | **29** | **含 1 个 pre-flight 权限探测（需真实环境）** |

---

## 5. 部署

### 5.1 Cron 配置

```text
Job ID:    f4c80e659608
Schedule:  0 8 * * * (每天 08:00)
Script:    feishu-daily-quiz.sh
Mode:      no_agent (零 token)
Deliver:   all (推送到所有已连接平台)
```

### 5.2 前置条件

1. macOS 已授权 cua-driver 的 Accessibility 权限
2. Chrome 已启用「允许 Apple 事件中的 JavaScript」
   - Chrome → 查看 → 开发者 → 允许 Apple 事件中的 JavaScript
3. 飞书已登录，群聊"正泰安能户用光伏党支部"在最近聊天列表中
4. Chrome SSO 登录态有效（已通过 chintsso.feishu.cn 认证）

### 5.3 日志

```text
~/.hermes/logs/feishu-quiz/YYYY-MM-DD.log   # 每日详细日志
~/.hermes/cache/feishu-quiz-state.json       # 断点续跑状态
```

### 5.4 手动触发

```bash
bash ~/.hermes/scripts/feishu-quiz-now.sh
```

---

## 6. 已知问题与约束

| 问题 | 影响 | 状态 |
|------|------|------|
| 飞书 UI 更新可能改变 AX Tree 结构 | History 菜单/群名索引失效 | 双策略兜底 |
| Chrome JS 权限被关闭 | Phase 3 完全无法工作 | pre-flight 不覆盖（依赖 Chrome 设置） |
| SSO 登录态过期 | 表单打不开 | 需手动重新登录 chintsso.feishu.cn |
| 飞书自定义组件改版 | `ud__tag` 选择器失效 | 无兜底，需更新 JS 选择器 |
| 非 macOS 不可用 | cua-driver 依赖 macOS AX API | 设计约束 |

---

## 7. 后续优化方向

1. **飞书推送**：对接 Hermes Gateway，答题结果实时推送到飞书 Bot（当前 `deliver: all` 待 `/sethome`）
2. **统计仪表**：答题成功率、耗时趋势、失败原因分布
3. **Chrome JS 权限自动检测**：启动时验证 Chrome JS 可用性
4. **多群支持**：支持多个飞书群（当前仅一个）
5. **Windows 移植**：使用 Win32 API 替代 macOS AX（需要不同驱动）
