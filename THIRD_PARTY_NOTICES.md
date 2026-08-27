# Third-party notices

Speech Trainer 自身采用 MIT License。以下条目说明项目直接接入、但不改变
Speech Trainer 自有代码许可证的第三方组件。

公开 GitHub 仓库只包含源代码和项目自制演示素材，不包含第三方依赖包、模型
权重、评测原始音频、Windows 私测运行时或宣传片成片。发布范围与维护者确认项见
`docs/open-source-rights-audit.md`。

## CPython Windows runtime

- Project: <https://www.python.org/>
- Distribution source: <https://github.com/astral-sh/python-build-standalone>
- Version: CPython 3.12.14 (`python-build-standalone` release `20260814`)
- License: Python Software Foundation License Version 2
- Usage: Windows 私测包的独立 Python x64 运行时

Windows 私测包保留运行时内的 `LICENSE.txt`。该运行时仅用于让测试包在未安装
Python 的电脑上运行，不改变 Speech Trainer 源代码的 MIT 许可。

## sherpa-onnx

- Project: <https://github.com/k2-fsa/sherpa-onnx>
- Version: 1.13.6
- License: Apache License 2.0
- Usage: 本地实时语音识别推理运行时

项目保留 sherpa-onnx 的原始版权与许可证声明，不使用其名称暗示官方背书。

## Chinese streaming Zipformer model

- Model: `sherpa-onnx-streaming-zipformer-zh-int8-2025-06-30`
- Source: <https://github.com/k2-fsa/sherpa-onnx/releases/tag/asr-models>
- Upstream checkpoint: <https://huggingface.co/yuekai/icefall-asr-multi-zh-hans-zipformer-large>
- Usage: 中文实时字幕

该模型权重不包含在 Git 仓库或公开 Release 中。Windows 私人好友测试包为避免
国内网络下载失败，可包含从上游取得并校验 SHA-256 的模型文件及其原始说明；
使用或再分发模型权重时，应另外核对上游模型页面与发布包中的适用条款。

## SenseVoiceSmall final-transcript model

- Model: `sherpa-onnx-sense-voice-zh-en-ja-ko-yue-int8-2024-07-17`
- Converted package: <https://github.com/k2-fsa/sherpa-onnx/releases/tag/asr-models>
- Upstream project: <https://github.com/FunAudioLLM/SenseVoice>
- Usage: 本地句末字幕精校

该模型权重同样不包含在 Git 仓库或公开 Release 中；Windows 私人好友测试包可
包含经过上游 SHA-256 校验的模型文件及其许可说明。使用或分发时应保留
SenseVoice/FunASR 名称与来源归属，并遵守上游对模型权重适用的模型许可条款；
运行时代码的 Apache-2.0 许可不能替代模型权重自身的条款。

## FLEURS evaluation metadata

- Dataset: Google FLEURS (`cmn_hans_cn`)
- Homepage: <https://huggingface.co/datasets/google/fleurs>
- License: Creative Commons Attribution 4.0 International (CC BY 4.0)
- License text: <https://creativecommons.org/licenses/by/4.0/>
- Usage: 中文普通话语音识别与表达事实评测的样本元数据、转写和派生结果

仓库不分发 FLEURS 原始音频。`backend/evals/web_audio_manifest.json` 记录样本来源、
许可证和获取日期；运行评测时由使用者另行获取音频，音频目录已被 Git 忽略。
仓库中的样本筛选、文本规范化、人工复核标注和派生指标属于对上游数据的整理或
分析，不改变原始数据的 CC BY 4.0 归属要求。
