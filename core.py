"""Outils communs: Telegram, Claude, sources de news, calendrier, prix (Yahoo)."""
import os
import re
import json
import html
import time
import hashlib
import calendar
import datetime as dt
from urllib.parse import quote_plus
from zoneinfo import ZoneInfo

import requests
import feedparser

TZ = ZoneInfo(os.getenv("TZ_NAME", "America/Toronto"))
UTC = dt.timezone.utc
MODEL = os.getenv("CLAUDE_MODEL", "claude-haiku-5-5")
IMPACT_MIN = int(os.getenv("IMPACT_MIN", "2"))
MAX_PER_GROUP = int(os.getenv("MAX_PER_GROUP", "6"))
SEEN_FILE = "seen.json"
UA = {"User-Agent": "Mozilla/5.0 (GoldWatch)"}
SEP = "━━━━━━━━━━━━━━━"
JOURS = ["lun", "mar", "mer", "jeu", "ven", "sam", "dim"]
MOIS = ["jan", "fév", "mar", "avr", "mai", "juin", "juil", "août", "sep", "oct", "nov", "déc"]


def fr_date(d):
    return f"{JOURS[d.weekday()]} {d.day} {MOIS[d.month - 1]}"


def section(emoji, title, lines):
    return f"{SEP}\n{emoji} {title}\n" + "\n".join(lines)


def arrow(chg):
    return "🟩 ▲" if chg >= 0 else "🟥 ▼"


# ---------- Telegram / Claude / état ----------
def send_telegram(text):
    token, chat = os.environ["TELEGRAM_TOKEN"], os.environ["TELEGRAM_CHAT_ID"]
    chunks, cur = [], ""
    for block in text.split("\n\n"):          # coupe entre les sections, pas au milieu
        if len(cur) + len(block) + 2 > 3800 and cur:
            chunks.append(cur)
            cur = ""
        cur += ("\n\n" if cur else "") + block
    chunks.append(cur)
    for c in chunks:
        for i in range(0, len(c), 3900):
            for attempt in range(3):
                r = requests.post(f"https://api.telegram.org/bot{token}/sendMessage",
                                  json={"chat_id": chat, "text": c[i:i + 3900],
                                        "disable_web_page_preview": True}, timeout=30)
                if r.status_code == 429:   # limite de débit: on attend ce que Telegram demande
                    time.sleep(min(int(r.json().get("parameters", {}).get("retry_after", 5)), 30))
                    continue
                r.raise_for_status()
                break


def has_ai():
    return bool(os.getenv("ANTHROPIC_API_KEY"))


def claude(prompt, max_tokens=1500):
    key = os.getenv("ANTHROPIC_API_KEY")
    if not key:
        return None
    r = requests.post("https://api.anthropic.com/v1/messages",
                      headers={"x-api-key": key, "anthropic-version": "2023-06-01",
                               "content-type": "application/json"},
                      json={"model": MODEL, "max_tokens": max_tokens,
                            "messages": [{"role": "user", "content": prompt}]}, timeout=120)
    r.raise_for_status()
    return r.json()["content"][0]["text"]


def load_seen():
    try:
        with open(SEEN_FILE) as f:
            return json.load(f)
    except Exception:
        return []


def save_seen(seen):
    with open(SEEN_FILE, "w") as f:
        json.dump(seen[-3000:], f)


def uid(s):
    return hashlib.md5(s.lower().strip().encode()).hexdigest()[:16]


def clean_html(s):
    s = re.sub(r"<br\s*/?>", " ", s)
    s = re.sub(r"<[^>]+>", "", s)
    return re.sub(r"\s+", " ", html.unescape(s)).strip()


# ---------- prix (Yahoo Finance: indicatif, souvent retardé) ----------
def yahoo_bars(sym, interval, rng):
    last = None
    for attempt in range(3):
        try:
            r = requests.get(f"https://query1.finance.yahoo.com/v8/finance/chart/{sym}"
                             f"?interval={interval}&range={rng}", headers=UA, timeout=25)
            r.raise_for_status()
            res = r.json()["chart"]["result"][0]
            q = res["indicators"]["quote"][0]
            return [(dt.datetime.fromtimestamp(t, UTC), o, h, l, c)
                    for t, o, h, l, c in zip(res["timestamp"], q["open"], q["high"], q["low"], q["close"])
                    if None not in (o, h, l, c)]
        except Exception as e:
            last = e
            time.sleep(2 * (attempt + 1))
    raise last


