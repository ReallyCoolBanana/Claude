# Market Research System

## Goal

Build collective AI expertise in stock picking across multiple analytical methods. Each agent team contributes research, logs picks, tracks performance, and iterates on strategies. Over time, the system accumulates institutional knowledge about what works, what doesn't, and why.

## Analysis Methods Tracked

| Method | Description |
|--------|-------------|
| **Fundamental Analysis** | Valuation metrics (P/E, P/B, DCF), revenue/earnings growth, competitive moats, management quality, balance sheet health |
| **Technical Analysis** | Price action, moving averages, RSI, MACD, support/resistance levels, volume patterns, chart formations |
| **Sentiment Analysis** | News sentiment scoring, social media signals, insider trading activity, analyst rating changes, earnings call tone |
| **Quantitative / Statistical** | Factor models, statistical arbitrage, mean reversion strategies, momentum factors, risk-adjusted return optimization |
| **Macro / Sector Analysis** | Interest rate environment, sector rotation, geopolitical risk, commodity cycles, regulatory landscape |

## How It Works

### Logging Picks

1. Copy `picks/TEMPLATE.md` to a new file named `picks/PICK-XXXX.md`.
2. Fill in the thesis, analysis, and exit criteria.
3. Update `picks/index.json` to register the new pick.

### Tracking Performance

- Each pick has a defined entry price, target price, and stop loss.
- When a pick is closed, the outcome section is filled in with actual return, lessons learned, and a link to any knowledge base entry generated.
- `picks/index.json` maintains aggregate performance statistics (win rate, average return).

### Iterating on Strategies

- After closing a pick, teams write up what worked and what didn't.
- Insights feed back into the method guides under `methods/`.
- The `watchlist.json` file tracks stocks and themes under observation before committing to a pick.

## The Prime Directive

Each team builds on the work of the last. When making a pick or refining a method:

- Reference prior picks and KB entries using `builds_on` links.
- Check what has already been tried for a given ticker or sector.
- Improve method templates based on accumulated outcomes.
- Never start from scratch when prior work exists.

## Directory Structure

```
market-research/
  README.md              -- This file
  watchlist.json         -- Stocks and themes under observation
  methods/
    README.md            -- Index of analysis methods
    fundamental.md       -- Fundamental analysis guide
    technical.md         -- Technical analysis guide
    sentiment.md         -- Sentiment analysis guide
    quantitative.md      -- Quantitative methods guide
  picks/
    index.json           -- Registry of all picks with performance stats
    TEMPLATE.md          -- Template for new pick entries
    PICK-XXXX.md         -- Individual pick files
```
