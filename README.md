# AI Personification

[中文](README.md) · [English](README_EN.md)

一个具有模型外动力、持久人格和后台主动性的长期陪伴 AI 运行时。

它不是把“人设”塞进提示词。程序负责保存人格、需求、关系、记忆证据和行动状态；模型只负责理解语言和实现表达，不能凭自评分制造行动理由。

## 核心机制

![AI Personification system overview](companion-system-architecture.svg?v=20260812-3)

![AI Personification runtime flow](companion-runtime-flow.svg?v=20260812-3)

每个周期都必须形成一个决定，但外部发消息不是必须的：

1. 用户消息或时间变化以带序列号的事件写入 SQLite；并发写入使用 CAS，避免旧 Worker 覆盖新状态。
2. 语义解释器从语言中提取感谢、边界、拒绝、冲突、修复、承诺、偏好和纠错等带来源与置信度的事件。
3. 内稳态引擎更新联结、关怀、好奇、自主、一致性和互动负荷。
4. 动力引擎在模型之外生成回应、主动联系、内部整理和等待等原生意图，并先由内核选定当前唯一意图。
5. 模型只为这个已授权意图生成一份表达草稿；模型自报的收益、关系评分和动作类型不会进入最终决策。
6. 策略层重新比较需求缓解、关系状态、互动负荷、打扰成本和边界，失败或不安全的表达明确回退为等待。
7. 决策事件和 Outbox 动作在同一事务中写入；消息通道领取带租约的动作并使用真实 `action_id` 确认送达后，结果才改变人格状态。

## 已实现

- SQLite WAL 事务事件仓库，事件 ID 唯一约束、序列 CAS 和状态摘要，多进程写入不会制造重复事件或让旧快照覆盖新状态。
- 旧 `events.jsonl` 在首次打开新版运行目录时自动导入。
- 可跨重启保存的名字、特质、价值、表达风格、用户时区和学习参数。
- 六种有界状态；其中互动负荷与联结等促进型需求分开建模。
- 内核原生意图生成。零需求时模型不能靠自评分主动联系，高需求时内核会产生联系机会。
- 证据化关系变化。普通或空消息不会自动增加信任；冲突、感谢、边界和修复产生不同影响。
- 带来源、置信度和确认状态的轻量语义记忆。
- 每积累多次可信证据后才小幅调整需求权重的人格学习。
- 持久 Outbox，支持 `rendered`、`queued`、`sending`、`delivered`、`failed` 和 `cancelled`；领取使用租约，暂停会取消未执行动作。
- 主动联系的安静时间、可配置的 24 小时节奏上限（待发送动作也计入容量），以及未回复后的 72 小时冷却再评估；没有“一生只能主动一次”的限制。
- 送达确认会反馈联结、关怀、好奇和互动负荷；内部笔记不自动回灌为模型记忆，`can_write_memory` 权限也不会被工具白名单绕过。
- Ollama、OpenAI Responses API 和已登录 Codex CLI 三种模型后端。
- 交互式聊天 CLI、后台 Worker、结构化审计和 30/180 天模拟。

当前仍是单用户、单人格、文字通道内核。完整语义/情景记忆检索、实际第三方消息通道、网页界面和多用户隔离尚未实现。

## 安装

需要 Python 3.12 或更高版本。

```bash
python3 -m venv .venv
.venv/bin/python -m pip install -e '.[dev]'
.venv/bin/python -m pytest -q -W error
```

## 对话

本地 Ollama：

```bash
.venv/bin/companion-chat --provider ollama --model '<your-local-model>' \
  --runtime ./runtime \
  --persona-name 'Mira' \
  --persona-trait 'playful' --persona-trait 'direct' \
  --persona-value 'honesty' --persona-value 'curiosity' \
  --persona-style 'short, vivid, and opinionated'
```

后续启动可以省略人格参数，程序会从 `runtime/runtime.db` 恢复原人格。

OpenAI Responses API：

```bash
OPENAI_API_KEY='...' .venv/bin/companion-chat \
  --provider openai_responses --model '<model-id>' --runtime ./runtime
```

