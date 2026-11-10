#!/usr/bin/env python3
"""Gold Watch v2 - XAUUSD / DXY / argent. Alertes courtes + recaps via Telegram.

Modes: test | sources | alert | calendar | bias | weekly | open-<session> | end-<session>
(sessions: sydney, tokyo, london, ny)
"""
import os
import sys
import re
import json
import html
import hashlib
import calendar
import traceback
import datetime as dt
from urllib.parse import quote_plus
from zoneinfo import ZoneInfo

import requests
import feedparser

TZ = ZoneInfo(os.getenv("TZ_NAME", "America/Toronto"))
MODEL = os.getenv("CLAUDE_MODEL", "claude-haiku-5-5")
IMPACT_MIN = int(os.getenv("IMPACT_MIN", "2"))         # 3 = seulement les news intenses (rouge)
MAX_PER_GROUP = int(os.getenv("MAX_PER_GROUP", "6"))   # max de news par message
SEEN_FILE = "seen.json"
UA = {"User-Agent": "Mozilla/5.0 (GoldWatch)"}
UTC = dt.timezone.utc


def gnews(query):
    return ("https://news.google.com/rss/search?q=" + quote_plus(query + " when:1d")
            + "&hl=en-US&gl=US&ceid=US:en")


# (groupe, nom, type, url) - chaque groupe = un message Telegram séparé
SOURCES = [
    ("squawk", "First Squawk", "telegram", "https://t.me/s/firstsquaw"),
    ("squawk", "ZeroHedge", "rss", "https://feeds.feedburner.com/zerohedge/feed"),
    ("squawk", "InvestingLive", "rss", "https://rss.investinglive.com/live-feed/"),
    ("bloomberg", "Bloomberg", "rss",
     gnews("site:bloomberg.com (gold OR silver OR dollar OR Fed OR yields OR inflation OR tariffs)")),
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
GOLD = {"bullish": "📈 Or haussier", "bearish": "📉 Or baissier", "neutral": "➖ Or neutre"}
JOURS = ["lun", "mar", "mer", "jeu", "ven", "sam", "dim"]
MOIS = ["jan", "fév", "mar", "avr", "mai", "juin", "juil", "août", "sep", "oct", "nov", "déc"]


def fr_date(d):
    return f"{JOURS[d.weekday()]} {d.day} {MOIS[d.month - 1]}"


SEP = "━━━━━━━━━━━━━━━"


def section(emoji, title, lines):
    return f"{SEP}\n{emoji} {title}\n" + "\n".join(lines)


def arrow(chg):
    return "🟩 ▲" if chg >= 0 else "🟥 ▼"


def price_line(name, s):
    return (f"{name}  {s['close']:,.2f}  {arrow(s['chg'])} {s['chg']:+.2f}%\n"
            f"      ⬆️ {s['high']:,.2f}   ⬇️ {s['low']:,.2f}")


# ---------- utilitaires ----------
def send_telegram(text):
    token, chat = os.environ["TELEGRAM_TOKEN"], os.environ["TELEGRAM_CHAT_ID"]
    for i in range(0, len(text), 3900):
        r = requests.post(
            f"https://api.telegram.org/bot{token}/sendMessage",
            json={"chat_id": chat, "text": text[i:i + 3900], "disable_web_page_preview": True},
            timeout=30,
        )
        r.raise_for_status()


def claude(prompt, max_tokens=1500):
    key = os.getenv("ANTHROPIC_API_KEY")
    if not key:
        return None
    r = requests.post(
        "https://api.anthropic.com/v1/messages",
        headers={"x-api-key": key, "anthropic-version": "2023-06-01", "content-type": "application/json"},
        json={"model": MODEL, "max_tokens": max_tokens, "messages": [{"role": "user", "content": prompt}]},
        timeout=120,
    )
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


# ---------- sources de news ----------
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
        out.append({"title": title, "link": e.get("link", ""),
                    "ts": dt.datetime.fromtimestamp(calendar.timegm(ts), UTC)})
    return out


def fetch_telegram(url):
    """Lit l'aperçu web public d'un canal Telegram (t.me/s/<canal>)."""
    r = requests.get(url, headers=UA, timeout=25)
    r.raise_for_status()
    out = []
    for chunk in r.text.split("tgme_widget_message_wrap")[1:]:
        m = re.search(r'class="tgme_widget_message_text[^"]*"[^>]*>(.*?)</div>', chunk, re.S)
        d = re.search(r'<time[^>]*datetime="([^"]+)"', chunk)
        link = re.search(r'tgme_widget_message_date"\s+href="([^"]+)"', chunk)
        if not m or not d:
            continue
        text = clean_html(m.group(1))
        if not text:
            continue
        out.append({"title": text[:300], "link": link.group(1) if link else "",
                    "ts": dt.datetime.fromisoformat(d.group(1)).astimezone(UTC)})
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
            key = g["title"].lower()[:90]
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


# ---------- calendrier Forex Factory ----------
HOLIDAY_CCY = {"USD", "EUR", "GBP", "JPY", "CNY", "CAD"}


def calendar_events():
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
        if (impact == "High" or (impact == "Medium" and country == "USD")
                or (impact == "Holiday" and country in HOLIDAY_CCY)):
            out.append({"t": t, "country": country, "title": e.get("title"), "impact": impact,
                        "forecast": e.get("forecast", ""), "previous": e.get("previous", "")})
    out.sort(key=lambda x: x["t"])
    return out


def fmt_event(e, day=False):
    icon = {"High": "🔴", "Medium": "🟠", "Holiday": "🏖"}.get(e["impact"], "⚪")
    when = "Toute la journée" if e["impact"] == "Holiday" else e["t"].strftime("%H:%M")
    d = fr_date(e["t"]) + " " if day else ""
    extra = "" if e["impact"] == "Holiday" else f" (prév {e['forecast'] or '-'} | préc. {e['previous'] or '-'})"
    return f"{icon} {d}{when} {e['country']} {e['title']}{extra}"


# ---------- prix (CFD spot via Yahoo, indicatif) ----------
def yahoo_bars(sym, interval, rng):
    r = requests.get(f"https://query1.finance.yahoo.com/v8/finance/chart/{sym}?interval={interval}&range={rng}",
                     headers=UA, timeout=25)
    res = r.json()["chart"]["result"][0]
    q = res["indicators"]["quote"][0]
    return [(dt.datetime.fromtimestamp(t, UTC), o, h, l, c)
            for t, o, h, l, c in zip(res["timestamp"], q["open"], q["high"], q["low"], q["close"])
            if None not in (o, h, l, c)]


def window_stats(sym, start, end):
    bars = [b for b in yahoo_bars(sym, "15m", "5d") if start <= b[0] < end]
    if not bars:
        return None
    o, c = bars[0][1], bars[-1][4]
    return {"close": c, "high": max(b[2] for b in bars), "low": min(b[3] for b in bars),
            "chg": (c / o - 1) * 100}


def snapshot():
    out = []
    for name, sym, is_yield in (("🥇 XAUUSD", "XAUUSD=X", False), ("🥈 XAGUSD", "XAGUSD=X", False),
                                ("💵 DXY", "DX-Y.NYB", False), ("📊 US10Y", "^TNX", True)):
        try:
            closes = [b[4] for b in yahoo_bars(sym, "1d", "5d")]
            last, prev, first = closes[-1], closes[-2], closes[0]
            if is_yield:
                out.append(f"{name} {last:.2f}% ({last - prev:+.2f} jour, {last - first:+.2f} sur 5j)")
            else:
                out.append(f"{name} {last:.2f} ({(last / prev - 1) * 100:+.2f}% jour, "
                           f"{(last / first - 1) * 100:+.2f}% sur 5j)")
        except Exception as e:
            print("snapshot error", sym, e)
    return "\n".join(out) or "Prix indisponibles"


# ---------- tri des news ----------
def keyword_score(title):
    t = title.lower()

    def has(kws):
        return any(re.search(r"\b" + re.escape(k.strip()), t) for k in kws)

    return 3 if has(HIGH_KW) else 2 if has(MID_KW) else 1


def score_items(items):
    """Ajoute impact (1-3), t (titre FR), why, gold, expect à chaque item."""
    items = items[:40]
    result = {}
    if items and os.getenv("ANTHROPIC_API_KEY"):
        lines = "\n".join(f"{i}. [{it['source']}] {it['title'][:280]}" for i, it in enumerate(items))
        prompt = (
            "Tu es analyste macro pour un trader d'XAUUSD (CFD or spot). Il suit l'or, le dollar (DXY), "
            "l'argent et les rendements US. Pour chaque ligne, donne:\n"
            "- impact: 3 = intense (peut bouger l'or fortement: CPI/NFP/Fed/taux, choc géopolitique, grosse "
            "surprise), 2 = modéré (pertinent pour or/DXY/argent/rendements), 1 = faible, hors sujet, "
            "doublon ou simple opinion. Sois strict: la plupart des lignes valent 1.\n"
            "- t: titre traduit en français, 15 mots max\n"
            "- why: pourquoi ça compte, 12 mots max\n"
            "- gold: bullish, bearish ou neutral (effet attendu sur l'or)\n"
            "- expect: à quoi s'attendre pour l'or, 10 mots max\n"
            'Réponds UNIQUEMENT avec un tableau JSON: [{"i":0,"impact":3,"t":"...","why":"...",'
            '"gold":"bullish","expect":"..."}]\n\n' + lines
        )
        try:
            txt = claude(prompt, 4000)
            data = json.loads(txt[txt.index("["): txt.rindex("]") + 1])
            result = {d["i"]: d for d in data}
        except Exception as e:
            print("scoring error, fallback mots-clés:", e)
    for i, it in enumerate(items):
        s = result.get(i)
        if s:
            it["impact"] = int(s.get("impact", 1))
            it["t"] = s.get("t") or it["title"]
            it["why"], it["gold"], it["expect"] = s.get("why", ""), s.get("gold", "?"), s.get("expect", "")
        else:
            it["impact"], it["t"] = keyword_score(it["title"]), it["title"]
            it["why"], it["gold"], it["expect"] = "", "?", ""
    return items


def fmt_item(it, link=False):
    s = f"{EMO.get(it['impact'], '⚪')} {it['source']} · {it['t']}"
    if it["why"]:
        s += f"\n↳ {it['why']}"
    g = GOLD.get(it["gold"])
    if g:
        s += f"\n↳ {g}" + (f": {it['expect']}" if it["expect"] else "")
    if link and it["link"]:
        s += f"\n{it['link']}"
    return s


def send_groups(scored):
    for group in ("squawk", "bloomberg", "autres"):
        its = [it for it in scored if it["group"] == group and it["impact"] >= IMPACT_MIN]
        its.sort(key=lambda x: (-x["impact"], -x["ts"].timestamp()))
        if its:
            body = "\n\n".join(fmt_item(it) for it in its[:MAX_PER_GROUP])
            send_telegram(f"{HEAD[group]}\n{SEP}\n\n{body}")


def candidates(items):
    """Garde tout des squawks/Bloomberg, filtre les 'autres' par mots-clés (économise l'API)."""
    return [it for it in items if it["group"] != "autres" or keyword_score(it["title"]) >= 2][:40]


def colorize(text):
    for word, emo in (("HAUSSIER", "📈"), ("BAISSIER", "📉"), ("NEUTRE", "➖")):
        text = text.replace(word, f"{emo} {word}")
    return text


# ---------- tâches planifiées (heure de Toronto) ----------
def job_calendar():
    now = dt.datetime.now(TZ)
    evs = calendar_events()
    today = [e for e in evs if e["t"].date() == now.date()]
    tomorrow = [e for e in evs if e["t"].date() == (now + dt.timedelta(days=1)).date()
                and e["impact"] in ("High", "Holiday")]
    parts = [f"📅 CALENDRIER · {fr_date(now).upper()}"]
    parts.append(section("🗓", "AUJOURD'HUI", [fmt_event(e) for e in today] or ["Rien d'important."]))
    if tomorrow:
        parts.append(section("⏭", "DEMAIN", [fmt_event(e) for e in tomorrow]))
    parts.append("🔴 fort · 🟠 moyen · 🏖 congé bancaire")
    send_telegram("\n\n".join(parts))


# (clé, nom affiché, fuseau du centre financier, ouverture, fermeture) - heures LOCALES du centre, lun-ven.
# Les heures de Toronto en sont déduites automatiquement (changements d'heure inclus).
SESSIONS = {
    "sydney": ("SYDNEY", "Australia/Sydney", 9, 18),
    "tokyo": ("TOKYO", "Asia/Tokyo", 9, 18),
    "london": ("LONDON", "Europe/London", 8, 17),
    "ny": ("NEW YORK", "America/New_York", 8, 17),
}
ACTIVE_SESSIONS = [s.strip() for s in os.getenv("SESSIONS", "tokyo,london,ny").split(",") if s.strip()]
PRICE_SYMS = (("🥇 XAUUSD", "XAUUSD=X"), ("🥈 XAGUSD", "XAGUSD=X"), ("💵 DXY", "DX-Y.NYB"))


def session_windows(key, now, back=1, forward=1):
    """Liste de (ouverture, fermeture) en heure de Toronto, jours ouvrables du centre financier."""
    label, tzname, oh, ch = SESSIONS[key]
    ctz = ZoneInfo(tzname)
    base = now.astimezone(ctz).date()
    out = []
    for off in range(-back, forward + 1):
        d = base + dt.timedelta(days=off)
        if d.weekday() > 4:
            continue
        out.append((dt.datetime.combine(d, dt.time(oh), ctz).astimezone(TZ),
                    dt.datetime.combine(d, dt.time(ch), ctz).astimezone(TZ)))
    return out


def last_price(sym):
    return yahoo_bars(sym, "15m", "1d")[-1][4]


def job_session_open(key, start, end):
    label = SESSIONS[key][0]
    rows = []
    for name, sym in PRICE_SYMS:
        try:
            rows.append(f"{name}  {last_price(sym):,.2f}")
        except Exception as e:
            print("price error", sym, e)
    evs = [e for e in calendar_events() if start <= e["t"] < end and e["impact"] != "Holiday"]
    parts = [f"🟢 OPEN OF {label} SESSION\n🕒 {start.strftime('%H:%M')} → {end.strftime('%H:%M')} (Toronto)",
             section("💰", "PRIX À L'OUVERTURE", rows or ["Prix indisponibles."]),
             section("📅", "AU PROGRAMME PENDANT LA SESSION",
                     [fmt_event(e) for e in evs] or ["Rien d'important."])]
    send_telegram("\n\n".join(parts))


def job_session_end(key, start, end):
    label = SESSIONS[key][0]
    prices = []
    for name, sym in PRICE_SYMS:
        try:
            s = window_stats(sym, start, end)
        except Exception as e:
            print("stats error", sym, e)
            s = None
        if s:
            prices.append(price_line(name, s))
    hours = (dt.datetime.now(TZ) - start).total_seconds() / 3600 + 1
    news = [it for it in fetch_all(hours) if start <= it["ts"] < end]
    top = [it for it in score_items(candidates(news)) if it["impact"] >= 2]
    top.sort(key=lambda x: (-x["impact"], -x["ts"].timestamp()))
    top = top[:4]
    parts = [f"🏁 END OF {label} SESSION\n🕒 {start.strftime('%H:%M')} → {end.strftime('%H:%M')} (Toronto)",
             section("💰", "PRIX À LA FERMETURE", prices or ["Prix indisponibles."])]
    if top:
        rows = []
        for it in top:
            g = GOLD.get(it["gold"])
            rows.append(f"{EMO[it['impact']]} {it['source']} · {it['t']}"
                        + (f"\n   ↳ {g}" + (f": {it['expect']}" if it["expect"] else "") if g else ""))
        parts.append(section("📰", "CE QUI A BOUGÉ", rows))
    if top and os.getenv("ANTHROPIC_API_KEY"):
        try:
            txt = claude("En 1 phrase (30 mots max, français, texte brut), dis ce qui a mené l'or (XAUUSD) "
                         "pendant cette session. Données:\n" + "\n".join(prices) + "\n"
                         + "\n".join(f"{it['t']} ({it['gold']})" for it in top))
            parts.append(section("🧠", "RÉSUMÉ", [txt.strip()]))
        except Exception as e:
            print("summary error", e)
    send_telegram("\n\n".join(parts))


def run_due_sessions(seen, now=None):
    """Envoie OPEN / END des sessions dont l'heure vient de passer (fenêtre de 60 min, une seule fois)."""
    now = now or dt.datetime.now(TZ)
    ran = []
    for key in ACTIVE_SESSIONS:
        if key not in SESSIONS:
            continue
        for o, c in session_windows(key, now):
            for kind, t, fn in (("open", o, job_session_open), ("end", c, job_session_end)):
                k = f"sess:{key}:{kind}:{t.isoformat()}"
                if t <= now < t + dt.timedelta(minutes=60) and k not in seen:
                    try:
                        fn(key, o, c)
                        seen.append(k)
                        ran.append(k)
                    except Exception:
                        traceback.print_exc()
    return ran


def latest_window(key, kind):
    """Dernière session passée (pour les tests manuels)."""
    now = dt.datetime.now(TZ)
    i = 0 if kind == "open" else 1
    past = [w for w in session_windows(key, now, back=5, forward=0) if w[i] <= now]
    return max(past, key=lambda w: w[i])


def job_bias():
    now = dt.datetime.now(TZ)
    monday = (now + dt.timedelta(days=(7 - now.weekday()) % 7 or 7)).date() if now.weekday() != 0 \
        else now.date()
    evs = [e for e in calendar_events()
           if monday <= e["t"].date() <= monday + dt.timedelta(days=4)]
    news = score_items(candidates(fetch_all(72)))
    news.sort(key=lambda x: -x["impact"])
    cal = "\n".join(fmt_event(e, True) for e in evs) or "Rien d'important"
    head = f"🧭 BIAIS XAUUSD — semaine du {fr_date(dt.datetime.combine(monday, dt.time()))}"
    txt = None
    if os.getenv("ANTHROPIC_API_KEY"):
        txt = claude(
            "Tu es analyste macro. Écris en français, texte brut, COURT, le biais fondamental de l'or "
            "(XAUUSD, CFD) pour la semaine qui vient. Format EXACT (une ligne chacune):\n"
            "SEMAINE: HAUSSIER|BAISSIER|NEUTRE - raison en 1 phrase\n"
            "LUN: HAUSSIER|BAISSIER|NEUTRE - 1 phrase (événement clé du jour)\n"
            "MAR: ...\nMER: ...\nJEU: ...\nVEN: ...\n"
            "RISQUE: 1 phrase (ce qui peut invalider le biais)\n"
            "N'invente aucun prix ni niveau. Appuie-toi seulement sur les données.\n\n"
            f"MARCHÉ:\n{snapshot()}\n\nCALENDRIER:\n{cal}\n\nNEWS (impact 1-3):\n"
            + "\n".join(f"[{n['impact']}] {n['t']}" for n in news[:20]), 800)
    parts = [head]
    if txt:
        parts.append(section("🎯", "BIAIS", colorize(txt.strip()).split("\n")))
    parts.append(section("📅", "CALENDRIER DE LA SEMAINE", cal.split("\n")))
    parts.append("Analyse automatique, pas un conseil financier.")
    send_telegram("\n\n".join(parts))


def job_weekly():
    news = score_items(candidates(fetch_all(96)))
    news.sort(key=lambda x: -x["impact"])
    txt = None
    if os.getenv("ANTHROPIC_API_KEY"):
        txt = claude(
            "Recap de fin de semaine pour l'or (XAUUSD, CFD). Français, texte brut, 120 mots max. "
            "Format EXACT:\nMOTEURS: 3 puces courtes (•)\nEN SUSPENS: 1-2 puces (•)\n"
            "BIAIS SEMAINE PROCHAINE: HAUSSIER|BAISSIER|NEUTRE - 1 phrase\n"
            "N'invente aucun prix.\n\n" f"MARCHÉ:\n{snapshot()}\n\nNEWS:\n"
            + "\n".join(f"[{n['impact']}] {n['t']}" for n in news[:20]), 700)
    parts = ["📊 RECAP DE FIN DE SEMAINE", section("💰", "PRIX (5 JOURS)", snapshot().split("\n"))]
    if txt:
        parts.append(section("🧠", "ANALYSE", colorize(txt.strip()).split("\n")))
    send_telegram("\n\n".join(parts))


# (nom, jours 0=lun, heure, minute, fonction) - heure de Toronto
JOBS = [
    ("calendar", {0, 1, 2, 3, 4}, 6, 0, job_calendar),
    ("weekly", {4}, 17, 15, job_weekly),
    ("bias", {6}, 18, 0, job_bias),
]


def run_due_jobs(seen, now=None, jobs=JOBS):
    """Lance les tâches dont l'heure est passée (fenêtre de 3 h, une fois par jour)."""
    now = now or dt.datetime.now(TZ)
    ran = []
    for name, days, h, m, fn in jobs:
        if now.weekday() not in days:
            continue
        due = now.replace(hour=h, minute=m, second=0, microsecond=0)
        key = f"job:{name}:{now.date()}"
        if due <= now < due + dt.timedelta(hours=3) and key not in seen:
            try:
                fn()
                seen.append(key)
                ran.append(name)
            except Exception:
                traceback.print_exc()
    return ran


# ---------- modes ----------
def mode_alert():
    seen = load_seen()
    seen_set = set(seen)
    new = [it for it in fetch_all(2) if uid(it["title"]) not in seen_set]
    for it in new:
        seen.append(uid(it["title"]))
    if new:
        send_groups(score_items(candidates(new)))
    now = dt.datetime.now(TZ)
    for e in calendar_events():
        mins = (e["t"] - now).total_seconds() / 60
        key = "ev:" + uid(f"{e['title']}{e['t'].isoformat()}{e['country']}")
        if e["impact"] != "Holiday" and 0 < mins <= 35 and key not in seen_set:
            send_telegram(f"⏰ DANS {int(mins)} MIN\n{SEP}\n{fmt_event(e)}")
            seen.append(key)
    run_due_sessions(seen)
    run_due_jobs(seen)
    save_seen(seen)


def mode_sources():
    stats = {}
    items = fetch_all(24, stats)
    lines = ["🔎 TEST DES SOURCES (dernières 24 h)"] + [f"{k}: {v}" for k, v in stats.items()]
    lines.append("")
    lines += [f"• {it['source']}: {it['title'][:90]}" for it in items[:6]]
    send_telegram("\n".join(lines))


if __name__ == "__main__":
    mode = (sys.argv[1] if len(sys.argv) > 1 else "test").strip()
    manual = {"calendar": job_calendar, "bias": job_bias, "weekly": job_weekly}
    if mode == "test":
        send_telegram("✅ Gold Watch v2 est connecté.\n\n" + snapshot())
    elif mode == "sources":
        mode_sources()
    elif mode == "alert":
        mode_alert()
    elif mode in manual:
        manual[mode]()
    elif "-" in mode and mode.split("-", 1)[0] in ("open", "end") and mode.split("-", 1)[1] in SESSIONS:
        kind, key = mode.split("-", 1)
        o, c = latest_window(key, kind)
        (job_session_open if kind == "open" else job_session_end)(key, o, c)
    else:
        sys.exit(f"Mode inconnu: {mode}")
