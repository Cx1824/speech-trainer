## 说明

<!-- 说明使用者可观察到的行为变化和动机，而不只描述实现。 -->

## 验证

<!-- 勾选已实际执行的项目；未执行请说明原因。 -->

- [ ] `cd backend && pytest -q`
- [ ] `cd frontend && npm run lint`
- [ ] `cd frontend && npm run build`
- [ ] `cd frontend && npm run test:live-filler && npm run test:resampler`
- [ ] `python3 scripts/check_release.py`
- [ ] 手工验证：<!-- 场景、步骤、结果 -->

## 数据与隐私

- [ ] 本 PR 不含 API Key、数据库、上传材料、真实音频、个人信息或未获授权的素材。
- [ ] 如涉及 Provider、音频、文本或报告，我已说明数据流和可见边界。

## 提交前检查

- [ ] 相关文档、类型与测试已更新，或已说明无需更新的原因。
- [ ] 改动聚焦单一问题，没有混入无关格式化或重构。
- [ ] 我已阅读并遵守 [贡献指南](../CONTRIBUTING.md) 与 [行为准则](../CODE_OF_CONDUCT.md)。