复用本机 Codex 登录：

```bash
.venv/bin/companion-chat \
  --provider codex_cli --model 'gpt-5.6-sol' --runtime ./runtime
```

## 后台运行

不需要先开发 GUI。聊天进程和后台 Worker 可以使用同一个运行目录：

```bash
.venv/bin/companion-worker \
  --provider ollama --model '<your-local-model>' \
  --runtime ./runtime --interval-seconds 3600
```

只运行一个后台周期：

```bash
.venv/bin/companion-worker \
  --provider ollama --model '<your-local-model>' \
  --runtime ./runtime --once
```

Worker 只生成并持久化待执行动作，不假装消息已经送达。实际通道通过领取租约避免重复发送，发送消息后再按 `action_id` 确认。

## Python API

```python
from datetime import UTC, datetime
from pathlib import Path

from companion_kernel import AgentRuntime, ConfigStore, KernelEvent, PersonalityKernel
from companion_kernel.clock import SystemClock
from companion_kernel.config import ModelSettings
from companion_kernel.model_backend import create_model_backend
from companion_kernel.types import EventKind

runtime_dir = Path("./runtime")
config = ConfigStore.open(runtime_dir)
backend = create_model_backend(ModelSettings(provider="ollama", model="<model>"))
kernel = PersonalityKernel.open(runtime_dir, SystemClock(), config)
runtime = AgentRuntime(kernel, backend)

event = KernelEvent(
    "message-1",
    datetime.now(UTC),
    EventKind.USER_MESSAGE,
    {"message": "你好"},
)
result = runtime.handle_event(event)

# 消息通道真正展示或送达之后再确认。
if result.response_text is not None and result.action_id is not None:
    runtime.acknowledge_action(
        result.action_id,
        outcome="delivered",
        at=datetime.now(UTC),
    )
```

不存在或伪造的 `action_id` 会被拒绝；重复确认是幂等的。

## 验证

```bash
.venv/bin/python -m pytest -q -W error

# 沉默用户：未回复时降频，但冷却结束后仍可重新评估。
.venv/bin/python -m companion_kernel.simulation \
  --days 30 --seed 7 --reply-every-days 0

# 每日互动的长期场景。
.venv/bin/python -m companion_kernel.simulation \
  --days 180 --seed 42 --reply-every-days 1
```

预期结果：测试全部通过、需求值保持在 `[0, 1]`、边界违规数为 `0`、`pending_actions` 为 `0`，且 `max_proactive_24h` 不超过配置上限。30 天和 180 天只是测试窗口，不参与任何行动限制；系统也没有人格生命周期内的主动消息总次数上限。

## 代码结构

```text
src/companion_kernel/
├── storage.py       # SQLite 事件、配置、语义记忆和 Outbox
├── drives.py        # 需求与互动负荷
├── semantics.py     # 证据化语言解释
├── motivation.py    # 内核原生意图生成
├── relationship.py  # 关系状态与语义事件演化
├── emotions.py      # 情绪与心境
├── policy.py        # 硬边界和最终仲裁
├── agent_runtime.py # 解释、动力、模型、策略和 Outbox 协调
├── worker.py        # 后台主动周期
├── agent_cli.py     # 交互式聊天
├── model_backend.py # 模型协议与后端工厂
├── kernel.py        # 状态归约、重放和审计
└── simulation.py    # 真实运行链路的长期模拟
```

## 设计边界

模型不能修改事件、人格配置、需求、关系状态或 Outbox，也不能决定自己的收益。默认对话权限不包含文件、Shell 或外部服务工具。语义解释器是保守的确定性基线，可替换为更强的独立解释模型，但结果仍需保存来源、置信度和确认状态。

项目没有把“互动时长”“必须留住用户”或“必须发消息”设置为终极奖励。系统必须作出决定，但等待和内部整理都是有效行动。

## 许可证

仓库尚未选择开源许可证。代码可公开查看，但在添加许可证前仍保留全部版权。
