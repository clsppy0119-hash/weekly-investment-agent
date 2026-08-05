---
name: investment-committee-shadow
description: Run a token-efficient, default-off, read-only investment committee shadow review using shared evidence. Use for experimental bull/bear/risk/judge analysis without changing production recommendations, schedules, notifications, or trading behavior.
---

# Investment Committee Shadow

Run only when an explicit shadow-mode flag is enabled. Keep the path isolated from production output.

Roles read the same evidence packet and return compact JSON only:

- `bull`: thesis, evidence IDs, confidence.
- `bear`: independent falsification of valuation, demand/earnings downside, geopolitical/supply-chain exposure, other material downside, evidence IDs, triggers, confidence.
- `risk`: data-quality, concentration, liquidity, corporate-action, and loss risks, evidence IDs, confidence.
- `judge`: buy / observe / do-not-invest, confidence, decisive evidence IDs, unresolved risks, and whether the quality gate passed.

The judge reads the role outputs and packet summary only; it must not refetch data or create a long debate. Reject a positive shadow verdict when the bear output is missing required challenge categories, repeats the bull thesis, lacks evidence IDs, or the packet quality gate fails.

Safety boundaries:

- Default off and read-only.
- Never place orders, alter production recommendations, change schedules, or send real notifications.
- Label every result `shadow_only`; never silently promote it.
