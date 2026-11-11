# Critical Review of the Plan

Version 0.1, 2026-11-11. Written before any code exists, to be revisited at the end of each phase.

## 1. Risks

| # | Risk | Likelihood | Impact | Mitigation |
| --- | --- | --- | --- | --- |
| R1 | Small local models (7B class in LM Studio) route and call tools poorly, so Phase 1 numbers look bad | high | medium | Report honestly; frame as the baseline that the DeepSeek comparison in v1 improves on. Choose the local model deliberately (one with documented tool-call support) and record the model name in every report |
| R2 | Ports are abstracted wrong on the first try and the v1 adapters (second provider, semantic cache, hybrid retriever) force interface changes | medium | medium | Phase 1 implements one adapter per port and keeps signatures minimal; expect one round of port cleanup at the start of v1 and budget 3 h for it |
| R3 | LangGraph API churn breaks the orchestrator adapter | medium | low | Pin versions; the port means the blast radius is one package; v2's sequential adapter is the escape hatch |
| R4 | Phase 1 overruns beyond two weeks | medium | high for motivation | Cut list below; T1.17 (first measured report) is the real finish line, not the UI |
| R5 | Olist dataset download needs a Kaggle account and manual steps, hurting "one command" | high | low | Loader accepts a local directory; README documents the five minute download; a 1000-row sample fixture ships in the repo for tests and CI |
| R6 | Synthetic policy documents feel fake to an interviewer | medium | low | Keep them short, realistic and clearly labeled; the point is the pipeline, not the corpus |
| R7 | Eval datasets written by the same person who writes the prompts are biased toward passing | medium | medium | Write datasets before the prompts for each suite; include a labeled "ambiguous" tag; show the confusion matrix, not just accuracy |
| R8 | Windows plus Docker Desktop plus LM Studio networking issues (`host.docker.internal`) | medium | medium | Verify in T1.02 before anything else depends on it; document the fallback of running the API outside Docker with `uv run` |
| R9 | AI assistant token budget for writing the code is limited | medium | medium | Specs are detailed on purpose so implementation sessions are short; each task has a clear verification so sessions end cleanly |
| R10 | Trace volume in PostgreSQL grows during eval runs | low | low | Eval runs tag spans with `eval_run_id`; a cleanup command deletes them; not a Phase 1 concern |

## 2. Where this plan may be over-engineered

- **Entry-point registry.** A dict mapping names to classes would do for a solo project. Entry points are kept because "a third-party package can add an adapter" is a concrete sentence for an interview and costs about an hour. If it causes friction, fall back to a dict in `registry.py` with the same public API.
- **Tool versioning with dual-serving majors.** Nobody will depend on `orders_late@1`. The rule is kept in the manifest and the eval dataset pins, but the "serve both majors for a phase" behavior is not implemented until a major bump actually happens.
- **Two database roles and read-only transactions.** Slightly heavier than a code review, but it is the one security claim that a test can prove in ten lines, so it stays.
- **Pass-through gate and cache nodes in Phase 1.** Adds two empty nodes. Kept because it makes the v1 diff tiny and the graph diagram stable across phases.
- **Human-in-the-loop hook that never fires in Phase 1 or v1.** Not implemented until v2; only the risk field in the manifest exists earlier. That is the right size.
- **OTel instead of a simple event log.** OTel adds some setup, but the alternative (a custom event schema) would need to be replaced later for the Jaeger demo. Kept.
- **Multi-tenancy from day one.** Carrying `tenant_id` everywhere is cheap; the permission model and seller filter are v1. The cost is one extra field, the benefit is never retrofitting it.

## 3. Cut order if Phase 1 runs out of time

Cut from the bottom up; each line names what the demo loses.

1. T1.14 web UI: replace with the API's JSON trace rendered by a tiny static page or shown in the terminal. Loses visual polish, keeps every claim.
2. `parallel` route and `MergeAgent` (parts of T1.10 and T1.11): demo shows three routes. Loses the best demo moment; move to v1.
3. T1.16 regression gate: keep lint and tests in CI, add the gate in v1. Loses the "regression gate in CI" bullet temporarily.
4. Structured output strategies 2 and 3 in T1.05: ship native JSON schema plus prompted JSON only. Loses nothing visible.
5. `top_products@1` and `describe_schema@1` tools: three tools still demonstrate the layer.

Never cut: T1.07 read-only tools with audit, T1.12 tracing, T1.15 and T1.17 measured reports. Without those the project is a demo, not evidence.

## 4. What I would challenge in my own design

- **Is LangGraph pulling its weight behind a port?** For four routes and one parallel branch, a hand-written async function would be shorter. LangGraph is kept because it is what the target jobs ask for, the state model and interrupts are useful in v2, and the sequential adapter in v2 lets the project show both. If v2 never happens, the interview answer is still honest: "I kept it replaceable, here is the port."
- **Is a semantic cache worth a whole task?** On a demo corpus with no real traffic, the hit rate is whatever the dataset makes it. The value is in the false-hit measurement and the invalidation design, which are the parts that go wrong in production. The report must show the threshold sweep, otherwise the feature is decoration.
- **Is the quality gate a judge that can be wrong?** Yes. That is why it gets its own precision and recall numbers and why it re-routes at most once. A gate without measurement is worse than no gate.
- **Will the local model make the toolcall suite meaningless?** Possibly. The suite should be run on DeepSeek early in v1 to see the ceiling; if even a cloud model is below 0.85, the tool descriptions or the dataset need work, and that finding is itself a good README paragraph.

## 5. Open questions for the author

1. Which LM Studio model will be the Phase 1 default? It should have documented tool-call support and fit comfortably in VRAM with an embedding model loaded at the same time.
2. Does the Olist seller id mapping make sense as the tenant boundary, or should tenants map to product categories? Seller is proposed because it is the most natural "merchant" story.
3. Is the Apple-like visual direction for the UI something to settle before T1.14, or after Phase 1 in T2.12? The plan assumes after, so Phase 1 UI is unstyled.
4. Should eval reports be committed to the repository or published as CI artifacts only? The plan commits selected reports so the README can link to them.
