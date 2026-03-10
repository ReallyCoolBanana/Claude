# Quantitative Methods Guide

## Overview

Quantitative analysis applies mathematical and statistical models to identify mispricings, systematic edges, and risk-adjusted return opportunities. It relies on data rather than narrative, and on repeatable processes rather than one-off judgments.

## Factor Models

### Common Factors
| Factor | Description | Classic Metric |
|--------|-------------|---------------|
| **Value** | Cheap stocks outperform expensive ones | Book-to-market ratio, earnings yield |
| **Momentum** | Recent winners continue winning | 12-month return minus last month |
| **Size** | Small caps outperform large caps | Market capitalization |
| **Quality** | Profitable, stable companies outperform | ROE, earnings stability, low leverage |
| **Low Volatility** | Less volatile stocks offer better risk-adjusted returns | Historical standard deviation |
| **Growth** | High-growth companies outperform (in certain regimes) | Revenue/earnings growth rate |

### Multi-Factor Approach
1. Score each stock on multiple factors.
2. Combine scores using equal weighting or optimized weighting.
3. Rank the universe and go long top decile, short bottom decile (or long-only top quintile).
4. Rebalance at regular intervals (monthly or quarterly).

## Statistical Arbitrage

### Pairs Trading
1. Identify two historically correlated stocks (same sector, similar business).
2. Calculate the spread (price ratio or difference).
3. Compute z-score of current spread relative to historical mean.
4. Enter when spread diverges beyond threshold (e.g., z > 2): long the underperformer, short the outperformer.
5. Exit when spread reverts to mean (z approaches 0).

### Key Metrics
- **Correlation coefficient** -- How closely the pair moves together (want > 0.8)
- **Cointegration test** -- Engle-Granger or Johansen; confirms mean-reverting relationship
- **Half-life** -- How quickly the spread reverts; shorter = better for trading
- **Spread z-score** -- Standard deviations from mean; entry/exit signal

## Mean Reversion Strategies

### Concept
Prices that deviate significantly from their historical average tend to revert. This can apply to:
- Individual stock price relative to moving average
- Valuation multiples relative to historical range
- Sector performance relative to market
- Volatility relative to historical levels

### Implementation
1. Define the "fair value" or equilibrium (moving average, historical P/E, etc.).
2. Measure deviation from equilibrium.
3. Enter when deviation exceeds threshold.
4. Exit when price returns to equilibrium or opposite threshold.
5. Use stop losses for cases where mean reversion fails (regime change).

## Momentum Strategies

### Price Momentum
- Rank stocks by trailing 12-month return (excluding the most recent month).
- Go long the top performers, short (or avoid) the bottom performers.
- Rebalance monthly or quarterly.

### Earnings Momentum
- Track earnings revision trends (upward vs. downward revisions).
- Companies with accelerating positive revisions tend to outperform.
- Post-earnings-announcement drift: stocks continue moving in the direction of the earnings surprise.

## Risk Management

### Position Sizing
- **Kelly criterion** -- Optimal bet size based on edge and odds
- **Volatility targeting** -- Size positions inversely to their volatility
- **Equal risk contribution** -- Each position contributes equally to portfolio risk

### Portfolio Construction
- **Diversification** -- Spread across sectors, factors, and time
- **Correlation monitoring** -- Avoid concentration in correlated bets
- **Drawdown limits** -- Reduce exposure after predefined drawdown thresholds
- **Regime detection** -- Adjust strategy based on market environment (trending vs. mean-reverting)

## Checklist for a Quantitative Pick

- [ ] Define the quantitative signal or model being used
- [ ] Document the historical backtest results (win rate, Sharpe ratio, max drawdown)
- [ ] Specify the data sources and lookback period
- [ ] Confirm the signal is currently active (entry threshold met)
- [ ] Calculate position size based on risk model
- [ ] Set systematic exit rules (target, stop loss, time-based)
- [ ] Identify what could invalidate the model (regime change, structural break)
- [ ] Note any correlation with existing portfolio positions
