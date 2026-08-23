"""The ranking rule the daily report ships, in one importable place.

This used to live inside ``daily_report.py``, which runs its whole pipeline on
import, so nothing else could reuse it.  The backtest therefore validated a
plain price-momentum rule that the product never actually runs.  Keeping the
rule here lets the report and the backtest score candidates with the same code,
so a backtest result says something about the recommendations users see.
"""

from __future__ import annotations

import math

WEIGHTS = {
    "value": {"revenue": 15, "eps": 15, "roe": 15, "debt": 15, "pe": 15, "pb": 10, "dividend": 5, "trend20": 10},
    "swing": {"revenue": 10, "eps": 5, "roe": 5, "debt": 5, "pe": 5, "trend20": 25, "trend5": 20, "change": 25},
    "dividend": {"revenue": 10, "eps": 15, "roe": 15, "debt": 15, "pe": 10, "pb": 5, "dividend": 25, "trend20": 5},
    "comprehensive": {"revenue": 15, "eps": 12, "roe": 12, "debt": 10, "pe": 10, "pb": 6, "dividend": 7, "trend20": 12, "trend5": 7, "change": 9},
}

MINIMUM_COVERAGE = {"value": 70, "swing": 45, "dividend": 70, "comprehensive": 70}
MINIMUM_SCORE = 60
DEFAULT_PICKS = 3
MAX_NUMBER_ABS = 10**18


def number(value):
    """True only for bounded, finite built-in JSON numbers.

    Python comparisons with NaN and infinity do not fail closed: NaN can fall
    through score thresholds, while infinity can win a ranking tie.  The
    production inputs are JSON, so accepting numeric subclasses or integers
    too large for a float conversion only creates an extra hostile surface.
    """
    if type(value) is int:
        return abs(value) <= MAX_NUMBER_ABS
    return (
        type(value) is float
        and math.isfinite(value)
        and abs(value) <= MAX_NUMBER_ABS
    )


def ranking_volume(quote):
    """Positive finite volume, with every zero/invalid form canonicalized to 0."""
    if type(quote) is not dict:
        return 0
    value = quote.get("volume")
    return value if number(value) and value > 0 else 0


def score_metric(value, thresholds, default=None):
    if not number(value):
        return default
    for limit, score in thresholds:
        if value >= limit:
            return score
    return thresholds[-1][1]


TREND_SLOPE = 2.5

# Every strength factor rewarded more strength without limit, so the top of the
# ranking was whatever had moved furthest that day. The Taiwan daily limit is
# 10%, and the picks averaged +9.10% while the index heavyweights that actually
# drive 0050 averaged +2.48% and were never selected once in 92 signal days.
# The rule was buying the day's limit-up names, which is short-term reversal --
# the peak of a speculative spike, not evidence of strength.
#
# These give each factor a single peak: moderate strength scores best, and both
# weakness and over-extension score worse.
#
# TESTED AND REJECTED, 2026-08-09, two years of official data. The reasoning
# above is sound in general and wrong here: penalising the extreme made results
# worse, not better. Against 0050 the train window went from +0.98% per
# rebalance to -1.58%, conclusively negative, and no split improved. Against the
# eligible pool it fell from +1.83% to -0.63%. Whatever drives the daily limit
# names in this sample continued rather than reversed.
#
# Kept opt-in and unused so the negative result is not re-discovered by someone
# reasoning their way to the same idea. Do not enable without new evidence.
CHANGE_PEAK_PCT = 2.5
CHANGE_DECAY = 8.0
TREND_PEAK_PCT = 8.0
TREND_DECAY = 4.0


def trend_score(price, average, continuous: bool, reversal_aware: bool = False):
    """Position relative to a moving average.

    The shipped form is binary, which discards how far above the average a
    stock sits and makes scores tie in large blocks.  The continuous form keeps
    the distance.  The reversal-aware form also stops treating an extended
    stock as a strong one.
    """
    if not (number(price) and number(average)) or average <= 0:
        return None
    if not continuous:
        return 75 if price >= average else 35
    premium = (price / average - 1) * 100
    if not math.isfinite(premium):
        return None
    if reversal_aware:
        return max(0.0, min(100.0, 100 - TREND_DECAY * abs(premium - TREND_PEAK_PCT)))
    return max(0.0, min(100.0, 50 + premium * TREND_SLOPE))


