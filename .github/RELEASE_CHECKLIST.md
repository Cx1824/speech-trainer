# GitHub 草稿与公开发布检查表

## 创建私有草稿仓库前

- [x] `python3 scripts/check_release.py --history` 通过。
- [x] `cd backend && .venv/bin/python -m pytest -q` 通过。
- [x] `cd frontend && npm run lint && npm run build` 通过。
- [x] `cd frontend && npm run test:live-filler && npm run test:resampler` 通过。
- [x] Windows 私测会话的最终启动脚本已合并并通过不含真实 Key 的结构测试。
- [x] Windows 私测包已在 64 位 Windows 环境完成首次启动、模型加载、训练和报告复测。
- [x] `artifacts/`、`.env`、数据库、音频、材料、日志及含临时 Key 的测试包均未纳入候选文件。

## 维护者必须亲自确认

- [x] 已完成公开候选文件、第三方来源和许可证的工程审计，见 `docs/open-source-rights-audit.md`。
- [x] 维护者已于 2026-08-29 确认有权以 MIT License 发布自有代码和示例内容。
- [x] Git 历史作者与提交者邮箱已改写为 GitHub `noreply`，后续本地提交沿用隐私邮箱。
- [x] 已在 GitHub 私有仓库 `Cx1824/speech-trainer` 完成发布演练，随后按维护者确认公开。
- [x] 默认分支为 `main`；已启用私密漏洞报告、依赖关系图、Dependabot 警报与安全更新、密钥保护。
- [x] 已从私有仓库演练分支重新 clone，并完成安装、模型检查、启动、健康检查和全量测试。

## 正式公开 `v0.1.0` 前

- [x] 维护者已确认三个场景均完成真实麦克风训练并检查生成报告。
- [x] 补齐每条确定性评分的用户可读计算依据，并同步到网页、HTML 与 PDF。
- [x] 维护者已确认三场景真实训练与报告检查；正常/问题表达检测另有自动化回归覆盖。
- [x] 实时弱提示支持隐藏与回看，累计信号不受影响。
- [x] 外部 AI 请求具备超时边界，语音连接结束时清理后台任务。
- [x] `CHANGELOG.md` 与 `v0.1.0` Release Notes 草稿已准备。
- [ ] 为公开版本创建 tag、变更说明和校验后的 Release；私测 Key 与私测包不得进入 Release。
