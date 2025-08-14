# News Trading Signals Bot (Any Ticker) – TP/SL – ENV tolerant

- Telegram alerts (no auto-trading)
- Alpha Vantage News Sentiment feed
- Strong news filters + real-time price for TP/SL
- ENV tolerant: accepts multiple variable names and logs masked values on boot
- Works on Render Free/Starter as a Web Service

## Files
- app.py
- requirements.txt
- render.yaml
- README.md
- .env.example (local dev only)

## Environment variables (Render → Settings → Environment)
- TELEGRAM_TOKEN or TELEGRAM_BOT_TOKEN (required)
- TELEGRAM_CHAT_ID or TG_CHAT_ID (required)
- ALPHA_VANTAGE_KEY or ALPHAVANTAGE_API_KEY (required)
- Optional: POLL_SECONDS, HOURS_WINDOW, STRONG_SCORE, MIN_TICKER_RELEVANCE, TOPICS, KEYWORD_BOOST, TP_PCT, SL_PCT

Start Command: `python app.py`
