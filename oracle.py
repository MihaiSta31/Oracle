"""Oracle — bot Telegram care trimite rezumate simple ale știrilor bursiere.

Rulare:
    python oracle.py --test   -> trimite un mesaj de test pe Telegram
    python oracle.py          -> verifică știrile noi și le trimite
"""
import csv
import json
import os
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import requests

# ---------------- Configurare ----------------
WATCHLIST = ["AAPL", "NVDA", "TSLA", "MSFT", "AMZN"]  # companiile urmărite
MAX_NEWS_PER_RUN = 15                                 # câte știri trimitem la AI pe rulare
AI_PROVIDER_DEFAULT = "gemini"                        # "gemini" sau "groq"

BASE = Path(__file__).parent
SEEN_FILE = BASE / "data" / "seen.json"
LOG_FILE = BASE / "data" / "predictions.csv"

PROMPT = """Ești Oracle, un asistent care explică știri bursiere unui începător, în limba română simplă.
Primești o listă de știri în format JSON. Pentru FIECARE știre întoarce un obiect cu câmpurile:
- "id": același id primit
- "relevant": true doar dacă știrea poate influența prețul acțiunii (false pentru reclame, liste "top 10", articole generice sau repetate)
- "ce_s_a_intamplat": o singură propoziție scurtă, fără jargon
- "impact": "crestere", "scadere" sau "incert"
- "incredere": "scazuta", "medie" sau "ridicata"
- "termen": "ore", "zile" sau "saptamani"
- "termen_nou": un termen financiar din știre explicat simplu, în formatul "termen = explicație", sau "" dacă nu există
Fii prudent: dacă efectul nu e clar, pune impact "incert" și încredere "scazuta".
Răspunde DOAR cu o listă JSON, fără alt text."""

ARROW = {"crestere": "⬆️ creștere", "scadere": "⬇️ scădere", "incert": "↔️ incert"}


# ---------------- Utilitare ----------------
def load_env():
    """Citește cheile din fișierul .env (doar la rulare locală)."""
    env_file = BASE / ".env"
    if env_file.exists():
        for line in env_file.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                key, value = line.split("=", 1)
                os.environ.setdefault(key.strip(), value.strip())


def load_seen():
    if SEEN_FILE.exists():
        return json.loads(SEEN_FILE.read_text(encoding="utf-8"))
    return []


def save_seen(seen):
    SEEN_FILE.parent.mkdir(exist_ok=True)
    SEEN_FILE.write_text(json.dumps(seen[-3000:]), encoding="utf-8")


# ---------------- Știri ----------------
def fetch_news():
    today = datetime.now(timezone.utc).date()
    start = today - timedelta(days=1)
    items = []
    for ticker in WATCHLIST:
        try:
            r = requests.get(
                "https://finnhub.io/api/v1/company-news",
                params={"symbol": ticker, "from": start.isoformat(), "to": today.isoformat(),
                        "token": os.environ["FINNHUB_API_KEY"]},
                timeout=20,
            )
            r.raise_for_status()
            data = r.json()
        except requests.RequestException as e:
            print(f"[finnhub] {ticker} sărit: {e}")
            continue
        for n in data:
            items.append({
                "id": str(n["id"]),
                "ticker": ticker,
                "headline": n.get("headline", ""),
                "summary": (n.get("summary") or "")[:500],
                "url": n.get("url", ""),
                "datetime": n.get("datetime", 0),
            })
        time.sleep(1)  # politicos cu limita Finnhub
    return items


# ---------------- AI ----------------
def ask_gemini(text):
    model = os.getenv("GEMINI_MODEL", "gemini-flash-latest")
    r = requests.post(
        f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent",
        headers={"x-goog-api-key": os.environ["GEMINI_API_KEY"]},
        json={
            "contents": [{"parts": [{"text": PROMPT + "\n\nȘtiri:\n" + text}]}],
            "generationConfig": {"responseMimeType": "application/json", "temperature": 0.2},
        },
        timeout=60,
    )
    r.raise_for_status()
    return r.json()["candidates"][0]["content"]["parts"][0]["text"]


def ask_groq(text):
    model = os.getenv("GROQ_MODEL", "llama-3.3-70b-versatile")
    r = requests.post(
        "https://api.groq.com/openai/v1/chat/completions",
        headers={"Authorization": f"Bearer {os.environ['GROQ_API_KEY']}"},
        json={
            "model": model,
            "temperature": 0.2,
            "messages": [{"role": "system", "content": PROMPT},
                         {"role": "user", "content": "Știri:\n" + text}],
        },
        timeout=60,
    )
    r.raise_for_status()
    return r.json()["choices"][0]["message"]["content"]


