# AI Personification

一个面向长期陪伴型 AI 的确定性人格运行时（deterministic personality runtime）。
本项目把持续动力、情绪、边界、主动性和审计放在模型之外，由独立软件维护；
LLM 或本地模型只负责情境理解、候选意图和语言表达。

**状态：** v0.1 核心内核已实现并通过 36 项测试。LLM 接入、长期语义记忆、UI
和真实消息发送属于后续模块。

## 原理图

[打开学术简洁风格部署原理图（SVG）](companion-system-architecture.svg)

![AI Personification runtime–model architecture](companion-system-architecture.svg)

## 核心思想

不要把“人格”只写成一段 prompt。Prompt 可以描述风格，但不能可靠地维护跨时间的
状态、因果、权限和安全边界。本项目将系统拆为两层：

| 层 | 职责 |
| --- | --- |
| `Companion Runtime` 独立运行时 | 事件、虚拟时钟、六种需求、情绪、状态、策略、安全、审计和重放 |
| `ModelAdapter` 模型适配层 | 调用远程模型 API 或本地模型，返回结构化候选意图与表达草稿 |

模型没有直接写数据库、修改需求值或发送消息的权限。模型输出必须先经过确定性策略闸门。

## 运行流程

```text
用户消息 / 定时器
        ↓
事件规范化与虚拟时钟
        ↓
需求、情绪、记忆和关系状态更新
        ↓
组织脱敏上下文，调用远程 API 或本地模型
        ↓
模型返回 CandidateIntent
        ↓
硬边界检查 → 需求缓解/关系健康/打扰成本评分
        ↓
发送、内部记录、等待或拒绝
        ↓
写回事件日志、快照和决策审计
```

## 已实现能力

- 事件日志、幂等事件处理和 UTC 虚拟时钟
- 六种有界需求：联结、关怀、好奇、自主、一致性、节律/负荷
- 确定性情绪评价和缓慢心境移动平均
- 配置权限：系统策略、用户设置、学习人格分层
- 硬边界优先：暂停、安静时间、频率上限、未回复锁定、安全不确定即拒绝
- 校验和快照、事件重放和结构化决策审计
- 30/180 天长期模拟与 100 组随机不变量扫描

## 安全与信任边界

`PersonalityKernel.process()` 只接受宿主认证并规范化后的事件。模型文本不能直接构造
`KernelEvent`；模型建议只能映射为 `CandidateIntent`，并继续经过策略闸门。

主动/响应模式由可信事件类型决定，不能由候选意图自报。安全评估缺失、超时或不确定时，
候选默认失败关闭。当前版本不实现外部工具操作、真实消息投递或自然语言安全分类。

## 快速开始

需要 Python 3.12 或更高版本。

```bash
python3 -m venv .venv
.venv/bin/python -m pip install -e '.[dev]'
.venv/bin/python -m pytest -q -W error
```

运行长期模拟：

```bash
.venv/bin/python -m companion_kernel.simulation --days 180 --seed 42
```

模拟器默认使用临时运行目录，因此可以重复执行。结果应保持需求值在 `[0, 1]`，且边界违规为 `0`。

## 代码结构

```text
src/companion_kernel/
├── types.py       # 跨模块枚举
├── config.py      # 配置层与写入权限
├── clock.py       # 系统时钟与 FakeClock
├── events.py      # 不可变事件与 JSONL 事件仓库
├── drives.py      # 六需求内稳态引擎
├── emotions.py    # 情绪与心境评价
├── policy.py      # 硬边界与候选评分
├── state.py       # 规范化状态与校验和快照
├── audit.py       # 结构化决策审计
├── kernel.py      # reducer、重放与内核入口
└── simulation.py  # 长期模拟与 CLI
```

## 后续模块

1. `ModelAdapter`：为远程 API、本地模型和混合路由定义统一接口。
2. 记忆服务：语义记忆、事件记忆、用户控制和冲突确认。
3. 后台反思与主动联系：低频调度、消息生成和发送反馈适配器。
4. Web/App/IM 通道与可视化审计界面。

## 许可证

