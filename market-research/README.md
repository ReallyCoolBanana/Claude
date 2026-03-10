# Market Research System

## Goal

Build collective AI expertise in stock picking across multiple analytical methods. Each agent team contributes research, logs picks, tracks performance, and iterates on strategies — creating a compounding knowledge base that improves over time.

## Analysis Methods Tracked

| Method | Description |
|--------|-------------|
| **Fundamental Analysis** | Valuation metrics (P/E, P/B, DCF), revenue growth, competitive moats, balance sheet health |
| **Technical Analysis** | Price action, moving averages, RSI, MACD, support/resistance levels, chart patterns |
| **Sentiment Analysis** | News sentiment, social media signals, insider trading activity, analyst revisions |
| **Quantitative / Statistical** | Factor models, statistical arbitrage, mean reversion, momentum strategies, backtesting |
| **Macro / Sector Analysis** | Economic indicators, sector rotation, interest rate environment, geopolitical factors |

## How It Works

### Logging Picks

1. Copy `picks/TEMPLATE.md` to a new file named `picks/PICK-XXXX.md`.
2. Fill in the thesis, analysis, and exit criteria.
3. Update `picks/index.json` to register the pick.

### Tracking Performance

- Each pick has a status: `active`, `closed`, or `watchlist`.
- When a pick is closed, record the actual return and lessons learned in the Outcome section.
- `picks/index.json` aggregates performance statistics (win rate, average return).
- `watchlist.json` tracks stocks and themes under observation but not yet picked.

### Iterating on Strategies

- Closed picks generate knowledge entries that feed back into method refinement.
- The `methods/` directory contains living guides that are updated as we learn what works.
- Performance data reveals which methods and conditions produce the best results.

## The Prime Directive

Each team builds on the work of the last. When making a pick or updating a method guide:

- Reference prior picks and KB entries that informed your analysis (`builds_on` field).
- Note what previous teams got right or wrong.
- Improve the method templates based on accumulated evidence.
- The system gets smarter with every pick, every outcome, every iteration.

## Directory Structure

```
market-research/
  README.md              — This file
  watchlist.json         — Stocks and themes under observation
  methods/
    README.md            — Index of analysis methods
    fundamental.md       — Fundamental analysis guide
    technical.md         — Technical analysis guide
    sentiment.md         — Sentiment analysis guide
    quantitative.md      — Quantitative methods guide
  picks/
    index.json           — Pick registry and performance summary
    TEMPLATE.md          — Template for new picks
    PICK-XXXX.md         — Individual pick entries
```
