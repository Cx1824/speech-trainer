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
- [ ] 确认有权以 MIT License 发布自有代码和示例内容。
- [x] Git 历史作者与提交者邮箱已改写为 GitHub `noreply`，后续本地提交沿用隐私邮箱。
- [ ] 确定 GitHub 仓库所有者与仓库名，并启用私密漏洞报告。
- [ ] 创建私有仓库后先走一遍 clone、安装、启动、测试流程，再决定是否公开。

## 正式公开 `v0.1.0` 前

- [ ] 三个场景各完成一次真实训练并生成报告。
- [x] 补齐每条确定性评分的用户可读计算依据，并同步到网页、HTML 与 PDF。
- [ ] 完成三场景正常/问题表达对比录音验证。
- [x] 实时弱提示支持隐藏与回看，累计信号不受影响。
- [x] 外部 AI 请求具备超时边界，语音连接结束时清理后台任务。
- [x] `CHANGELOG.md` 与 `v0.1.0` Release Notes 草稿已准备。
- [ ] 为公开版本创建 tag、变更说明和校验后的 Release；私测 Key 与私测包不得进入 Release。
