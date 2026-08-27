# 表达能力训练器

> 本地优先的中文表达训练工具：表达事实分析、实时弱提示、三场景可解释报告。

这个项目关注的不只是“说了什么”，还帮助你看见自己“怎么说”：语速、停顿、口癖、重复、长句和相对个人基线的声音变化。

当前处于 `v0.1.0` 发布准备阶段，适合本地自用和参与开发，尚未作为稳定版本发布。

![首页与示例报告演示](docs/assets/demo.gif)

无需配置 API Key 即可从首页打开固定示例报告；完整训练中的内容评价仍需配置语言模型。

## 核心特点

- **个人声音基线（实验）**：通过朗读记录个人语速、音调和停顿习惯；当前只保存声音事实，不生成紧张或稳定评分。
- **实时弱提示**：说话过程中提示口癖、模糊措辞、连续重复、语速和停顿问题，尽量不打断表达。
- **共享分析核心**：三个场景复用同一套收音、转写、文本和声音分析链路。
- **场景化评价**：面试、工作汇报和演讲使用不同的语义评价维度，而不是简单更换提示词。
- **证据优先**：确定性指标展示用户可读的计算依据；语义评价需要引用训练原话或明确事实。
- **本地数据**：配置、训练记录和报告默认保存在本机 SQLite 数据库中。

## 训练场景

| 场景 | 训练重点 | 主要评价维度 |
| --- | --- | --- |
| 模拟面试 | 自我介绍、项目追问、岗位问题和反问 | 回答结构、岗位匹配、表达流畅度 |
| 工作汇报 | 结论先行、数据支撑和质询应对 | 结论与结构、数据与论据、时间控制 |
| 演讲训练 | 限时表达、节奏和核心观点 | 演讲结构、观点表达、声音与节奏 |

## 工作原理

```text
共享事实层
录音 → 转写 → 个人基线 → 实时提示 → 表达与声音事实 → 本地保存
                                              ↓
场景评价层
面试评价 / 工作汇报评价 / 演讲评价
                                              ↓
场景化报告
```

共享事实和场景评价刻意分开：语速、口癖等事实不随场景改变；这些事实在不同沟通任务中的重要程度和语义评价标准可以不同。

## 技术栈

- 前端：React 18、TypeScript、Vite、Ant Design
- 后端：Python 3.11+、FastAPI、WebSocket、SQLAlchemy
- 存储：SQLite
- AI：ASR、TTS、LLM 均采用可配置 Provider
- 默认字幕：本地 sherpa-onnx 实时初稿 + SenseVoice 句末精校（无需 API Key，阿里云 Paraformer 可选）
- 默认内容分析：DeepSeek Chat Completions，模型 `deepseek-v4-pro`（需要使用者自己的 API Key）

## 本地启动

### 环境要求

- Python 3.11 或更高版本
- Node.js 20.19+ 或 22.12+
- npm

### 1. 启动后端

```bash
cd backend
python3 -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
python scripts/install_local_asr.py
cp .env.example .env
uvicorn app.main:app --host 127.0.0.1 --port 8000
```

### 2. 启动前端

另开一个终端：

```bash
cd frontend
npm ci
npm run dev
```

浏览器打开 <http://localhost:5178>。

后端健康检查应返回 `{"status":"ok", ...}`：

```bash
curl http://127.0.0.1:8000/api/health
```

暂时不配置 AI 服务也可以从首页打开固定示例报告，先了解项目的分析结构；示例中的人物、分数和对话均为演示数据。

完成首次依赖安装后，也可以在项目根目录运行：

```bash
./start.sh
```

### 3. 配置 AI 服务

实时字幕默认使用本地 sherpa-onnx，无需配置密钥。流式 Zipformer 负责说话中
持续更新的字幕，停顿成句后再由 SenseVoice 在本机生成最终文本。首次安装运行
`python scripts/install_local_asr.py`，共下载约 300MB 的两个模型压缩包，解压后
约占 410MB；模型保存在用户数据目录，不会进入 Git 仓库。磁盘空间有限时可加
`--streaming-only` 只安装实时模型，此时系统会自动使用流式结果定稿。需要云端
识别时，也可以在设置页切换到阿里云 Paraformer；原有阿里云能力不会被删除。

