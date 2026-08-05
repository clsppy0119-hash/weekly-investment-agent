"""Small, deterministic price/volume strategy variants for research only."""

from __future__ import annotations

from statistics import mean, pstdev

from backtest import BUY_FEE, SELL_FEE, SLIPPAGE_BPS, STOCK_SELL_TAX


def run_variant(history, fast=20, slow=60, count=5, holding=5, vol_weight=0.0, volume_weight=0.0, market_filter=True):
    returns = []
    index = max(slow, 20)
    while index + holding + 1 < len(history):
        today = history[index]
        entry = history[index + 1]
        exit_ = history[index + holding + 1]
        market = [row.get("0050", (None, 0))[0] for row in history[index - slow + 1:index + 1]]
        market = [value for value in market if value]
        if market_filter and (len(market) < slow or market[-1] < mean(market)):
            index += holding
            continue
        ranked = []
        for code, (price, volume) in today.items():
            fast_row = history[index - fast].get(code)
            slow_row = history[index - slow].get(code)
            entry_row = entry.get(code)
            exit_row = exit_.get(code)
            if not (fast_row and slow_row and entry_row and exit_row and volume >= 500_000 and price >= 10):
                continue
            trail = [row.get(code, (None, 0))[0] for row in history[index - 19:index + 1]]
            if any(value is None or value <= 0 for value in trail):
                continue
            daily_returns = [trail[pos] / trail[pos - 1] - 1 for pos in range(1, len(trail))]
            volatility = pstdev(daily_returns)
            volume_trail = [row.get(code, (0, 0))[1] for row in history[index - 19:index + 1]]
            average_volume = mean(volume_trail) if volume_trail else volume
            volume_ratio = volume / average_volume if average_volume else 1
            fast_momentum = price / fast_row[0] - 1
            slow_momentum = price / slow_row[0] - 1
            score = 0.45 * fast_momentum + 0.55 * slow_momentum - vol_weight * volatility + volume_weight * (volume_ratio - 1)
            if slow_momentum > -0.25:
                ranked.append((score, entry_row[0], exit_row[0]))
        picks = sorted(ranked, reverse=True)[:count]
        if picks:
            gross = mean(exit_price / entry_price - 1 for _, entry_price, exit_price in picks)
            net = (1 + gross) * (1 - BUY_FEE - SLIPPAGE_BPS / 10_000) * (1 - SELL_FEE - STOCK_SELL_TAX - SLIPPAGE_BPS / 10_000) - 1
            returns.append(net)
        index += holding
    equity = 1.0
    for value in returns:
        equity *= 1 + value
    return equity - 1, len(returns)
