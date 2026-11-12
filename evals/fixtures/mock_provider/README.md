# Mock provider fixtures

Recorded on 2026-11-12 from Bionic 1.2.0 (LM Studio 1.2.0), model `qwen3-8b`,
context 8192, `reasoning_effort: none`.

| Purpose | Count | Contents |
| --- | --- | --- |
| `demo` | 1 | a plain completion, text `OK.`, 19 input and 3 output tokens |
| `routing` | 1 | a structured plan via the tool-call strategy, agents `[data]`, 250 input and 49 output tokens |
| `supervisor.plan` | 60 | one routing case each, recorded by the T1.15 eval harness |
| `agent.data` | 40 | one toolcall case each, recorded by the T1.15 eval harness |

The file name is the SHA-256 of the normalised request: purpose, model,
messages, tool specs and response schema. Temperature, `max_tokens` and run
metadata are deliberately not in the key (ADR 0008). A changed prompt changes
the key, so replay misses and the test tells you to re-record; it never falls
back to a default response.

Re-record the demo and routing smoke fixtures with:

```bash
uv run --env-file .env python scripts/record_mock_fixtures.py --force
```

Re-record the eval fixtures with (the files already present are replayed, so
delete one first when its prompt changed):

```bash
uv run --env-file .env switchboard eval run --suite routing --provider mock --record --split all
uv run --env-file .env switchboard eval run --suite toolcall --provider mock --record
```

Review the diff before committing. The CI gate replays these fixtures and
therefore measures the pipeline (parsing, validation, normalization, scoring),
not the model.