def change_score(change, reversal_aware: bool = False):
    """Today's move, as evidence about tomorrow.

    The shipped form rises with the move and caps at 90, so anything near the
    daily limit is indistinguishable from the best possible candidate.
    """
    if not number(change):
        return None
    if reversal_aware:
        return max(15.0, min(90.0, 90 - CHANGE_DECAY * abs(change - CHANGE_PEAK_PCT)))
    return min(90, 65 + change * 3) if change > 0 else max(15, 50 + change * 3)


def metrics(quote: dict, fund: dict, continuous_trend: bool = False,
            reversal_aware: bool = False) -> dict:
    """Per-factor scores, or ``None`` where the input is missing."""
    return {
        "revenue": score_metric(fund.get("revenueYoY"), [(20, 90), (10, 80), (0, 65), (-10, 45), (-999999, 20)]),
        "eps": 70 if number(fund.get("eps")) and fund["eps"] > 0 else (15 if number(fund.get("eps")) else None),
        "roe": score_metric(fund.get("roe"), [(20, 90), (15, 80), (8, 65), (5, 45), (-999999, 25)]),
        "debt": score_metric(-fund["debtRatio"], [(-30, 90), (-50, 75), (-70, 50), (-999999, 20)]) if number(fund.get("debtRatio")) else None,
        "pe": score_metric(-fund["pe"], [(-12, 85), (-20, 75), (-30, 60), (-45, 40), (-999999, 20)]) if number(fund.get("pe")) and fund["pe"] > 0 else None,
        "pb": score_metric(-fund["pb"], [(-1.5, 85), (-3, 70), (-6, 50), (-999999, 30)]) if number(fund.get("pb")) and fund["pb"] > 0 else None,
        "dividend": (85 if 3 <= fund["dividendYield"] <= 8 else 45 if fund["dividendYield"] > 10 else 65 if fund["dividendYield"] >= 2 else 35) if number(fund.get("dividendYield")) else None,
        "trend20": trend_score(quote.get("price"), quote.get("ma20"), continuous_trend, reversal_aware),
        "trend5": trend_score(quote.get("price"), quote.get("ma5"), continuous_trend, reversal_aware),
        "change": change_score(quote.get("change"), reversal_aware),
    }


def score_quote(quote: dict, fund: dict, style: str, weights: dict | None = None,
                continuous_trend: bool = False, reversal_aware: bool = False) -> tuple[int, int]:
    """Weighted score and the weight actually backed by present data.

    ``weights`` overrides the style's table, which lets research measure one
    factor's contribution without editing the shipped weights.
    """
    weights = WEIGHTS[style] if weights is None else weights
    metric = metrics(quote, fund, continuous_trend, reversal_aware)
    available_weight = sum(weight for key, weight in weights.items() if number(metric[key]))
    weighted = sum(metric[key] * weight for key, weight in weights.items() if number(metric[key]))
    return (round(weighted / available_weight) if available_weight else 0), available_weight


def stock_score(code, style, quotes, fundamentals, weights: dict | None = None,
                continuous_trend: bool = False, reversal_aware: bool = False):
    return score_quote(quotes.get(code, {}), fundamentals.get(code, {}), style, weights,
                       continuous_trend, reversal_aware)


def candidates(style, quotes, fundamentals, picks: int | None = DEFAULT_PICKS,
               weights: dict | None = None, minimum_coverage: int | None = None,
               continuous_trend: bool = False, reversal_aware: bool = False):
    """Ranked eligible candidates; ``picks=None`` returns the whole pool."""
    ranked = []
    if minimum_coverage is None:
        minimum_coverage = MINIMUM_COVERAGE[style]
    for code, fund in fundamentals.items():
        quote = quotes.get(code, {})
        price = quote.get("price")
        if not (
            type(code) is str
            and len(code) == 4
            and all("0" <= character <= "9" for character in code)
            and number(price)
            and price > 0
        ):
            continue
        score, coverage = stock_score(code, style, quotes, fundamentals, weights,
                                      continuous_trend, reversal_aware)
        if score >= MINIMUM_SCORE and coverage >= minimum_coverage:
            ranked.append((score, coverage, code, quote, fund))
    # Scores tie constantly -- the trend factors are binary and `change` is
    # capped -- and sorting the raw tuple broke those ties on the stock code,
    # descending.  That silently bought the highest-numbered names, which in
    # Taiwan skews to small speculative listings.  Break on traded volume
    # instead, keeping the code only as a last resort for reproducibility.
    ranked.sort(
        key=lambda item: (item[0], item[1], ranking_volume(item[3]), item[2]),
        reverse=True,
    )
    return ranked if picks is None else ranked[:picks]
