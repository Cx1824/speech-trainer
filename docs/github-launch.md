# GitHub 上线执行单

## 仓库展示文案

**Description**

> 本地优先的中文表达训练器：实时看见口头禅、重复与节奏，覆盖面试、工作汇报和演讲复盘。

**Topics**

`speech-recognition`、`speech-coach`、`chinese`、`interview`、
`public-speaking`、`local-first`、`fastapi`、`react`、`deepseek`

**首发标题**

> v0.1.0 — 把口头禅、重复和节奏变成实时可见的训练信号

Release 正文直接使用 [v0.1.0 草稿](../.github/RELEASE_NOTES_v0.1.0.md)。

## 转公开前的 GitHub 设置

1. 先创建私有仓库，推送候选提交并等待 CI 全绿。
2. 在私有仓库执行一次全新 clone、安装、启动和固定示例报告检查。
3. 在 **Settings → Security → Private vulnerability reporting** 启用私密漏洞报告。
4. 设置上方 Description、Topics 和项目主页（如后续有独立主页）。
5. 上传 1280×640 社交预览图；不得使用含个人数据、未授权素材或平台生成标识被移除的画面。
6. 确认 Issues 可用，Discussion 仅在确实准备维护社区时开启。
7. 完成维护者权利确认和真人录音门槛后，再把仓库改为 Public。

## 正式发布顺序

```bash
git status --short
python3 scripts/check_release.py --history
cd backend && .venv/bin/python -m pytest -q && .venv/bin/python -m pip_audit
cd ../frontend && npm ci && npm run lint && npm run build
npm run test:live-filler && npm run test:resampler && npm audit --omit=dev
cd ..
```

全部通过并确认候选提交后，再创建 `v0.1.0` tag 与 GitHub Release。公开 Release
只发布源代码和必要文档，不上传 Windows 好友私测包、本地模型、API Key、数据库、
录音、简历、报告或日志。

## 首发后 72 小时

- 在 README 顶部保持 5～10 秒演示 GIF，30 秒完整视频放在 Release 或公开平台。
- 首日集中回复安装与体验问题，把高频问题补进 README，而不是只在 Issue 中回答。
- 对无法复现的问题先索要脱敏环境信息；不要让使用者上传完整数据库或训练录音。
- 只承诺已经有复现或验收依据的能力，尤其避免宣传心理、情绪或真实能力判断。
