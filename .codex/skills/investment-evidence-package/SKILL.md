---
name: investment-evidence-package
description: Build one compact, deterministic evidence packet for a Taiwan-stock research task. Use before any bull, bear, risk, judge, report, or recommendation workflow so all roles share the same quoted data and source identifiers.
---

# Investment Evidence Package

Create one immutable packet per stock and pass its reference to every downstream role.

Include only structured evidence: stock code/name, quote timestamp, source and dataset identifiers, coverage status, fundamentals, prices, dividends/ex-rights, news references, and known limitations. Attach an evidence ID to every material claim.

Rules:

- Fetch and normalize data once; do not let specialist roles fetch URLs or duplicate provider calls.
- Keep raw rows, tokens, credentials, and dynamic market values out of this Skill.
- Mark missing, stale, conflicting, or unaudited fields explicitly; never fill gaps by inference.
- Use deterministic code for calculations, thresholds, fees, tax, slippage, corporate actions, and coverage counts.
- Pass a compact JSON packet downstream; do not include long narrative or duplicate evidence.
