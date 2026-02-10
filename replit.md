# replit.md

## Overview

This is a **Telegram Bot** project that serves two main purposes:

1. **Football Betting Analysis Bot** — Predicts football match outcomes (Over/Under goals, BTTS, match result) based on the LSE paper "A Profitable Model For Predicting the Over/Under Market in Football." It scrapes match data from Forebet and uses xG (expected goals) statistics with Poisson distribution modeling.

2. **Forex Trading Signal Bot** — Monitors forex currency pairs in real-time, performs technical analysis (RSI, EMA, MACD, candlestick patterns), and sends trade signals via Telegram with entry/exit prices, take profit, and stop loss levels.

Both features are delivered through a single Telegram bot with different command handlers. The project runs as a Python application on Replit.

## User Preferences

Preferred communication style: Simple, everyday language.

## System Architecture

### Runtime & Language
- **Python 3.11** is the primary language
- **Flask** is included but primarily used as a lightweight HTTP server (likely for webhook or keep-alive pinging, not as the main web framework)
- The core application logic runs as an **async Telegram bot** using `python-telegram-bot` v20.7

### Bot Architecture
- **Single `main.py` entry point** containing all bot logic — command handlers, analysis functions, data fetching, and the Telegram bot setup
- Uses `python-telegram-bot` async framework with polling or webhook mode
- Known issue: event loop conflicts (`RuntimeError: This event loop is already running`) when mixing sync Flask with async telegram bot — use `nest-asyncio` or restructure to pure async

### Data Flow
- **Football predictions**: Scrapes Forebet.com for mathematical predictions using `requests` + `BeautifulSoup4`. Handles Brotli-compressed responses. Falls back to manual stat input via conversational Telegram interface
- **Forex signals**: Fetches candlestick data from **TwelveData API** (`api.twelvedata.com`), calculates technical indicators (RSI, EMA, MACD), detects candlestick patterns, and generates BUY/SELL signals
- Analysis uses `pandas`, `numpy`, `scipy` for statistical calculations and `scikit-learn` for ML models

### Data Storage
- **JSON file-based storage** — no database currently
  - `bot_settings.json` / `enhanced_bot_settings.json`: Bot configuration (monitoring intervals, currency pairs, subscribed users)
  - `alerts.json`: User price alerts for forex pairs
  - `performance_log.json`: Trade signal history and outcomes
- **In-memory dictionaries** for team data, fixtures, and user conversation state
- Future improvement: migrate to SQLite or PostgreSQL for prediction logs and user data

### Configuration & Settings
- `bot_settings.json`: Basic forex monitoring settings (interval, timeframe, pairs, subscribers)
- `enhanced_bot_settings.json`: Advanced settings with market hours enforcement, pattern confidence thresholds, and feedback system
- Monitored forex pairs include major pairs: GBP/USD, EUR/USD, USD/JPY, USD/CHF, AUD/USD, NZD/USD, USD/CAD

### Key Design Patterns
- **Conversational state machine** for multi-step user input (collecting team stats one by one)
- **Batch processing** for forex pair scanning (processes pairs in groups to respect API rate limits)
- **Fallback/mock data** when APIs are unavailable or rate-limited
- **Market hours awareness** — only sends forex signals during active trading sessions (London, New York, Tokyo, Sydney)

### Known Technical Issues
- Async event loop conflicts between Flask and python-telegram-bot need careful handling
- TwelveData API key must be properly configured as a Replit Secret (currently shows 401 errors)
- Forebet scraping encounters Brotli decompression issues occasionally
- Multiple cached HTML files in the repo root are debug artifacts and can be ignored

## External Dependencies

### APIs & Services
- **Telegram Bot API** — Primary user interface. Requires `TELEGRAM_BOT_TOKEN` as an environment secret
- **TwelveData API** (`api.twelvedata.com`) — Forex price data and candlestick history. Requires `TWELVEDATA_API_KEY` as an environment secret. Free tier has rate limits (8 requests/minute)
- **Forebet.com** — Web scraping target for football match predictions. No API key needed but requires anti-ban measures (rate limiting, proper headers, user-agent rotation)

### Python Libraries
- `python-telegram-bot==20.7` — Telegram bot framework (async)
- `pandas`, `numpy` — Data manipulation and numerical analysis
- `scikit-learn` — Machine learning models for prediction
- `scipy` — Statistical distributions (Poisson for goal modeling)
- `requests`, `aiohttp` — HTTP clients for API calls and scraping
- `beautifulsoup4` — HTML parsing for Forebet scraping
- `selenium`, `webdriver-manager` — Browser automation fallback for scraping (if requests fails)
- `brotli` — Decompressing Brotli-encoded web responses
- `yfinance` — Alternative financial data source
- `nest-asyncio` — Fixes nested event loop issues
- `flask` — Lightweight HTTP server

### Environment Secrets Required
- `TELEGRAM_BOT_TOKEN` — Telegram Bot API token
- `TWELVEDATA_API_KEY` — TwelveData forex data API key