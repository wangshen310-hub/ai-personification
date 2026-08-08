# AI Personification

[中文](README.md) · [English](README_EN.md)

这是一个用 Python 写的长期陪伴运行时。

模型负责理解消息、写回复。运行时负责保存时间、事件、需求、情绪和行为规则。这样，模型换了，之前的状态和限制仍然保留。

## 怎么工作

![AI Personification system overview](companion-system-architecture.svg)

一条消息会经过下面几步。

1. 消息变成一个带时间和唯一 ID 的事件。
2. 运行时更新状态、六种需求和情绪。
3. 模型返回结构化的回复候选。
4. `PolicyEngine` 检查安全、权限、主动联系限制和打扰成本。
5. 运行时选择发送、等待、内部记录或不行动，并写入日志。

主动联系也走这条流程。暂停、安静时间、24 小时频率上限和未回复锁定都由运行时执行。

## 为什么需要运行时

提示词可以规定语气，不能可靠地保存长期状态。程序需要保证这些事情。

- 同一个事件只处理一次。
- 时间过去后，需求会按规则变化。
- 高需求不能绕过安全限制。
- 每次主动行为都能查到原因。

项目把职责分开。

| 部分 | 作用 |
| --- | --- |
| `Companion Runtime` | 保存事件、状态、需求、情绪、权限、策略和审计记录 |
| `ModelBackend` | 调用本地或远程模型，返回候选回复和动作 |
| 消息适配器 | 发送已经通过策略检查的结果 |

模型可以提出一条问候语，不能直接改状态或发消息。

## 当前能做什么

- 保存事件、状态快照和审计记录。
- 计算联结、关怀、好奇、自主、一致性和节律六种需求。
- 根据事件计算情绪和心境变化。
- 执行暂停、安静时间、频率上限和未回复锁定。
- 接入 Ollama 本地模型。
- 接入 OpenAI Responses API，也可以使用 Codex 一类的模型。
- 运行只有对话权限的 Agent。模型提出的文件、Shell 和外部服务请求会被拦截，不会执行。
- 运行命令行聊天和 180 天模拟。

目前还没有长期语义记忆、网页界面和真实消息投递。`AgentRuntime` 会返回被策略选中的文字，消息发送由单独的通道适配器负责。

## 使用本地模型

先在本机启动 Ollama，再安装项目。

```bash
python3 -m venv .venv
.venv/bin/python -m pip install -e '.[dev]'
.venv/bin/companion-chat --provider ollama --model '<your-local-model>'
```

如果使用 Codex 或其他 OpenAI 模型，模型推理会发送到远程 API，状态、日志和策略仍保存在本地。

```bash
OPENAI_API_KEY='...' .venv/bin/companion-chat \\
  --provider openai_responses --model '<openai-codex-model>'
```

模型服务停机、返回坏 JSON 或没有通过安全检查时，运行时会保留事件并选择不行动。

## 在 Python 中调用

```python
from datetime import UTC, datetime
from pathlib import Path

from companion_kernel import AgentRuntime, KernelEvent, PersonalityKernel, create_model_backend
from companion_kernel.clock import SystemClock
from companion_kernel.config import ConfigStore, ModelSettings
from companion_kernel.types import EventKind

backend = create_model_backend(
    ModelSettings(provider="ollama", model="<your-local-model>")
)
kernel = PersonalityKernel.open(Path("./runtime"), SystemClock(), ConfigStore.defaults())
runtime = AgentRuntime(kernel, backend)

result = runtime.handle_event(
    KernelEvent(
        "message-1",
        datetime.now(UTC),
        EventKind.USER_MESSAGE,
        {"message": "你好"},
    )
)
print(result.response_text)
```

## 快速验证

```bash
.venv/bin/python -m pytest -q -W error
.venv/bin/python -m companion_kernel.simulation --days 180 --seed 42
```

模拟器使用临时目录。需求值应保持在 `[0, 1]`，边界违规数应为 `0`。

## 代码结构

```text
src/companion_kernel/
├── types.py          # 跨模块枚举
├── config.py         # 配置层与写入权限
├── clock.py          # 系统时钟与 FakeClock
├── events.py         # 不可变事件与 JSONL 事件仓库
├── drives.py         # 六种需求的稳态计算
├── emotions.py       # 情绪与心境评价
├── policy.py         # 硬边界与候选评分
├── model_backend.py  # 模型上下文、候选协议和后端工厂
├── ollama_backend.py # Ollama 本地后端
├── openai_backend.py # OpenAI Responses API 后端
├── permissions.py    # Agent 工具权限配置
├── safety.py         # 独立的保守安全检查器
├── agent_runtime.py  # 模型、权限和内核协调器
├── agent_cli.py      # 本地交互式聊天 CLI
├── state.py          # 规范化状态与校验和快照
├── audit.py          # 结构化决策审计
├── kernel.py         # reducer、重放与内核入口
└── simulation.py     # 长期模拟与 CLI
```

## 安全边界

模型输出先经过结构校验、权限检查和安全评估，再交给策略层。模型不能构造核心事件、改写需求、关闭安全策略，也不能自行获得工具权限。

主动联系还是响应用户，由可信事件类型决定。系统不会用内疚、威胁、嫉妒、虚假脆弱、自伤暗示或排他性依赖来换取用户回复。

## 后续计划

1. 增强独立安全评估。
2. 加入用户可控制的事件记忆和语义记忆。
3. 增加消息通道、发送反馈和网页界面。
4. 为文件和代码操作增加沙箱与审批流程。

## 许可证

仓库目前还没有选择开源许可证。代码已经公开，版权仍归作者所有，直到仓库加入许可证文件。
