import os
import time
import threading
from datetime import datetime, timezone, timedelta
import requests
from flask import Flask, jsonify

# ================= CONFIG (all via ENV) =================
TELEGRAM_TOKEN    = os.getenv("TELEGRAM_TOKEN")           # required
TELEGRAM_CHAT_ID  = os.getenv("TELEGRAM_CHAT_ID")         # required
ALPHA_VANTAGE_KEY = os.getenv("ALPHA_VANTAGE_KEY")        # required

# Polling cadence (seconds) - free API has limits
POLL_SECONDS = int(os.getenv("POLL_SECONDS", "60"))

# Consider only articles from the last N hours
HOURS_WINDOW = int(os.getenv("HOURS_WINDOW", "6"))

# "Strong news" filters
# Absolute ticker_sentiment_score must be >= STRONG_SCORE (0..1)
STRONG_SCORE = float(os.getenv("STRONG_SCORE", "0.35"))
# Per-article relevance of that ticker must be >= MIN_TICKER_RELEVANCE (0..1)
MIN_TICKER_RELEVANCE = float(os.getenv("MIN_TICKER_RELEVANCE", "0.50"))

# Optional filter by topics (comma-separated per Alpha Vantage docs)
# Examples: earnings,financial_markets,technology,mergers_and_acquisitions,analyst_ratings,iponews,legal
TOPICS = os.getenv(
    "TOPICS",
    "earnings,financial_markets,mergers_and_acquisitions,analyst_ratings,legal",
)

# Extra keyword boost (found in headline -> mention reason)
KEYWORD_BOOST = [
    k.strip().lower()
    for k in os.getenv(
        "KEYWORD_BOOST",
        "earnings,results,guidance,acquires,merger,acquisition,sec,investigation,bankruptcy,ceo,resigns,forecast,upgrade,downgrade,beats,misses,raises,cuts",
    ).split(",")
    if k.strip()
]

# TP/SL percentages (for long; for short we invert automaticamente)
TP_PCT = float(os.getenv("TP_PCT", "0.005"))  # +0.5%
SL_PCT = float(os.getenv("SL_PCT", "0.003"))  # -0.3%

# Dedup store: (time_published|title|ticker) already emitted
emitted = set()

app = Flask(__name__)

# ================= Utilities =================
def tg_send(text: str):
    if not TELEGRAM_TOKEN or not TELEGRAM_CHAT_ID:
        print("[WARN] Telegram env not set. Message:", text[:200])
        return
    try:
        url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
        requests.post(url, json={"chat_id": TELEGRAM_CHAT_ID, "text": text}, timeout=10)
    except Exception as e:
        print("[ERROR] Telegram send failed:", e)

def parse_time_published(tp: str):
    # Alpha Vantage: "YYYYMMDDTHHMMSS" (UTC)
    try:
        return datetime.strptime(tp, "%Y%m%dT%H%M%S").replace(tzinfo=timezone.utc)
    except Exception:
        return None

def fmt_price(p: float) -> str:
    if p is None:
        return "n/d"
    if p < 1:
        return f"{p:.6f}"
    if p < 10:
        return f"{p:.4f}"
    return f"{p:.2f}"

def get_realtime_price(symbol: str) -> float | None:
    """Try GLOBAL_QUOTE first, then fallback to INTRADAY 1min."""
    base = "https://www.alphavantage.co/query"

    # GLOBAL_QUOTE
    try:
        r = requests.get(
            base,
            params={"function": "GLOBAL_QUOTE", "symbol": symbol, "apikey": ALPHA_VANTAGE_KEY},
            timeout=12,
        )
        r.raise_for_status()
        j = r.json()
        price_str = j.get("Global Quote", {}).get("05. price")
        if price_str:
            return float(price_str)
    except Exception:
        pass

    # INTRADAY 1min fallback
    try:
        r = requests.get(
            base,
            params={
                "function": "TIME_SERIES_INTRADAY",
                "symbol": symbol,
                "interval": "1min",
                "outputsize": "compact",
                "apikey": ALPHA_VANTAGE_KEY,
            },
            timeout=12,
        )
        r.raise_for_status()
        j = r.json()
        ts = j.get("Time Series (1min)") or {}
        if ts:
            latest_ts = sorted(ts.keys())[-1]
            last_close = ts[latest_ts].get("4. close")
            if last_close:
                return float(last_close)
    except Exception:
        pass

    return None

