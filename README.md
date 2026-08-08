# AI Personification

[中文](README.md) · [English](README_EN.md)

这是一个用 Python 写的长期陪伴运行时。

聊天模型很会说话，却不擅长自己保存几个月的关系变化。今天答应过的事、用户刚刚说过的暂停、一次主动消息没有得到回复，这些信息如果只放在提示词里，很容易在下一轮对话中丢掉。

这个项目把时间、事件、需求、情绪和行为限制放在模型外面。模型负责理解当前语境和组织文字，运行时负责记住发生过什么，以及这次到底能不能行动。

## 先看它怎么工作

![AI Personification system overview](companion-system-architecture.svg)

一条用户消息大致会经过这几步。

1. 消息先变成一个有时间和唯一 ID 的事件。
2. 运行时根据事件更新状态、六种需求和情绪。
3. `AgentRuntime` 把必要的上下文交给模型。模型返回结构化的候选回复或候选动作。
4. `PolicyEngine` 检查安全、权限、主动联系限制和打扰成本，再选出发送、等待、内部记录或不行动。
5. 事件、状态快照和决策结果写入本地日志，之后可以重放和检查。

后台的主动联系也走同一条路径。用户暂停后不会收到主动消息，安静时间和 24 小时频率上限也由策略层执行。一次主动消息没有得到回复时，系统会先等用户回来。

## 为什么要把这些东西放在模型外

提示词能规定说话风格，不能稳定地承担一个长期状态机。下面几件事需要由程序保存和检查。

- 时间过去以后，需求怎样增加或缓解。
- 同一个事件重复到达时，状态只改变一次。
- 高需求不能绕过暂停、安静时间和安全限制。
- 换一个模型以后，事件记录和行为边界仍然在。
- 每次主动行为都能回到对应的事件、候选和策略判断。

代码里有两个清楚的边界。

| 部分 | 做什么 |
| --- | --- |
| `Companion Runtime` | 保存事件、时钟、状态、需求、情绪、权限、策略和审计记录 |
| `ModelBackend` | 调用本地或远程模型，返回结构化候选 |
| 消息适配器 | 接收用户输入，发送已经被策略批准的结果 |

模型可以提出一条问候语，不能直接改需求、改配置或发送消息。

## 现在已经能跑什么

- UTC 时钟、幂等事件日志和状态快照。
- 联结、关怀、好奇、自主、一致性和节律六种需求。
- 基于事件的情绪评价和缓慢的心境变化。
- 暂停、安静时间、主动消息频率上限和未回复锁定。
- 缺少安全评估时的失败关闭行为。
- Ollama 本地模型后端。
- OpenAI Responses API 后端，可以接入 Codex 一类的模型。
- 只有对话权限的默认 Agent 配置。模型提出的文件、Shell 或外部服务请求会被拦下，当前版本不会执行工具。
- 命令行聊天和 180 天模拟。

现在还没有长期语义记忆、网页界面和真实消息投递。`AgentRuntime` 会返回被策略选中的文字，消息要通过单独的通道适配器发送。

## 接入本地模型

先在本机启动 Ollama，准备一个聊天或 instruct 模型。安装项目后可以直接运行命令行聊天。

```bash
python3 -m venv .venv
.venv/bin/python -m pip install -e '.[dev]'
.venv/bin/companion-chat --provider ollama --model '<your-local-model>'
```

模型服务停机、返回坏 JSON 或没有通过安全检查时，运行时会保留事件并选择不行动。

如果要接 Codex 或其他 OpenAI 模型，需要把模型请求发到远程 API。人格状态、日志和策略仍然在本地，模型推理会离开本机。

```bash
OPENAI_API_KEY='...' .venv/bin/companion-chat \\
  --provider openai_responses --model '<openai-codex-model>'
```

需要在 Python 代码里接入时，最小写法如下。

```python
from datetime import UTC, datetime
from pathlib import Path

from companion_kernel import AgentRuntime, KernelEvent, PersonalityKernel, create_model_backend
from companion_kernel.clock import SystemClock
from companion_kernel.config import ConfigStore, ModelSettings
from companion_kernel.types import EventKind

config = ConfigStore.defaults()
backend = create_model_backend(
    ModelSettings(provider="ollama", model="<your-local-model>")
)
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
print(result.response_text)
```

## 快速验证

运行测试。

```bash
.venv/bin/python -m pytest -q -W error
```

运行 180 天模拟。

```bash
.venv/bin/python -m companion_kernel.simulation --days 180 --seed 42
```

模拟器使用临时目录，每次运行互不影响。需求值应保持在 `[0, 1]`，边界违规数应为 `0`。

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

模型输出会先进入结构校验、权限检查和安全评估，再交给策略层。模型不能构造核心事件、改写需求、关闭安全策略，也不能凭自己的一句话获得工具权限。

主动联系还是响应用户，由可信事件类型决定，模型不能自报。系统不会用内疚、威胁、嫉妒、虚假脆弱、自伤暗示或排他性依赖来换取用户回复。

## 后续计划

1. 把安全检查换成更强的独立评估器。
2. 加入用户可控制的事件记忆和语义记忆。
3. 增加消息通道、发送反馈和网页界面。
4. 为需要文件或代码操作的专用 Agent 增加单独的沙箱和审批流程。

## 许可证

仓库目前还没有选择开源许可证。代码已经公开，版权仍归作者所有，直到仓库加入许可证文件。
