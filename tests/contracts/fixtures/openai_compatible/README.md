# Recorded OpenAI-compatible responses

Captured on 2026-11-12 from Bionic 1.2.0 (LM Studio 1.2.0), model `qwen3-8b`,
served on `http://localhost:18080/v1` with `temperature: 0` and
`reasoning_effort: none` unless noted.

| File | Request | Notes |
| --- | --- | --- |
| `plain_reply.json` | "Reply with the single word OK." | `reasoning_content` empty |
| `reasoning_reply.json` | "Is 17 prime? Think briefly." | thinking left on, `finish_reason: length`, reasoning separated from content |
| `tool_call.json` | weather question with a `get_weather` tool | one call, arguments as a JSON string |
| `prompted_json.json` | plan question, JSON requested in the system prompt | no response format support |
| `native_json.json` | plan question with `response_format: json_schema` | LM Studio accepts it, but the provider config declares `json_schema: false` because small models may ignore it |

The cached-token test adds `usage.prompt_tokens_details.cached_tokens` to a copy
of `plain_reply.json` inline. LM Studio does not report prefix-cache hits, but
OpenAI-compatible gateways and DeepSeek do, so the field shape is tested.

The bodies are stored unchanged. Load them with
`ChatCompletion.model_validate(payload)`; do not add a fake default response.
