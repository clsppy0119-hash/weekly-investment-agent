---
name: investment-quality-gates
description: Apply deterministic data-quality, backtest, audit, and reporting gates for the Taiwan-stock investment Agent. Use before publishing research, promoting a strategy, or formatting a traceable decision report.
---

# Investment Quality Gates

Evaluate gates in this order and record pass/fail plus evidence IDs:

1. Source and freshness: source, dataset, retrieval time, and coverage are present.
2. Data completeness: required quote, fundamental, price, dividend, and ex-right fields meet the task threshold.
3. Point-in-time integrity: historical listing and exit evidence is available; otherwise mark survivorship bias and keep research-only.
4. Backtest integrity: train, validation, and untouched test splits; 0050 benchmark; commissions, tax, slippage, cash/stock dividends, and ex-right adjustment included.
5. Decision traceability: thesis, counter-thesis, risks, triggers, assumptions, and source IDs are recorded.

Any failed mandatory gate blocks production recommendation or notification. The report must state the failed gate, scope, update time, and limitations. Use deterministic summaries and compact JSON; do not put dynamic market data in this Skill.