# ---------- sources de news ----------
def gnews(query):
    return ("https://news.google.com/rss/search?q=" + quote_plus(query + " when:1d")
            + "&hl=en-US&gl=US&ceid=US:en")


# (groupe, nom, type, url)
SOURCES = [
    ("squawk", "First Squawk", "telegram", "https://t.me/s/firstsquaw"),
    ("squawk", "ZeroHedge", "rss", "https://feeds.feedburner.com/zerohedge/feed"),
    ("squawk", "InvestingLive", "rss", "https://rss.investinglive.com/live-feed/"),
    ("bloomberg", "Bloomberg (via Google News)", "rss",
     gnews("site:bloomberg.com (gold OR silver OR dollar OR Fed OR yields OR inflation OR tariffs)")),
    ("autres", "Reuters (via Google News)", "rss",
     gnews("site:reuters.com (gold OR dollar OR Fed OR inflation OR Iran OR Israel OR tariffs)")),
    ("autres", "CNBC", "rss", "https://www.cnbc.com/id/10000664/device/rss/rss.html"),
    ("autres", "Google News", "rss",
     gnews('gold price OR XAUUSD OR silver OR "US dollar" OR "Treasury yields"')),
    ("autres", "FXStreet", "rss", "https://www.fxstreet.com/rss/news"),
]
HEAD = {"squawk": "🐦 SQUAWKS", "bloomberg": "🏦 BLOOMBERG", "autres": "📰 AUTRES SOURCES"}

HIGH_KW = ["fomc", "powell", "fed ", "cpi", "nfp", "payroll", "rate cut", "rate hike", "war",
           "attack", "missile", "sanction", "tariff", "central bank", "gold record", "emergency",
           "default", "ceasefire"]
MID_KW = ["gold", "xau", "silver", "dxy", "inflation", "dollar", "yields", "treasury", "pce",
          "gdp", "jobless", "ecb", "boj", "pboc", "geopolit", "safe haven", "oil"]
EMO = {3: "🔴", 2: "🟠", 1: "⚪"}


def fetch_rss(url):
    r = requests.get(url, headers=UA, timeout=25)
    r.raise_for_status()
    feed = feedparser.parse(r.content)
    out = []
    for e in feed.entries:
        ts = e.get("published_parsed") or e.get("updated_parsed")
        title = clean_html(e.get("title", ""))
        if not ts or not title:
            continue
        out.append({"title": title, "ts": dt.datetime.fromtimestamp(calendar.timegm(ts), UTC)})
    return out


def fetch_telegram(url):
    r = requests.get(url, headers=UA, timeout=25)
    r.raise_for_status()
    out = []
    for chunk in r.text.split("tgme_widget_message_wrap")[1:]:
        m = re.search(r'class="tgme_widget_message_text[^"]*"[^>]*>(.*?)</div>', chunk, re.S)
        d = re.search(r'<time[^>]*datetime="([^"]+)"', chunk)
        if not m or not d:
            continue
        text = clean_html(m.group(1))
        if text:
            out.append({"title": text[:300], "ts": dt.datetime.fromisoformat(d.group(1)).astimezone(UTC)})
    return out


def fetch_all(hours, stats=None):
    cutoff = dt.datetime.now(UTC) - dt.timedelta(hours=hours)
    items, titles = [], set()
    for group, name, kind, url in SOURCES:
        try:
            got = fetch_telegram(url) if kind == "telegram" else fetch_rss(url)
        except Exception as e:
            print("source error", name, e)
            if stats is not None:
                stats[name] = f"❌ {type(e).__name__}"
            continue
        n = 0
        for g in got:
            key = re.sub(r"\W+", " ", g["title"].lower())[:80]
            if g["ts"] < cutoff or key in titles:
                continue
            titles.add(key)
            g["group"], g["source"] = group, name
            items.append(g)
            n += 1
        if stats is not None:
            stats[name] = f"✅ {n} récentes ({len(got)} lues)"
    items.sort(key=lambda x: x["ts"], reverse=True)
    return items


def keyword_score(title):
    t = title.lower()

    def has(kws):
        return any(re.search(r"\b" + re.escape(k.strip()), t) for k in kws)

    return 3 if has(HIGH_KW) else 2 if has(MID_KW) else 1