def _parse(raw):
    start, end = raw.find("["), raw.rfind("]")
    if start == -1 or end == -1:
        raise ValueError(f"Răspunsul AI nu conține o listă JSON:\n{raw[:500]}")
    return json.loads(raw[start:end + 1])


def ask_ai(text):
    """Încearcă providerul principal cu retry, apoi celălalt ca fallback.
    Întoarce None dacă toate au eșuat (rularea nu mai pică)."""
    primary = os.getenv("AI_PROVIDER", AI_PROVIDER_DEFAULT).lower()
    order = ["groq", "gemini"] if primary == "groq" else ["gemini", "groq"]
    funcs = {"gemini": (ask_gemini, "GEMINI_API_KEY"), "groq": (ask_groq, "GROQ_API_KEY")}

    for name in order:
        fn, key = funcs[name]
        if not os.getenv(key):
            continue
        for attempt in range(3):
            try:
                return _parse(fn(text))
            except requests.HTTPError as e:
                code = e.response.status_code if e.response is not None else None
                body = e.response.text[:300] if e.response is not None else ""
                print(f"[{name}] HTTP {code} (încercarea {attempt + 1}): {body}")
                if code not in (429, 500, 502, 503, 504):
                    break  # eroare permanentă (cheie greșită, model inexistent) -> trecem la fallback
            except (requests.Timeout, requests.ConnectionError, ValueError, KeyError) as e:
                print(f"[{name}] {type(e).__name__} (încercarea {attempt + 1}): {e}")
            time.sleep(10 * (attempt + 1))
    return None


# ---------------- Telegram ----------------
def send(text):
    r = requests.post(
        f"https://api.telegram.org/bot{os.environ['TELEGRAM_TOKEN']}/sendMessage",
        json={"chat_id": os.environ["TELEGRAM_CHAT_ID"], "text": text,
              "disable_web_page_preview": True},
        timeout=20,
    )
    r.raise_for_status()


def format_message(news, a):
    lines = [
        f"📰 {news['ticker']}",
        f"Ce s-a întâmplat: {a.get('ce_s_a_intamplat', news['headline'])}",
        f"Ce poate însemna: {ARROW.get(a.get('impact'), '↔️ incert')}",
        f"Încredere: {a.get('incredere', '?')} · Termen: {a.get('termen', '?')}",
    ]
    if a.get("termen_nou"):
        lines.append(f"📚 {a['termen_nou']}")
    lines.append(news["url"])
    return "\n".join(lines)


# ---------------- Logging pentru evaluare ----------------
def log_predictions(rows):
    if not rows:
        return
    LOG_FILE.parent.mkdir(exist_ok=True)
    is_new = not LOG_FILE.exists()
    with LOG_FILE.open("a", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        if is_new:
            w.writerow(["news_time_utc", "ticker", "impact", "incredere", "termen", "headline", "url"])
        for n, a in rows:
            t = datetime.fromtimestamp(n["datetime"], tz=timezone.utc).isoformat()
            w.writerow([t, n["ticker"], a.get("impact"), a.get("incredere"), a.get("termen"),
                        n["headline"], n["url"]])


# ---------------- Main ----------------
def main():
    load_env()

    if "--test" in sys.argv:
        send("Salut! Oracle e online. 🔮")
        print("Mesaj de test trimis.")
        return

    first_run = not SEEN_FILE.exists()
    seen = load_seen()
    seen_set = set(seen)

    new = {}
    for n in fetch_news():
        if n["id"] not in seen_set and n["id"] not in new:
            new[n["id"]] = n

    if first_run:
        # Prima rulare: nu trimitem tot istoricul, doar marcăm știrile existente.
        save_seen(seen + list(new))
        send("🔮 Oracle a pornit. De acum primești doar știrile noi.")
        print(f"Inițializat cu {len(new)} știri marcate ca văzute.")
        return

    if not new:
        print("Nicio știre nouă.")
        return

    batch = sorted(new.values(), key=lambda n: n["datetime"])[-MAX_NEWS_PER_RUN:]
    payload = json.dumps(
        [{"id": n["id"], "ticker": n["ticker"], "titlu": n["headline"], "rezumat": n["summary"]}
         for n in batch],
        ensure_ascii=False,
    )
    result = ask_ai(payload)
    if result is None:
        # AI indisponibil: nu marcăm știrile ca văzute, le reîncercăm la rularea următoare.
        print("AI indisponibil pe toți providerii. Reîncerc la următoarea rulare.")
        return
    analyses = {str(a.get("id")): a for a in result}

    sent = []
    for n in batch:
        a = analyses.get(n["id"])
        if a and a.get("relevant"):
            send(format_message(n, a))
            sent.append((n, a))
            time.sleep(1)

    log_predictions(sent)
    save_seen(seen + list(new))
    print(f"{len(new)} știri noi, {len(sent)} trimise.")


if __name__ == "__main__":
    main()
