import os
import time
import threading
from datetime import datetime, timezone, timedelta
import requests
from flask import Flask, jsonify

# ================= CONFIG (ENV tolerant) =================
def _get_env(*names):
    for n in names:
        v = os.getenv(n)
        if v:
            return v
    return None

TELEGRAM_TOKEN = _get_env("TELEGRAM_TOKEN", "TELEGRAM_BOT_TOKEN", "TG_BOT_TOKEN", "telegram_token")
TELEGRAM_CHAT_ID = _get_env("TELEGRAM_CHAT_ID", "TG_CHAT_ID", "telegram_chat_id")
ALPHA_VANTAGE_KEY = _get_env("ALPHA_VANTAGE_KEY", "ALPHAVANTAGE_API_KEY", "ALPHA_VANTAGE_API_KEY", "alphavantage_api_key")

POLL_SECONDS = int(os.getenv("POLL_SECONDS", "60"))
HOURS_WINDOW = int(os.getenv("HOURS_WINDOW", "6"))
STRONG_SCORE = float(os.getenv("STRONG_SCORE", "0.35"))
MIN_TICKER_RELEVANCE = float(os.getenv("MIN_TICKER_RELEVANCE", "0.50"))
TOPICS = os.getenv("TOPICS", "earnings,financial_markets,mergers_and_acquisitions,analyst_ratings,legal")
KEYWORD_BOOST = [k.strip().lower() for k in os.getenv(
    "KEYWORD_BOOST",
    "earnings,results,guidance,acquires,merger,acquisition,sec,investigation,bankruptcy,ceo,resigns,forecast,upgrade,downgrade,beats,misses,raises,cuts"
).split(",") if k.strip()]
TP_PCT = float(os.getenv("TP_PCT", "0.005"))  # +0.5%
SL_PCT = float(os.getenv("SL_PCT", "0.003"))  # -0.3%

emitted = set()
app = Flask(__name__)

# ================= Utils =================
def _mask(s, keep=4):
    if not s:
        return "None"
    s = str(s)
    if len(s) <= keep * 2:
        return "*" * len(s)
    return s[:keep] + "…" + s[-keep:]

print("[ENV] TELEGRAM_TOKEN:", _mask(TELEGRAM_TOKEN))
print("[ENV] TELEGRAM_CHAT_ID:", TELEGRAM_CHAT_ID or "None")
print("[ENV] ALPHA_VANTAGE_KEY:", _mask(ALPHA_VANTAGE_KEY))

def tg_send(text: str):
    if not TELEGRAM_TOKEN or not TELEGRAM_CHAT_ID:
        print("[WARN] Telegram non configurato. Messaggio:", text[:200])
        return
    try:
        url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
        requests.post(url, json={"chat_id": TELEGRAM_CHAT_ID, "text": text}, timeout=10)
    except Exception as e:
        print("[ERROR] Telegram send failed:", e)

def parse_time_published(tp: str):
    try:
        return datetime.strptime(tp, "%Y%m%dT%H%M%S").replace(tzinfo=timezone.utc)
    except Exception:
        return None

def fmt_price(p: float) -> str:
    if p is None: return "n/d"
    if p < 1:  return f"{p:.6f}"
    if p < 10: return f"{p:.4f}"
    return f"{p:.2f}"

def get_realtime_price(symbol: str):
    base = "https://www.alphavantage.co/query"
    # GLOBAL_QUOTE
    try:
        r = requests.get(base, params={"function":"GLOBAL_QUOTE","symbol":symbol,"apikey":ALPHA_VANTAGE_KEY}, timeout=12)
        r.raise_for_status()
        price_str = r.json().get("Global Quote", {}).get("05. price")
        if price_str: return float(price_str)
    except Exception:
        pass
    # fallback INTRADAY 1m
    try:
        r = requests.get(base, params={
            "function":"TIME_SERIES_INTRADAY","symbol":symbol,"interval":"1min","outputsize":"compact","apikey":ALPHA_VANTAGE_KEY
        }, timeout=12)
        r.raise_for_status()
        ts = r.json().get("Time Series (1min)") or {}
        if ts:
            latest_ts = sorted(ts.keys())[-1]
            last_close = ts[latest_ts].get("4. close")
            if last_close: return float(last_close)
    except Exception:
        pass
    return None

