---
name: investment-agent-operations
description: Run and maintain the Taiwan-stock investment Agent's repeated data, backtest, research, status-reporting, and safety workflows. Use when manually fetching data, checking scheduled GitHub Actions, interpreting cache coverage, running research-only backtests, or changing these recurring procedures.
---

# Investment Agent Operations

Use this skill for the recurring operational workflow. Keep dynamic market data, tokens, raw cache rows, and one-off run output out of this skill.

For specialized recurring steps, use the companion Skills:

- `investment-evidence-package` for one shared evidence packet.
- `investment-committee-shadow` for isolated, default-off committee review.
- `investment-quality-gates` for deterministic data, backtest, and audit gates.

## Data fetch

1. Use the `full-market-fundamentals` workflow for an on-demand fetch; confirm the run ID and wait for completion.
2. The scheduled workflow runs every 30 minutes (`*/30 * * * *`). Each run selects at most 50 symbols and at most 300 provider requests.
3. A transient failure may retry after three minutes. HTTP 402 or another explicit provider quota/payment response stops that run immediately; do not bypass limits or use VPNs.
4. Read the non-sensitive status artifact before reporting. Always report: run URL, success/partial/failure, symbols processed, cumulative cached / universe, remaining, unavailable, quota-limited flag, and whether the scheduled workflow is enabled.
5. If a run fails before producing a status artifact, report the last known coverage separately and do not invent a new count.

## Research and backtest gate

1. Treat incomplete current-market coverage, incomplete historical listing/exit evidence, or survivorship bias as research-only.
2. Backtests must include commissions, securities transaction tax, slippage, cash and stock dividends, and ex-right/ex-dividend adjustment.
3. Require validation and untouched test splits against 0050 before considering promotion. Never turn a research-only result into a production recommendation.
4. Do not place orders, overwrite production recommendations, or send real trading instructions from this workflow.

## Change and publication safety

- Keep tokens, private caches, holdings, and user-identifying data out of Git and artifacts.
- Validate Python and workflow syntax locally before any publication.
- Do not commit, push, or publish unless the user explicitly requests it for that change.
