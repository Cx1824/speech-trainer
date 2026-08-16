# 声学管线评测报告（合成真值集）

## A. 基频（f0）

| 音色 | 条数 | f0中位数 | 跨条CV | 判定 |
|---|---|---|---|---|
| zh-CN-XiaoxiaoNeural | 7 | 244.1 | 2.1% | ✅（区间✓ 稳定✓） |
| zh-CN-XiaoyiNeural | 7 | 298.1 | 0.6% | ✅（区间✓ 稳定✓） |
| zh-CN-YunxiNeural | 7 | 205.1 | 1.6% | ✅（区间✓ 稳定✓） |
| zh-CN-YunyangNeural | 7 | 141.7 | 2.6% | ✅（区间✓ 稳定✓） |

## B. 语速（相对注入 rate）

| 音色 | 注入 | 实测字/秒 | 相对比 | 期望 | 误差 | 判定 |
|---|---|---|---|---|---|---|
| Xiaoxiao_base | -20% | 7.58 | 0.837 | 0.80 | 4.6% | ✅ |
| Xiaoxiao_base | -10% | 8.38 | 0.925 | 0.90 | 2.8% | ✅ |
| Xiaoxiao_base | +0% | 9.06 | 1.000 | 1.00 | 0.0% | ✅ |
| Xiaoxiao_base | +10% | 10.11 | 1.116 | 1.10 | 1.4% | ✅ |
| Xiaoxiao_base | +20% | 10.66 | 1.176 | 1.20 | 2.0% | ✅ |
| Xiaoyi_base | -20% | 7.80 | 0.812 | 0.80 | 1.5% | ✅ |
| Xiaoyi_base | -10% | 8.81 | 0.916 | 0.90 | 1.8% | ✅ |
| Xiaoyi_base | +0% | 9.61 | 1.000 | 1.00 | 0.0% | ✅ |
| Xiaoyi_base | +10% | 10.36 | 1.078 | 1.10 | 2.0% | ✅ |
| Xiaoyi_base | +20% | 11.34 | 1.180 | 1.20 | 1.7% | ✅ |
| Yunxi_base | -20% | 14.95 | 0.782 | 0.80 | 2.3% | ✅ |
| Yunxi_base | -10% | 16.90 | 0.884 | 0.90 | 1.8% | ✅ |
| Yunxi_base | +0% | 19.12 | 1.000 | 1.00 | 0.0% | ✅ |
| Yunxi_base | +10% | 20.00 | 1.046 | 1.10 | 4.9% | ✅ |
| Yunxi_base | +20% | 22.81 | 1.193 | 1.20 | 0.6% | ✅ |
| Yunyang_base | -20% | 14.99 | 0.776 | 0.80 | 3.0% | ✅ |
| Yunyang_base | -10% | 16.85 | 0.872 | 0.90 | 3.1% | ✅ |
| Yunyang_base | +0% | 19.32 | 1.000 | 1.00 | 0.0% | ✅ |
| Yunyang_base | +10% | 20.88 | 1.081 | 1.10 | 1.7% | ✅ |
| Yunyang_base | +20% | 22.36 | 1.157 | 1.20 | 3.5% | ✅ |

## C. jitter（TTS 平稳参考带）

- 条目数 28，中位数 0.1188，P90 0.1872，最大 0.2054
- 机器平稳带参考：≤ 0.1882（真人紧张应显著高于此带，后续真人阶段校验）

## D. 停顿计数分布（base 文本，参考）

| 音色 | rate | pause_count |
|---|---|---|
| zh-CN-XiaoxiaoNeural | -20% | 17 |
| zh-CN-XiaoxiaoNeural | -10% | 11 |
| zh-CN-XiaoxiaoNeural | +0% | 6 |
| zh-CN-XiaoxiaoNeural | +10% | 6 |
| zh-CN-XiaoxiaoNeural | +20% | 5 |
| zh-CN-XiaoyiNeural | -20% | 11 |
| zh-CN-XiaoyiNeural | -10% | 9 |
| zh-CN-XiaoyiNeural | +0% | 6 |
| zh-CN-XiaoyiNeural | +10% | 5 |
| zh-CN-XiaoyiNeural | +20% | 5 |
| zh-CN-YunxiNeural | -10% | 20 |
| zh-CN-YunxiNeural | +0% | 19 |
| zh-CN-YunxiNeural | +20% | 17 |
| zh-CN-YunyangNeural | -20% | 20 |
| zh-CN-YunyangNeural | -10% | 15 |
| zh-CN-YunyangNeural | +0% | 11 |
| zh-CN-YunyangNeural | +10% | 8 |
| zh-CN-YunyangNeural | +20% | 7 |
| zh-CN-YunxiNeural | -20% | 20 |
| zh-CN-YunxiNeural | +10% | 19 |

## 总判定

- 基频：✅ PASS
- 语速：✅ PASS（相对误差<10%）
- jitter/停顿：参考带已记录（无硬线）
