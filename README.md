# 表达能力训练平台

> 基于 PRD v0.3 的实现项目 —— 面试训练场景（MVP）

## 技术栈

- **前端**：React 18 + Vite + TypeScript + Ant Design
- **后端**：Python 3.13 + FastAPI + WebSocket
- **存储**：SQLite（MVP 单机）
- **AI**：可配置（ASR / TTS / LLM 均支持多厂商）

## 项目结构

```
speech-trainer/
├── frontend/          # React 前端
├── backend/           # FastAPI 后端
├── shared/            # 前后端共享的类型定义与常量
├── docs/              # 设计文档
└── README.md
```

## 快速启动

### 后端

```bash
cd backend
python -m venv .venv
source .venv/bin/activate
pip install -e .
cp .env.example .env   # 填入 API 密钥
uvicorn app.main:app --reload --host 0.0.0.0 --port 8000
```

### 前端

```bash
cd frontend
npm install
npm run dev            # 默认端口 5178
```

打开 http://localhost:5178

## 开发规范

- 严格模块化：每个功能模块独立目录，禁止跨模块直接 import 内部实现
- 类型先行：所有数据结构定义在 `shared/types`，前后端共享
- 配置外部化：所有可变参数走 .env / 设置页，不硬编码
- 详见 [工程规范](./docs/engineering.md)
