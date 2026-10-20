# Oracle 🔮

Bot Telegram care trimite rezumate simple ale știrilor bursiere pentru un watchlist.

## 1. Află-ți chat ID-ul
1. Trimite orice mesaj botului tău în Telegram.
2. Deschide în browser: `https://api.telegram.org/bot<TOKEN>/getUpdates`
3. Caută `"chat":{"id":123456789` — numărul ăla e `TELEGRAM_CHAT_ID`.

## 2. Test local
```
pip install requests
copy .env.example .env      (Windows)  — apoi completează cheile în .env
python oracle.py --test     -> trebuie să primești "Salut! Oracle e online."
python oracle.py            -> prima rulare: marchează știrile existente
python oracle.py            -> de acum trimite doar știrile noi
```
Dacă testul local merge, șterge `data/seen.json` înainte de push, ca GitHub să pornească curat.

## 3. GitHub Actions
1. Repo nou pe GitHub (public = minute nelimitate). Urcă toate fișierele, **fără `.env`**.
2. Settings → Secrets and variables → Actions → New repository secret, câte unul:
   `FINNHUB_API_KEY`, `GEMINI_API_KEY`, `TELEGRAM_TOKEN`, `TELEGRAM_CHAT_ID` (opțional `GROQ_API_KEY`).
3. Tab-ul Actions → Oracle → Run workflow. Primești mesajul de pornire.
4. De acum rulează singur în fiecare oră.

## Personalizare
- Companii: `WATCHLIST` în `oracle.py`.
- AI backup: setează `AI_PROVIDER=groq` (în `.env` sau ca env în workflow).
- Predicțiile se salvează în `data/predictions.csv` — peste 1–2 luni le verificăm cu prețurile reale.