内容分析仍需要配置语言模型服务；需要 AI 语音播报时再配置语音合成服务。
可以复制 `backend/.env.example` 后填写，也可以在本地设置页配置。
仓库预填 DeepSeek 官方地址和 `deepseek-v4-pro` 模型名，但不会附带任何 Key；
模型可用范围以 [DeepSeek 官方 API 文档](https://api-docs.deepseek.com/api/create-chat-completion/) 为准。

API Key 不应提交到 Git。项目默认只监听本机回环地址，公开部署不在当前支持范围内。

## 数据与隐私边界

“本地优先”不等于所有功能都完全离线：

- 会话、材料解析结果、声音基线和运行配置默认保存在 `backend/data/`。
- 上传材料默认保存在 `backend/uploads/`。
- 使用默认本地语音识别时，训练音频不会上传；切换云端 ASR、TTS 或 LLM 后，对应的音频、文本或提示内容会发送给所选服务商。
- 项目不会主动提供公网服务；默认后端地址为 `127.0.0.1`。

公开仓库或反馈问题前，请先检查数据库、上传文件、日志和截图中是否包含个人信息。

## 评价边界

声音和文本分析属于训练用启发式指标：

- 表达连贯性、语速节奏和声音状态是三个不同维度，不合并成“紧张度”或“稳定性”。
- 声音快速波动目前只作为实验事实记录，不参与综合评分，也不代表心理状态判断。
- 没有有效发言时长、声音片段或语义评价时，报告会标记数据不足，不补默认分数。
- 三个场景使用不同评价标准，各场景总分不适合直接横向比较。
- 已公开首轮 24 条真人普通话样本和 4 条同文对照的评测方法、来源元数据、派生结果及失败案例；原始音频不随仓库分发，三场景自发口语仍需继续验证。

## 已知局限

- 当前发布边界是单机本地使用，不包含公网部署、多用户认证或云端数据隔离方案。
- 默认本地识别针对普通话场景；首次使用需另行下载约 300MB 模型，仓库和 Release 不包含模型权重。
- 内容结构、场景评价和生成式建议依赖外部 LLM；没有 Key 时只能体验固定示例报告和无需内容模型的本地能力。
- 声音、节奏和文本指标是训练用启发式信号，不用于判断紧张、自信、性格、心理或医疗状态。
- 三场景核心链路已有自动化覆盖，但真实自发口语对比样本和干净 Windows 环境发布演练仍需补齐。

## 开发与验证

后端测试：

```bash
cd backend
pytest -q
```

前端静态检查与生产构建：

```bash
cd frontend
npm run lint
npm run build
npm run test:live-filler
npm run test:resampler
```

依赖与发布候选检查：

```bash
cd backend
.venv/bin/python -m pip_audit
cd ../frontend
npm audit --omit=dev
cd ..
python3 scripts/check_release.py --history
```

详细的一阶段范围和发布门槛见 [docs/phase-1-open-source-readiness.md](docs/phase-1-open-source-readiness.md)，GitHub 草稿与公开发布步骤见 [.github/RELEASE_CHECKLIST.md](.github/RELEASE_CHECKLIST.md) 和 [GitHub 上线执行单](docs/github-launch.md)。

## 贡献与安全

欢迎针对训练闭环可靠性、评价可解释性、隐私边界和首次体验的改进。提交改动前请阅读 [贡献指南](CONTRIBUTING.md) 和 [工程规范](docs/engineering.md)，并运行对应的后端测试、前端 lint 与生产构建。

安全问题、密钥或个人训练数据不得提交到公开 Issue。请遵循 [安全政策](SECURITY.md) 通过私下渠道联系维护者。

所有参与者均应遵守 [社区行为准则](CODE_OF_CONDUCT.md)。

## 项目结构

```text
.
├── backend/
│   ├── app/
│   │   ├── api/          # HTTP 与 WebSocket 入口
│   │   ├── modules/      # 分析、场景、报告与训练会话
│   │   └── providers/    # ASR / TTS / LLM 适配
│   ├── evals/            # 可复现评测工具与结果
│   └── tests/            # 自动化测试
├── frontend/
│   └── src/              # React 页面、组件与语音会话逻辑
├── docs/                 # 工程与发布文档
├── PRD.md
└── start.sh
```

## 当前路线

1. 扩充面试、汇报和演讲自发口语对比录音，继续校准连贯性与节奏标签。
2. 在私有 GitHub 仓库完成 macOS/Linux/Windows 的干净安装与发布演练。
3. 根据首批真实使用反馈校准提示频率、面试覆盖和报告建议。
4. 准备公开仓库的社交预览图、短演示片和首发传播素材。

## 许可证

本项目以 [MIT License](LICENSE) 发布。第三方组件说明见
[THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md)。仓库不分发本地 ASR 模型权重；
评测元数据、音频来源和第三方素材仍需分别遵守其注明的许可证与使用条件。
不要将未经授权的音频、材料或个人数据提交到仓库。

## English summary

Speech Trainer is a local-first Chinese communication coach with personal acoustic calibration, low-distraction real-time feedback, and explainable reports for interviews, work presentations, and public speaking. The project is preparing for its first open-source release and is not yet a stable distribution.
