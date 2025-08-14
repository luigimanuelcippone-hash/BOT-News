# Strong News Signals Bot (Any Ticker) with TP/SL
- Telegram alerts only (no auto-trading)
- Alpha Vantage News Sentiment feed
- Strong news filters + real-time price for TP/SL
- Works on Render Free as a Web Service

## Env vars to set on Render
- TELEGRAM_TOKEN
- TELEGRAM_CHAT_ID
- ALPHA_VANTAGE_KEY
- (optional) POLL_SECONDS, HOURS_WINDOW, STRONG_SCORE, MIN_TICKER_RELEVANCE, TOPICS, KEYWORD_BOOST, TP_PCT, SL_PCT

**Start Command**: `python app.py`