def score_items(items):
    """Ajoute à chaque news: impact 1-3, t (titre FR), why, gold, dxy, silver, status, event."""
    items = items[:40]
    result = {}
    if items and has_ai():
        lines = "\n".join(f"{i}. [{it['source']}] {it['title'][:280]}" for i, it in enumerate(items))
        prompt = (
            "Tu es analyste macro pour un trader d'XAUUSD (CFD or spot). Pour chaque ligne, donne:\n"
            "- impact: 3 = intense (CPI/NFP/Fed/taux, choc géopolitique, grosse surprise), "
            "2 = modéré (pertinent or/DXY/argent/rendements), 1 = faible, hors sujet, doublon ou opinion. "
            "Sois strict: la plupart valent 1.\n"
            "- t: titre en français, 15 mots max\n"
            "- event: l'événement en 1 phrase courte (français)\n"
            "- why: pourquoi ça compte pour l'or, 15 mots max\n"
            "- gold / dxy / silver: bullish, bearish ou neutral (effet attendu)\n"
            "- status: confirmed (fait officiel), reported (rapporté, non confirmé), "
            "analysis (interprétation) ou speculation (rumeur)\n"
            'Réponds UNIQUEMENT avec un tableau JSON: [{"i":0,"impact":3,"t":"...","event":"...",'
            '"why":"...","gold":"bullish","dxy":"bearish","silver":"bullish","status":"reported"}]\n\n' + lines)
        try:
            txt = claude(prompt, 5000)
            data = json.loads(txt[txt.index("["): txt.rindex("]") + 1])
            result = {d["i"]: d for d in data}
        except Exception as e:
            print("scoring error, fallback mots-clés:", e)
    for i, it in enumerate(items):
        s = result.get(i)
        if s:
            it["impact"] = int(s.get("impact", 1))
            it["t"] = s.get("t") or it["title"]
            for k in ("event", "why", "gold", "dxy", "silver", "status"):
                it[k] = s.get(k, "")
        else:
            it["impact"], it["t"] = keyword_score(it["title"]), it["title"]
            for k in ("event", "why", "gold", "dxy", "silver", "status"):
                it[k] = ""
    return items


def candidates(items):
    """Garde tout des squawks/Bloomberg, filtre les autres par mots-clés (économise l'API)."""
    return [it for it in items if it["group"] != "autres" or keyword_score(it["title"]) >= 2][:40]


# ---------- calendrier Forex Factory ----------
HOLIDAY_CCY = {"USD", "EUR", "GBP", "JPY", "CNY", "CAD"}


def calendar_events(usd_only=False):
    raw = []
    for f in ("thisweek", "nextweek"):
        try:
            r = requests.get(f"https://nfs.faireconomy.media/ff_calendar_{f}.json", headers=UA, timeout=30)
            r.raise_for_status()
            raw += r.json()
        except Exception as e:
            print("calendar error", f, e)
    out, keys = [], set()
    for e in raw:
        try:
            t = dt.datetime.fromisoformat(e["date"]).astimezone(TZ)
        except Exception:
            continue
        impact, country = e.get("impact"), e.get("country")
        key = (e.get("title"), e.get("date"), country)
        if key in keys:
            continue
        keys.add(key)
        if usd_only and country != "USD":
            continue
        if (impact == "High" or (impact == "Medium" and country == "USD")
                or (impact == "Holiday" and country in HOLIDAY_CCY)):
            out.append({"t": t, "country": country, "title": e.get("title"), "impact": impact,
                        "forecast": e.get("forecast", ""), "previous": e.get("previous", ""),
                        "actual": e.get("actual", "")})   # absent du flux gratuit si vide
    out.sort(key=lambda x: x["t"])
    return out


def fmt_event(e, day=False):
    icon = {"High": "🔴", "Medium": "🟠", "Holiday": "🏖"}.get(e["impact"], "⚪")
    when = "Toute la journée" if e["impact"] == "Holiday" else e["t"].strftime("%H:%M")
    d = fr_date(e["t"]) + " " if day else ""
    extra = "" if e["impact"] == "Holiday" else f" (prév {e['forecast'] or '-'} | préc. {e['previous'] or '-'})"
    return f"{icon} {d}{when} {e['country']} {e['title']}{extra}"
