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

打包器会下载并固定校验 CPython 3.12 Windows x64 运行时，同时为 Windows x64
预下载所有 Python wheels，并把两个经过上游 SHA-256 校验的语音模型写入好友
测试包。构建缓存位于 `artifacts/runtime-cache/`，不会加入 Git。ZIP 内的运行时、
依赖和模型文件都会在构建后重新校验。

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

1. 校验并解压包内固定的 CPython 3.12.14 Windows x64 运行时；
2. 仅从包内 wheel 安装后端依赖，不访问 PyPI；
3. 校验包内两个本地语音模型，不访问 GitHub；
4. 在 `127.0.0.1:17860` 启动单进程应用并打开浏览器。

测试电脑不需要预装 Python、winget 或 Node.js，启动过程也不会安装或修改系统
Python。生产前端由 FastAPI 直接托管。启动器只停止自己记录并验证过的包内
Python 进程；端口冲突时不会结束其他项目。
