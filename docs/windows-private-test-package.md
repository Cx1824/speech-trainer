# Windows 私人测试包

该分发方式用于小范围、短期好友测试，不用于 GitHub Release。生成的 ZIP 会包含
当前运行配置中的 DeepSeek API Key，因此产物只能通过受控渠道发送，并应在测试
结束后撤销对应 Key。

## 构建

先完成前端生产构建，再运行打包器：

```bash
cd frontend
npm run build
cd ../backend
.venv/bin/python ../scripts/build_windows_test_package.py --skip-frontend-build
```

默认从 macOS 运行版数据目录读取 DeepSeek 配置。也可以显式指定只读配置数据库：

```bash
.venv/bin/python ../scripts/build_windows_test_package.py \
  --config-db "/path/to/speech_trainer.db" \
  --skip-frontend-build
```

产物写入被 Git 忽略的 `artifacts/`：

- `SpeechTrainer-Windows-Test-YYYYMMDD.zip`
- 同名 `.sha256` 校验文件

## 隐私边界

打包器只读取配置表中的 DeepSeek LLM 配置，不复制源数据库。以下内容会被构建后
校验明确拒绝：

- SQLite 数据库；
- `data`、`uploads`、`output` 中的会话、简历、材料和报告；
- 测试、评测、录音、日志、缓存和 Node/Python 开发环境；
- ASR/TTS Key 或 Secret。

DeepSeek Key 只能存在于 ZIP 内的 `backend/.env`。配置 API 仍只返回 `has_key`，
不会返回密钥原文。这个边界防止前端意外展示，并不阻止拿到 ZIP 的本机使用者解压
读取 `.env`；真正结束共享必须在 DeepSeek 控制台撤销该 Key。

## 测试者使用方式

Windows 10/11 64 位用户解压后双击 `启动训练器.cmd`。首次运行会：

1. 检测 Python 3.11/3.12；没有时通过 winget 安装 Python 3.12；
2. 创建包内独立虚拟环境并安装后端依赖；
3. 下载并校验约 300 MB 本地语音模型；
4. 在 `127.0.0.1:17860` 启动单进程应用并打开浏览器。

生产前端由 FastAPI 直接托管，Windows 运行时不需要 Node.js。启动器只停止自己
记录并验证过的虚拟环境进程；端口冲突时不会结束其他项目。
