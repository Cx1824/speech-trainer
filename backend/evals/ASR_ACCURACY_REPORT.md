# Local ASR accuracy comparison — 2026-08-24

## Decision summary

- Keep the existing streaming Zipformer for low-latency partial subtitles.
- SenseVoice with inverse text normalization is the only tested offline candidate
  that clears the accuracy gate for a final-transcript experiment.
- Do not adopt the tested offline Zipformer CTC: its gain is too small to justify
  a second model.
- Fix the browser resampler independently. Its clean-audio regression is small but
  measurable, and filtered resampling is more robust to out-of-band noise.

No production provider or selected ASR configuration was changed by this test.

## Corpus and metric

- Primary set: 24 FLEURS Mandarin recordings, 253.64 seconds total.
- Robustness set: the primary set plus four same-text/different-voice controls.
- Metric: micro-averaged character error rate (CER).
- Text normalization: Unicode NFKC, lowercase Latin letters, ignore whitespace and
  punctuation, retain Chinese characters, Latin letters, and digits.
- Limitation: this is read speech. It does not replace spontaneous interview,
  presentation, accent, or real microphone evaluation.

## Model comparison

| Recognizer | 24-sample CER | 28-sample CER | Warm RTF | Model file |
| --- | ---: | ---: | ---: | ---: |
| Current streaming Zipformer | 17.37% | 21.12% | 0.095 | ~167 MB package |
| SenseVoice, no ITN | 14.20% | 16.47% | 0.015 | ~241 MB |
| SenseVoice, ITN enabled | **13.26%** | **16.18%** | 0.015 | ~241 MB |
| Offline Zipformer CTC | 16.31% | 20.93% | 0.021 | ~353 MB |

The earlier Aliyun Paraformer run on the same 24-sample primary set measured
14.55% CER. SenseVoice ITN is 1.29 percentage points lower on this corpus; that
does not establish superiority on spontaneous speech.

SenseVoice ITN reduced total primary-set edits from 148 to 113. It regressed on
only one of the 24 recordings, by one edit. Its largest gains came from numeric
expressions, while foreign names remained a shared weakness.

## Browser resampling test

The current worklet converts 48 kHz blocks to 16 kHz with block-local linear
interpolation. The replacement condition uses filtered polyphase resampling.
The 20 dB high-frequency-noise condition is a diagnostic stress test above the
target 8 kHz Nyquist frequency, not an estimate of normal microphone noise.

| Input path | Streaming CER | SenseVoice ITN CER |
| --- | ---: | ---: |
| Direct 16 kHz | 17.37% | 13.26% |
| Current worklet, clean | 17.72% | 13.03% |
| Filtered resampler, clean | 17.37% | 13.26% |
| Current worklet, HF noise | 18.90% | 13.73% |
| Filtered resampler, HF noise | 17.72% | 13.38% |

The clean SenseVoice change of -0.23 percentage points is a two-edit fluctuation,
not evidence that the current resampler helps. For the streaming model, filtered
resampling restored the direct-input baseline and recovered 10 of the 13 extra
edits introduced by the stress condition.

## Runtime footprint

- Streaming model cold load: 1.417 seconds.
- SenseVoice cold load after streaming model: 0.517 seconds.
- Combined process peak RSS after loading both: about 806 MB on the test Mac.
- With both models resident during the resampling benchmark, streaming RTF was
  about 0.20 and SenseVoice RTF about 0.03; both remained faster than real time.

## Reproduction

Run model accuracy comparison:

```bash
cd backend
.venv/bin/python evals/eval_asr_accuracy.py \
  --online-model /path/to/streaming-model \
  --sense-voice-model /path/to/sense-voice-model \
  --zipformer-ctc-model /path/to/zipformer-ctc-model
```

Run input-path stress comparison:

```bash
cd backend
.venv/bin/python evals/eval_asr_resampling.py \
  --online-model /path/to/streaming-model \
  --sense-voice-model /path/to/sense-voice-model
```

Model weights remain outside the repository. Before integration, retain the
upstream model name and attribution and document the applicable FunASR model
license separately from sherpa-onnx's Apache-2.0 runtime license.
