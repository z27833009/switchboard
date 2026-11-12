# Progress

One line per task. Status is `todo`, `doing`, `blocked` (say on what), or `done` (with the commit). Updated in the same commit as the work. Notes hold facts a later session needs, such as measured numbers from [host] steps.

## Phase 1

| Task | Status | Commit | Notes |
| --- | --- | --- | --- |
| T1.01 | done | bf1b275 | Scaffold: uv 0.11.21, Python 3.12.13, ruff, mypy strict, pytest, pre-commit (local hooks), import-linter "Core imports nothing", CI lint and unit tests. CI green on push, run 43256857854 |
| T1.02 | done | 7d30c9f | Bionic 1.2.0 (LM Studio 1.2) on host port 18080: 1234 falls in a Windows excluded TCP range (1210-1309). Models loaded together at context 8192, parallel 1: 7745 of 8151 MiB VRAM used (405 MiB free). Qwen3-8B chat 24.5 s for 16 tokens, 14 of them reasoning tokens (thinking is on by default); bge-m3 embedding 2.7 s, 1024 dimensions. All four compose services healthy; /health reports postgres, mcp_olist and lmstudio as ok, and the API reaches LM Studio through host.docker.internal |
| T1.03 | todo | | Data downloaded and unzipped into `data/olist/raw/` (2026-11-12). CSV data rows (excluding header), measured: customers 99441, geolocation 1000163, order_items 112650, order_payments 103886, order_reviews 99224, orders 99441, products 32951, sellers 3095, category translation 71. Purchase dates 2016-09-04 to 2018-10-17; months outside 2017-01 to 2018-08 have very few orders. All eight statuses in design 7.1 occur |
| T1.04 | todo | | |
| T1.05 | todo | | |
| T1.06 | todo | | |
| T1.07 | todo | | |
| T1.08 | todo | | |
| T1.09 | todo | | |
| T1.09a | done | d581a3a and next | Routing: 60 questions from a separate session, blind labels by the author, agreement 50/60, 10 ambiguous, 5 adjudicated; splits dev 21, test 39. Toolcall: 40 cases generated, order ids sampled from the download; wording reviewed by the assistant at the author's request (labels are computed, so no independence is needed there). Date ranges filter on purchase date (design 7.1) |
| T1.10 | todo | | |
| T1.11 | todo | | |
| T1.12 | todo | | |
| T1.13 | todo | | |
| T1.14 | todo | | |
| T1.15 | todo | | |
| T1.16 | todo | | |
| T1.17 | todo | | |
| T1.18 | todo | | |