当前仓库尚未选择开源许可证。代码可以公开查看，但在添加许可证文件前，版权仍归作者所有。

---

# English

AI Personification is a deterministic personality runtime for long-term companion AI.
It keeps persistent drives, affect, boundaries, proactive behavior, and auditability
outside the language model. A remote LLM API or a local model is used for contextual
reasoning, candidate intentions, and language generation.

**Status:** v0.1 of the deterministic kernel is implemented and validated by 36 tests.
LLM integration, semantic memory, user interfaces, and real message delivery are planned
as separate modules.

## Architecture diagram

[Open the academic-style runtime–model diagram (SVG)](companion-system-architecture.svg)

![AI Personification runtime–model architecture](companion-system-architecture.svg)

## Design principle

Personality should not be reduced to a prompt. A prompt can describe style, but it cannot
reliably maintain state, causality, permissions, and safety across time. The system is
split into two layers:

| Layer | Responsibility |
| --- | --- |
| `Companion Runtime` | Events, virtual time, six drives, affect, state, policy, safety, audit, and replay |
| `ModelAdapter` | Calls a remote model API or local model and returns structured candidate intents and drafts |

The model cannot write the state store, change drive values, or send messages directly.
Every model proposal passes through the deterministic policy gate.

## Execution flow

```text
User message / scheduler tick
          ↓
Event normalization and virtual clock
          ↓
Update drives, affect, memory, and relationship state
          ↓
Build a bounded context and call a remote or local model
          ↓
Model returns CandidateIntent
          ↓
Hard-boundary checks → relief/relationship/intrusion scoring
          ↓
Send, write an internal note, wait, or reject
          ↓
Append event, snapshot, and decision audit
```

## Implemented

- Append-only events, idempotency, and a UTC fake clock
- Six bounded drives: connection, care, curiosity, autonomy, coherence, and rhythm/load
- Deterministic emotion appraisal and slow mood moving average
- Layered configuration authority for system, user, and learned persona settings
- Hard-boundary-first policy: pause, quiet hours, rate limit, unanswered-message lock, and fail-closed safety
- Checksummed snapshots, event replay, structured decision audit, and long-run simulation
- 30/180-day simulations plus a 100-seed invariant sweep

## Safety and trust boundary

`PersonalityKernel.process()` accepts only host-authenticated, normalized events. Model text
must never construct `KernelEvent`; model suggestions may only be mapped to `CandidateIntent`
and still pass through the policy gate.

Proactive-versus-reactive mode is derived from the trusted event kind, not self-declared by
the candidate. Missing, timed-out, or uncertain safety assessments fail closed. This version
does not implement external tool actions, real message delivery, or natural-language safety
classification.

## Quick start

Python 3.12 or newer is required.

```bash
python3 -m venv .venv
.venv/bin/python -m pip install -e '.[dev]'
.venv/bin/python -m pytest -q -W error
```

Run a longitudinal simulation:

```bash
.venv/bin/python -m companion_kernel.simulation --days 180 --seed 42
```

The simulator uses a temporary runtime directory by default, so repeated runs are isolated.
Drive values should remain in `[0, 1]` and boundary violations should remain `0`.

## Repository layout

```text
src/companion_kernel/
├── types.py       # Shared enums
├── config.py      # Configuration layers and write authority
├── clock.py       # System clock and FakeClock
├── events.py      # Immutable events and JSONL event store
├── drives.py      # Six-drive homeostasis engine
├── emotions.py    # Emotion and mood appraisal
├── policy.py      # Hard boundaries and candidate scoring
├── state.py       # Canonical state and checksummed snapshots
├── audit.py       # Structured decision audit
├── kernel.py      # Reducer, replay, and runtime entry point
└── simulation.py  # Longitudinal simulator and CLI
```

## Roadmap

1. `ModelAdapter`: one interface for remote APIs, local models, and hybrid routing.
2. Memory service: semantic/episodic memory, user controls, and conflict confirmation.
3. Background reflection and proactive contact with delivery feedback.
4. Web, mobile, and messaging channels plus an audit UI.

## License

No open-source license has been selected yet. The source is publicly readable, but copyright
remains with the author until a license file is added.
