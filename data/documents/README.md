# Synthetic policy documents

These five markdown files are written for the Switchboard demo. They are not
real marketplace policies and do not describe any real company. They exist so
the retrieval pipeline has a small, stable corpus with clear headings and
retrievable statements.

| File | Topic |
| --- | --- |
| `returns-policy.md` | return windows, electronics, refunds |
| `shipping-policy.md` | delivery times, carriers, tracking |
| `late-delivery-compensation.md` | when a delivery is late, vouchers |
| `payments.md` | payment methods, installments, refund timing |
| `seller-onboarding.md` | becoming a seller, documents, commission |

Ingest with:

```bash
uv run --env-file .env switchboard ingest data/documents --tenant default
```

The command is idempotent: a document whose content hash did not change is
skipped, and a second run reports zero changes.
