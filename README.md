# AI Personification

[中文](README.md) · [English](README_EN.md)

一个让 AI 具备长期连续人格的实验性项目。

它不把“人格”只写在一段提示词里，而是用一个独立的运行时保存状态、计算需求、
产生情绪、检查边界，再让语言模型负责理解情境和组织表达。

## 这是什么

我们希望构建的不是一个“每次聊天都重新开始”的机器人，而是一个能够长期陪伴的系统：

- 它能记住关系中的连续变化，而不是只记住几句对话；
- 它有类似“联结、关怀、好奇、自主、一致性和节律”的内部需求；
- 需求会积累、缓解和冲突，并影响它是否应该行动；
- 它可以表达情绪，但不能用情绪操控用户；
- 它可以主动联系，但必须尊重暂停、安静时间、拒绝和未回复；
- 更换模型供应商时，核心状态和行为边界仍然保持稳定。

## 为什么需要独立运行时

提示词适合描述风格，却不适合承担长期状态机。单靠提示词很难可靠地保证：

- 时间经过后状态会怎样变化；
- 同一个事件重复到达时不会重复产生影响；
- 高需求不会绕过安全边界；
- 模型更换后人格不会完全漂移；
- 每次主动行为都能解释和追溯。

因此，本项目把职责拆开：

| 部分 | 负责什么 |
| --- | --- |
| `Companion Runtime` | 事件、时钟、需求、情绪、状态、策略、安全、审计和重放 |
| `ModelBackend` | 调用远程模型 API 或本地模型，理解情境并提出候选意图 |
| 消息适配器 | 接收用户消息，发送已经通过策略批准的行动 |

模型可以提出“我想问候用户”这样的候选，但不能直接修改状态或发送消息。

## 工作方式

![AI Personification system overview](companion-system-architecture.svg)

一次用户消息或后台时间事件大致经过以下步骤：

1. 输入被转换成不可变、可去重的事件；
2. 运行时推进虚拟时钟，更新六种需求和情绪状态；
3. 系统整理必要的上下文，再调用模型生成一个或多个结构化候选；
4. 策略层先检查硬边界，再比较需求缓解、关系健康、风险和打扰成本；
5. 系统选择发送、内部记录、等待或拒绝，并把结果写入事件日志和审计日志。

后台主动联系遵循同一条路径。没有显著需求时，系统可以什么也不做；用户未回复一次主动消息后，
主动联系会锁定，直到用户再次回应。

## 当前版本已经实现

- UTC 虚拟时钟和幂等事件日志；
- 六种有界需求与持续缺口计算；
- 确定性情绪评价和缓慢心境变化；
- 系统、用户、学习人格三层配置权限；
- 暂停、安静时间、24 小时频率上限和未回复锁定；
- 安全信号缺失时失败关闭；
- 校验和快照、事件重放和结构化决策审计；
- 30/180 天模拟以及 100 组随机不变量测试。

当前版本已经提供 Ollama 本地后端、OpenAI Responses API 后端和 Agent 协调器，但还不实现长期语义记忆、用户界面或真实消息投递；这些功能会通过独立适配器加入。

## 接入模型 Agent

模型是候选生成器，不是人格内核。`AgentRuntime` 会先构造有限上下文，再调用模型生成结构化候选，最后交给
`PolicyEngine` 决定是否发送。默认 `DIALOGUE_PERMISSIONS` 没有任何工具权限；模型返回的文件、Shell 或外部服务请求只会被记录为阻断请求，不会执行。

完全本地的最小接入方式如下。先在本机启动 Ollama，并准备一个聊天/instruct 模型：

```python
from datetime import UTC, datetime
from pathlib import Path

from companion_kernel import AgentRuntime, KernelEvent, PersonalityKernel, create_model_backend
from companion_kernel.clock import SystemClock
from companion_kernel.config import ConfigStore, ModelSettings
from companion_kernel.types import EventKind

config = ConfigStore.defaults()
settings = ModelSettings(provider="ollama", model="<your-local-model>")
backend = create_model_backend(settings)
kernel = PersonalityKernel.open(Path("./runtime"), SystemClock(), config)
runtime = AgentRuntime(kernel, backend)

result = runtime.handle_event(
    KernelEvent(
        "message-1",
        datetime.now(UTC),
        EventKind.USER_MESSAGE,
        {"message": "你好"},
    )
)
print(result.response_text)  # 只有策略选中 SEND_MESSAGE 时才有值
```

如果使用 Codex 等 OpenAI 模型，将后端改为：

```python
settings = ModelSettings(
    provider="openai_responses",
    model="<openai-codex-model>",
    base_url="https://api.openai.com/v1",
)
```

这种方式仍然让人格状态和策略留在本地，但模型请求会发送到远程 API；要做到完全离线，请使用 Ollama 后端。
模型服务不可用、输出格式错误或安全检查失败时，运行时会提交事件并降级为不行动。

## 快速运行

需要 Python 3.12 或更高版本。

```bash
python3 -m venv .venv
.venv/bin/python -m pip install -e '.[dev]'
.venv/bin/python -m pytest -q -W error
```

运行 180 天模拟：

```bash
.venv/bin/python -m companion_kernel.simulation --days 180 --seed 42
```

启动本地模型对话（以 Ollama 为例）：

```bash
.venv/bin/companion-chat --provider ollama --model '<your-local-model>'
```

如使用 Codex 等 OpenAI 模型：

```bash
OPENAI_API_KEY='...' .venv/bin/companion-chat \\
  --provider openai_responses --model '<openai-codex-model>'
```

模拟器默认使用临时目录，因此可以重复运行。正常结果应满足：需求值在 `[0, 1]` 内，边界违规为 `0`。

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
├── model_backend.py  # 模型上下文、候选协议和后端工厂
├── ollama_backend.py # Ollama 本地后端
├── openai_backend.py # OpenAI Responses API 后端
├── permissions.py # Agent 工具权限配置
├── safety.py      # 独立的保守安全检查器
├── agent_runtime.py # 模型、权限和内核协调器
├── agent_cli.py   # 本地交互式聊天 CLI
├── state.py       # 规范化状态与校验和快照
├── audit.py       # 结构化决策审计
├── kernel.py      # reducer、重放与内核入口
└── simulation.py  # 长期模拟与 CLI
```

## 安全边界

模型输出是不可信的候选输入，必须经过运行时策略闸门。模型不能直接构造核心事件、改写需求、
关闭安全策略或获得外部工具权限。主动/响应模式由可信事件类型决定，不能由模型自报。

系统禁止用内疚、威胁、嫉妒、虚假脆弱、自伤暗示或排他性依赖来换取用户回复。

## 后续计划

1. 增加更强的安全评估、长期语义记忆和模型路由；
2. 增加可由用户控制的语义记忆和事件记忆；
3. 增加后台反思、消息生成和发送反馈适配器；
4. 增加 Web/App/IM 通道和可视化审计界面。

## 许可证

当前仓库尚未选择开源许可证。代码已经公开，但在加入许可证文件前，版权仍归作者所有。
