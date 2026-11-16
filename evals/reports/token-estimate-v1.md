# Token estimate calibration v1

Provider: `lmstudio` / `qwen3-8b`. Dataset: `evals/datasets/v3/token-calibration.json` (10 dev cases).
Each request sends only the Chinese user message, so the reported prompt tokens are the
chat template plus that message; the system prompt and tool schemas are not included.

| Metric | Value |
| --- | ---: |
| Raw p50 relative error | 32.00% |
| Raw p95 relative error | 52.17% |
| Raw maximum relative error | 52.17% |
| Fit slope | 0.449 |
| Fit intercept | 18.9373 tokens |
| Calibrated p50 relative error | 6.28% |
| Calibrated p95 relative error | 37.36% |
| Calibrated maximum relative error | 37.36% |

| Case | Characters | Estimated | Actual | Raw error | Calibrated | Calibrated error |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| cal-01 | 14 | 10 | 19 | 47.37% | 23.43 | 23.30% |
| cal-02 | 27 | 19 | 29 | 34.48% | 27.47 | 5.28% |
| cal-03 | 21 | 17 | 25 | 32.00% | 26.57 | 6.28% |
| cal-04 | 27 | 17 | 27 | 37.04% | 26.57 | 1.59% |
| cal-05 | 23 | 17 | 26 | 34.62% | 26.57 | 2.19% |
| cal-06 | 45 | 22 | 46 | 52.17% | 28.81 | 37.36% |
| cal-07 | 32 | 24 | 33 | 27.27% | 29.71 | 9.96% |
| cal-08 | 21 | 21 | 21 | 0.00% | 28.37 | 35.07% |
| cal-09 | 30 | 28 | 25 | 12.00% | 31.51 | 26.03% |
| cal-10 | 43 | 38 | 34 | 11.76% | 36.0 | 5.88% |

The estimate counts CJK characters as one token each and other characters at three
characters per token, plus four tokens per message. The fit is a linear correction
on this dev set; the report keeps both the raw and calibrated errors so the gap is
visible instead of hiding the template overhead.
