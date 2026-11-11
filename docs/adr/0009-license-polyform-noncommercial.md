# ADR 0009: License: PolyForm Noncommercial 1.0.0

Status: proposed, 2026-11-11

## Context

The author wants the code public for portfolio purposes, wants attribution, and does not want commercial use without permission. Under the Open Source Definition, a license that forbids commercial use is not "open source"; the accurate term is "source-available".

## Decision

License the repository under PolyForm Noncommercial 1.0.0. It permits use, modification and distribution for noncommercial purposes, requires that license notices and attribution be preserved, and is written for software (unlike Creative Commons licenses). The README describes the project as source-available and names the author for attribution. Commercial licensing is available on request.

## Alternatives

- **Apache-2.0 or MIT.** Most conventional for portfolio projects; allows commercial use. Rejected by the author's requirement.
- **AGPL-3.0.** Open source in the strict sense; commercial users must publish their modifications when offering the software as a service, which deters freeloading. Attribution through copyright notices. A reasonable alternative if the author later wants the "open source" label.
- **CC BY-NC 4.0.** Common for noncommercial content. Rejected because it is not designed for code (no patent clause, unclear on linking and distribution of binaries).

## Consequences

- Contributors are unlikely; that is acceptable for a portfolio project.
- Dependencies are all permissive or copyleft-compatible with noncommercial distribution; the Olist dataset (CC BY-NC-SA 4.0) is not redistributed and must be downloaded by the user.
- The README and the docs must say "source-available", not "open source".