def fetch_latest_news():
    base = "https://www.alphavantage.co/query"
    params = {
        "function": "NEWS_SENTIMENT",
        "sort": "LATEST",
        "limit": "200",
        "apikey": ALPHA_VANTAGE_KEY,
    }
    if TOPICS:
        params["topics"] = TOPICS
    r = requests.get(base, params=params, timeout=20)
    r.raise_for_status()
    return r.json()

def strong_news_signals(feed):
    """Yield dicts with strong signals extracted from the feed."""
    cutoff = datetime.now(timezone.utc) - timedelta(hours=HOURS_WINDOW)
    for item in feed.get("feed", []):
        tp = parse_time_published(item.get("time_published", ""))
        if not tp or tp < cutoff:
            continue
        title = (item.get("title") or "").strip()

        for ts in item.get("ticker_sentiment", []):
            try:
                sym = (ts.get("ticker") or "").upper()
                score = float(ts.get("ticker_sentiment_score", 0.0))
                label = (ts.get("ticker_sentiment_label") or "").upper()  # BULLISH / BEARISH / NEUTRAL
                rel = float(ts.get("relevance_score", 0.0))
            except Exception:
                continue

            if abs(score) < STRONG_SCORE or rel < MIN_TICKER_RELEVANCE:
                continue

            key = f"{item.get('time_published')}|{title}|{sym}"
            if key in emitted:
                continue

            # Direction & price levels
            if label == "BULLISH":
                action = "BUY"
                arrow = "📈 Compra"
            elif label == "BEARISH":
                action = "SELL"
                arrow = "📉 Vendi"
            else:
                continue

            price = get_realtime_price(sym)

            if price is not None:
                if action == "BUY":
                    tp = price * (1 + TP_PCT)
                    sl = price * (1 - SL_PCT)
                else:  # SELL (short)
                    tp = price * (1 - TP_PCT)
                    sl = price * (1 + SL_PCT)
                price_line = f"• Prezzo: {fmt_price(price)} | TP: {fmt_price(tp)} (+{TP_PCT*100:.1f}%) | SL: {fmt_price(sl)} (-{SL_PCT*100:.1f}%)"
            else:
                price_line = "• Prezzo: n/d (limite API) — TP/SL non calcolati"

            # Keyword reason
            low_title = title.lower()
            matched = [k for k in KEYWORD_BOOST if k in low_title]

            reason_lines = [
                f"{arrow} {sym}",
                f"• Titolo: {title}",
                f"• Pubblicata (UTC): {tp.strftime('%Y-%m-%d %H:%M:%S')}",
                f"• Sentiment ticker: {score:+.2f} ({label}), Rilevanza: {rel:.2f}",
                price_line,
            ]
            if matched:
                reason_lines.append("• Parole chiave: " + ", ".join(sorted(set(matched))))
            reason_lines.append(
                "• Regola: |score| ≥ {:.2f} e relevance ≥ {:.2f} ⇒ segnale su news forte".format(
                    STRONG_SCORE, MIN_TICKER_RELEVANCE
                )
            )

            yield {
                "key": key,
                "symbol": sym,
                "action": action,
                "text": "📢 **News forte rilevata**\n" + "\n".join(reason_lines),
            }

# ================= Worker =================
def worker():
    if not ALPHA_VANTAGE_KEY:
        tg_send("⚠️ Manca ALPHA_VANTAGE_KEY: imposta la tua chiave su Render (Environment).")
        return
    if not TELEGRAM_TOKEN or not TELEGRAM_CHAT_ID:
        print("[WARN] Telegram non configurato: imposta TELEGRAM_TOKEN e TELEGRAM_CHAT_ID.")
    tg_send(
        "🤖 Bot NEWS-TRADING (solo segnali, qualsiasi titolo) avviato.\n"
        f"Filtri: |score|≥{STRONG_SCORE}, relevance≥{MIN_TICKER_RELEVANCE}, window {HOURS_WINDOW}h.\n"
        f"TP: +{TP_PCT*100:.1f}%  |  SL: -{SL_PCT*100:.1f}%"
    )

    while True:
        try:
            feed = fetch_latest_news()
            for sig in strong_news_signals(feed):
                tg_send(sig["text"])
                emitted.add(sig["key"])
        except Exception as e:
            tg_send(f"⚠️ Errore ciclo bot: {e}")
        time.sleep(POLL_SECONDS)

# ================= Flask (health) =================
@app.route("/")
def root():
    return jsonify({"ok": True, "service": "strong-news-signals-any-ticker"})

@app.route("/health")
def health():
    return jsonify({"ok": True})

if __name__ == "__main__":
    threading.Thread(target=worker, daemon=True).start()
    port = int(os.getenv("PORT", "10000"))
    app.run(host="0.0.0.0", port=port)
