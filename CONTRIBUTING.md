# 参与贡献

感谢你愿意改进表达能力训练器。项目仍处于首个公开版本的准备阶段；优先欢迎能提高训练闭环可靠性、评价可解释性、隐私边界和首次体验的贡献。

## 开始前

1. 阅读 [README](README.md) 和 [工程规范](docs/engineering.md)。
2. 先搜索现有 Issue 和 Pull Request，避免重复工作。
3. 较大的功能或行为改变请先开 Issue 说明问题、预期行为和验收方式；不要把个人偏好直接做成通用规则。
4. 安全漏洞请遵循 [SECURITY.md](SECURITY.md)，不要在公开 Issue 中披露细节。

## 本地开发

项目需要 Python 3.11+、Node.js 20.19+ 或 22.12+，以及 npm。按 [README 的本地启动说明](README.md#本地启动) 启动服务。

提交改动前，请运行与改动范围相符的检查：

```bash
cd backend
pytest -q
```

```bash
cd frontend
npm run lint
npm run build
npm run test:live-filler
npm run test:resampler
```

提交前还应在仓库根目录运行发布门禁：

```bash
python3 scripts/check_release.py
```

如果改动涉及实时训练、报告或 Provider，请在 Pull Request 中写明你实际验证的场景和边界条件。

## 代码与文档约定

- 保持前后端职责分离；接口和数据结构的约定分别放在后端 schema 与前端 `src/types/` 中。
- 不提交 API Key、数据库、上传材料、真实训练音频、个人信息、日志或本地验收截图。
- 评测元数据、复现实验脚本和其来源/许可证说明可以提交；受限音频或个人标注必须先确认再分发权利。
- 使用清晰的中文或英文说明行为变化，避免只描述实现细节。
- Commit 建议使用 `<type>(<scope>): <subject>`，例如 `fix(report): 保存最终转写`。

## Pull Request 要求

- 一个 Pull Request 聚焦一个问题，避免混入无关格式化或重构。
- 更新受影响的测试与文档；没有测试时说明原因和手工验证步骤。
- 使用默认分支的最新代码解决冲突后再请求审查。
- 提交即表示同意你的贡献以本项目的 [MIT License](LICENSE) 提供。

参与社区时请遵守 [行为准则](CODE_OF_CONDUCT.md)。
