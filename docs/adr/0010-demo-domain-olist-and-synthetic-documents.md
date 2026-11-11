# ADR 0010: Demo domain: Olist dataset plus synthetic policy documents

Status: proposed, 2026-11-11

## Context

The demo needs a business database for SQL-style tools and a document corpus for RAG, with a natural multi-tenant story, and nothing may come from the author's former employer.

## Decision

- Business data: the public Olist Brazilian E-commerce dataset (Kaggle, CC BY-NC-SA 4.0): orders, items, customers, sellers, products, payments, reviews. Loaded into schema `olist` by a script from CSV files the user downloads; a 1000-row sample ships in the repository for tests and CI. The dataset is not redistributed in full.
- Tenants map to sellers: a tenant's `seller_ids` restrict tenant-scoped tools to that seller's orders. The `default` tenant is unrestricted for the demo.
- Documents: a small set of synthetic shop policy documents (returns, shipping, seller onboarding, payments, late delivery compensation) written for this project and marked as synthetic in their front matter. They exist so that routing has a `rag` target and `parallel` has a reason to exist (data plus policy).

## Alternatives

- **SEC EDGAR filings.** Rich documents, public. Rejected because the structured side (financial tables) makes weaker tool demos and the tenant story is forced.
- **Open-source project docs plus GitHub issues.** Good RAG corpus. Rejected because a business database is the stronger tool story and matches the author's background.
- **Fully synthetic database.** Full control. Rejected because a real public dataset is more credible and saves generation effort.

## Consequences

- Dates in demo questions must fall in the dataset's range (2016 to 2018).
- The noncommercial dataset license is compatible with the project license (ADR 0009).
- Synthetic documents are a known weakness for realism; kept short and clearly labeled.