def fetch_latest_news():
    base = "https://www.alphavantage.co/query"
    params = {"function":"NEWS_SENTIMENT","sort":"LATEST","limit":"200","apikey":ALPHA_VANTAGE_KEY}
    if TOPICS: params["topics"] = TOPICS
    r = requests.get(base, params=params, timeout=20)
    r.raise_for_status()
    return r.json()

def strong_news_signals(feed):
    cutoff = datetime.now(timezone.utc) - timedelta(hours=HOURS_WINDOW)
    for item in feed.get("feed", []):
        tp = parse_time_published(item.get("time_published",""))
        if not tp or tp < cutoff: continue
        title = (item.get("title") or "").strip()

        for ts in item.get("ticker_sentiment", []):
            try:
                sym = (ts.get("ticker") or "").upper()
                score = float(ts.get("ticker_sentiment_score", 0.0))
                label = (ts.get("ticker_sentiment_label") or "").upper()
                rel = float(ts.get("relevance_score", 0.0))
            except Exception:
                continue

            if abs(score) < STRONG_SCORE or rel < MIN_TICKER_RELEVANCE:
                continue

            key = f"{item.get('time_published')}|{title}|{sym}"
            if key in emitted: continue

            if label == "BULLISH":
                action, arrow = "BUY", "📈 Compra"
            elif label == "BEARISH":
                action, arrow = "SELL", "📉 Vendi"
            else:
                continue

            price = get_realtime_price(sym)
            if price is not None:
                if action == "BUY":
                    tpv = price * (1 + TP_PCT)
                    slv = price * (1 - SL_PCT)
                else:
                    tpv = price * (1 - TP_PCT)
                    slv = price * (1 + SL_PCT)
                price_line = f"• Prezzo: {fmt_price(price)} | TP: {fmt_price(tpv)} (+{TP_PCT*100:.1f}%) | SL: {fmt_price(slv)} (-{SL_PCT*100:.1f}%)"
            else:
                price_line = "• Prezzo: n/d (limite API) — TP/SL non calcolati"

            matched = [k for k in KEYWORD_BOOST if k in title.lower()]
            reason = [
                f"{arrow} {sym}",
                f"• Titolo: {title}",
                f"• Pubblicata (UTC): {tp.strftime('%Y-%m-%d %H:%M:%S')}",
                f"• Sentiment ticker: {score:+.2f} ({label}), Rilevanza: {rel:.2f}",
                price_line,
            ]
            if matched:
                reason.append("• Parole chiave: " + ", ".join(sorted(set(matched))))
            reason.append(f"• Regola: |score| ≥ {STRONG_SCORE:.2f} e relevance ≥ {MIN_TICKER_RELEVANCE:.2f} ⇒ news forte")

            yield {"key": key, "text": "📢 **News forte rilevata**\n" + "\n".join(reason)}

# ================= Worker =================
def worker():
    if not ALPHA_VANTAGE_KEY:
        tg_send("⚠️ Manca ALPHA_VANTAGE_KEY nelle Environment Variables.")
        return
    tg_send(
        "🤖 Bot NEWS-TRADING (solo segnali) avviato.\n"
        f"Filtri: |score|≥{STRONG_SCORE}, relevance≥{MIN_TICKER_RELEVANCE}, window {HOURS_WINDOW}h.\n"
        f"TP: +{TP_PCT*100:.1f}%  |  SL: -{SL_PCT*100:.1f}%"
    )
    while True:
        try:
            feed = fetch_latest_news()
            for sig in strong_news_signals(feed):
                tg_send(sig["text"]); emitted.add(sig["key"])
        except Exception as e:
            tg_send(f"⚠️ Errore ciclo bot: {e}")
        time.sleep(POLL_SECONDS)

# ================= Flask (health) =================
app = Flask(__name__)

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
