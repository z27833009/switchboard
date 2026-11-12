# Progress

One line per task. Status is `todo`, `doing`, `blocked` (say on what), or `done` (with the commit). Updated in the same commit as the work. Notes hold facts a later session needs, such as measured numbers from [host] steps.

## Phase 1

| Task | Status | Commit | Notes |
| --- | --- | --- | --- |
| T1.01 | done | bf1b275 | Scaffold: uv 0.11.21, Python 3.12.13, ruff, mypy strict, pytest, pre-commit (local hooks), import-linter "Core imports nothing", CI lint and unit tests. CI green on push, run 43256857854 |
| T1.02 | done | 7d30c9f | Bionic 1.2.0 (LM Studio 1.2) on host port 18080: 1234 falls in a Windows excluded TCP range (1210-1309). Models loaded together at context 8192, parallel 1: 7745 of 8151 MiB VRAM used (405 MiB free). Qwen3-8B chat 24.5 s for 16 tokens, 14 of them reasoning tokens (thinking is on by default); bge-m3 embedding 2.7 s, 1024 dimensions. All four compose services healthy; /health reports postgres, mcp_olist and lmstudio as ok, and the API reaches LM Studio through host.docker.internal |
| T1.03 | done | fb52462 | Loader uses asyncpg COPY; first full load took 10.2 s and counts are in `data/olist/expected_counts.json` and `data/olist/README.md`; a second load reports a match. Sample: 1000 orders (1139 items, 1034 payments, 994 reviews, 928 products, 537 sellers, 71 translations, geolocation header only), deterministic across runs and loaded into a separate database. `switchboard_tools` SELECT works; INSERT, DELETE and CREATE TABLE are denied. Purchase dates 2016-09-04 to 2018-10-17; months outside 2017-01 to 2018-08 have very few orders. All eight statuses in design 7.1 occur |
| T1.04 | done | 866fbe8 | Core: 9 ports, domain models, error taxonomy, plan validation, ClearOldestToolResults and the config schema/registry. PyYAML stays in `switchboard/config.py`; `core/config.py` holds the models and env substitution, so the core import contract stays green. Config files for platform, providers and tenants are committed. 39 unit tests pass, mypy strict covers 25 files |
| T1.05 | done | 660f9ae | `OpenAICompatibleProvider` (openai 3.49.0) with the three structured-output strategies and one repair round; 15 contract tests on five recorded Bionic 1.2.0 / qwen3-8b payloads. Live smoke: plan agents `[data]`, strategy `tool_call`, 250 input and 49 output tokens, 31.7 s, no reasoning tokens (`reasoning_effort: none`). LM Studio 1.2 requires `tool_choice: "required"` for the tool-call strategy. Host commands use `localhost:18080`; compose passes `host.docker.internal` to the api container |
| T1.06 | done | 60fb4f3 | `MockProvider` keyed by SHA-256 of purpose, model, normalised messages, tool specs and response schema; a miss raises `FixtureMissError` naming the purpose and the re-record command. `scripts/record_mock_fixtures.py` recorded two fixtures from Bionic 1.2.0 / qwen3-8b (demo 19/3 tokens, routing plan `[data]` 250/49 tokens); a changed prompt misses. `MockEmbedder` is deterministic; `OpenAICompatibleEmbedder` live check returned 1024-dimensional vectors. 32 contract tests |
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
