# 工程规范

## 核心原则

1. **模块化**：功能内聚，目录即模块边界，禁止跨模块 import 内部文件
2. **类型先行**：所有共享数据结构定义在 `shared/types`，前后端共用
3. **单一职责**：每个文件/类/函数只做一件事
4. **最小依赖**：不引入未使用的库，能用标准库解决的不装三方包
5. **配置外部化**：环境变量、用户配置不硬编码

## 目录规范

### 前端 (`frontend/src/`)

```
src/
├── pages/            # 页面级组件（按路由划分）
│   ├── Home/
│   ├── Interview/
│   ├── Report/
│   └── Settings/
├── components/       # 可复用 UI 组件（跨页面通用）
│   ├── Danmu/
│   ├── EmotionIndicator/
│   └── Layout/
├── modules/          # 业务模块（封装完整业务逻辑）
│   ├── interview/    # 面试会话管理
│   ├── audio/        # 音频采集与处理
│   └── realtime/     # 实时分析
├── services/         # 后端 API 调用封装
├── hooks/            # 自定义 React Hooks
├── store/            # 全局状态（Zustand）
├── types/            # 前端专用类型（不与后端共享的）
├── utils/            # 工具函数
└── styles/           # 全局样式、主题变量
```

**规则**：
- `pages/` 只组合 `components/` 和 `modules/`，不直接写业务逻辑
- `modules/` 每个模块独立，对外只暴露 `index.ts`
- `components/` 必须可复用（至少被 2 处调用才算合格）
- `services/` 只负责 HTTP/WS 通信，不含业务逻辑

### 后端 (`backend/app/`)

```
app/
├── main.py               # FastAPI 入口
├── config.py             # 配置加载
├── core/                 # 核心基础设施
│   ├── logging.py
│   ├── database.py
│   └── exceptions.py
├── api/                  # HTTP 路由层
│   ├── deps.py
│   └── v1/
│       ├── config.py
│       ├── interview.py
│       └── report.py
├── modules/              # 业务模块
│   ├── interview/        # 面试会话
│   │   ├── manager.py
│   │   ├── state_machine.py
│   │   └── prompts.py
│   ├── resume/           # 简历解析
│   ├── question_bank/    # 题库
│   ├── analysis/         # 表达分析
│   │   ├── text_rules.py
│   │   ├── voice_features.py
│   │   └── emotion.py
│   └── report/           # 报告生成
├── providers/            # AI 能力 Adapter 层
│   ├── base.py
│   ├── llm/
│   ├── tts/
│   └── asr/
├── models/               # 数据模型（SQLAlchemy）
├── schemas/              # Pydantic schemas
└── utils/
```

**规则**：
- `api/` 只做参数校验 + 调用 modules，不含业务逻辑
- `modules/` 每个模块独立，对外只暴露 `__init__.py` 中声明的接口
- `providers/` 只负责 AI 能力调用，不知道业务上下文
- `models/` 只定义数据结构，不含行为

## 命名规范

- **文件/目录**：`lower_snake_case`（Python）、`lower_snake_case` 或 `PascalCase` 文件夹（前端，按组件名）
- **类**：`PascalCase`
- **函数/变量**：`camelCase`（前端）/ `snake_case`（Python）
- **常量**：`UPPER_SNAKE_CASE`
- **类型**：`PascalCase`

## 类型规范

- 共享类型放在 `shared/types/`，前端用 `.ts`、后端用 `.py`（通过脚本同步）
- 后端 Pydantic schema 与 SQLAlchemy model 分离
- 前端 API 返回类型必须显式声明，禁止 `any`

## 错误处理

- **前端**：网络错误统一在 `services/` 层捕获并转换为友好提示
- **后端**：
  - 业务异常抛 `app.core.exceptions.<Specific>Error`
  - 全局 exception handler 统一转换为 HTTP 响应
  - 不暴露堆栈信息给前端（开发环境除外）

## 日志规范

- 前端：关键操作用 `console.info/warn`，错误用 `console.error`，调试用 `console.debug`
- 后端：使用 `logging`，格式 `%(asctime)s [%(levelname)s] %(name)s: %(message)s`
  - INFO：业务关键节点（会话开始/结束、API 调用）
  - WARN：可恢复异常（重试、降级）
  - ERROR：不可恢复错误（需关注）

## 测试规范（MVP 阶段轻量）

- 后端核心逻辑（state machine、analysis）写单元测试
- 前端 MVP 阶段不强制测试，但关键 hook 鼓励写
- 测试文件与源文件同目录，命名 `*.test.ts` / `test_*.py`

## Git 规范

- 分支：`main`（稳定）+ `dev`（开发）
- Commit 格式：`<type>(<scope>): <subject>`
  - type：feat / fix / refactor / docs / chore / test
  - scope：模块名
  - 例：`feat(interview): 实现状态机`、`fix(danmu): 修复轨道重叠`

## 性能预算

- 前端首屏 ≤ 2s（本地）
- WebSocket 消息延迟 ≤ 100ms
- ASR 文字回流 ≤ 500ms
- 弹幕渲染 60fps（200 条以下同屏）

## 安全规范

- API 密钥只存在后端 .env，前端永远不直接调用第三方 AI API
- 用户上传文件先校验类型与大小，再保存
- 所有外部输入走 Pydantic 校验
