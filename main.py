#!/usr/bin/env python3
"""Gold Watch v3 - XAUUSD / DXY / argent. Rapports et alertes via Telegram.

Modes: test | all | sources | alert | briefing | weekly | liquidity | open-<asia|london|ny> | end-<...>
"""
import sys
import traceback
import datetime as dt

import core
import levels as L
import reports as R
from core import TZ, SEP, send_telegram, load_seen, save_seen, uid

ACTIVE = ["asia", "london", "ny"]


# (nom, jours 0=lun, heure, minute, fonction) - heure de Toronto
JOBS = [
    ("briefing", {0, 1, 2, 3, 4}, 7, 0, R.daily_briefing),
    ("weekly", {6}, 17, 0, R.weekly_report),
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


def run_due_sessions(seen, now=None):
    now = now or dt.datetime.now(TZ)
    ran = []
    for key in ACTIVE:
        for o, c in L.session_windows(key, now):
            for kind, t in (("open", o), ("end", c)):
                k = f"sess:{key}:{kind}:{t.isoformat()}"
                if t <= now < t + dt.timedelta(minutes=60) and k not in seen:
                    try:
                        R.session_report(key, o, c, kind, now)
                        seen.append(k)
                        ran.append(k)
                    except Exception:
                        traceback.print_exc()
    return ran


def send_news(new, seen):
    scored = core.score_items(core.candidates(new))
    its = [it for it in scored if it["impact"] >= core.IMPACT_MIN]
    its.sort(key=lambda x: (-x["impact"], -x["ts"].timestamp()))
    major = [it for it in its if it["impact"] >= 3]
    rest = [it for it in its if it["impact"] < 3]
    for it in major[:5]:                       # grosses news: une alerte complète chacune
        send_telegram(R.news_alert(it))
    if rest:                                   # le reste: groupé par source pour limiter le bruit
        for group in ("squawk", "bloomberg", "autres"):
            g = [it for it in rest if it["group"] == group][:core.MAX_PER_GROUP]
            if g:
                body = "\n\n".join(f"{core.EMO[it['impact']]} {it['source']} · {it['t']}"
                                   + (f"\n↳ {it['why']}" if it["why"] else "")
                                   + (f"\n↳ Or: {R.GOLD[it['gold']]}" if R.GOLD.get(it["gold"]) else "")
                                   for it in g)
                send_telegram(f"{core.HEAD[group]}\n{SEP}\n\n{body}")


def mode_alert():
    seen = load_seen()
    seen_set = set(seen)
    new = [it for it in core.fetch_all(2) if uid(it["title"]) not in seen_set]
    for it in new:
        seen.append(uid(it["title"]))
    steps = [
        ("news", lambda: new and send_news(new, seen)),
        ("rappels", lambda: remind(seen, seen_set)),
        ("publications", lambda: R.release_alerts(seen)),
        ("liquidité", lambda: R.liquidity_alerts(seen)),
        ("sessions", lambda: run_due_sessions(seen)),
        ("jobs", lambda: run_due_jobs(seen)),
    ]
    for name, fn in steps:
        try:
            fn()
        except Exception:
            print("étape en échec:", name)
            traceback.print_exc()
    save_seen(seen)


def remind(seen, seen_set):
    now = dt.datetime.now(TZ)
    for e in core.calendar_events():
        mins = (e["t"] - now).total_seconds() / 60
        key = "ev:" + uid(f"{e['title']}{e['t'].isoformat()}{e['country']}")
        if e["impact"] != "Holiday" and 0 < mins <= 35 and key not in seen_set:
            send_telegram(f"⏰ DANS {int(mins)} MIN\n{SEP}\n{core.fmt_event(e)}\n↳ {R.why_usd(e['title'])}")
            seen.append(key)


def mode_sources():
    stats = {}
    items = core.fetch_all(24, stats)
    lines = ["🔎 TEST DES SOURCES (dernières 24 h)"] + [f"{k}: {v}" for k, v in stats.items()]
    lines += [""] + [f"• {it['source']}: {it['title'][:90]}" for it in items[:6]]
    lines += ["", "ℹ️ Non disponibles gratuitement: Bloomberg/Reuters/FT/WSJ (accès direct), X, FinancialJuice."]
    send_telegram("\n".join(lines))


def latest_window(key, kind):
    now = dt.datetime.now(TZ)
    i = 0 if kind == "open" else 1
    past = [w for w in L.session_windows(key, now, back=5, forward=0) if w[i] <= now]
    return max(past, key=lambda w: w[i])


def run_session_msg(kind, key):
    o, c = latest_window(key, kind)
    R.session_report(key, o, c, kind)


def mode_all():
    send_telegram("🧪 TEST COMPLET — voici tout ce que le bot sait envoyer.")
    steps = [("sources", mode_sources),
             ("exemple news", lambda: send_news(core.fetch_all(24)[:40], [])),
             ("calendrier USD", lambda: send_telegram(
                 "📅 PROCHAINES NEWS USD\n" + SEP + "\n" + "\n".join(
                     core.fmt_event(e, True) for e in core.calendar_events(usd_only=True)
                     if e["t"] > dt.datetime.now(TZ))[:3000] or "Aucune")),
             ("liquidité", lambda: send_telegram(
                 "💧 BALAYAGES RÉCENTS (8 dernières bougies 15 min)\n" + SEP + "\n" + (
                     "\n".join(f"{e['label']} {e['level']:,.2f} → {e['status']}" for e in R.sweep_events())
                     or "Aucun balayage récent détecté.")))]
    for key in ACTIVE:
        for kind in ("open", "end"):
            steps.append((f"{kind}-{key}", lambda k=key, i=kind: run_session_msg(i, k)))
    steps += [("briefing 7h", R.daily_briefing), ("rapport dimanche", R.weekly_report)]
    for name, fn in steps:
        try:
            fn()
        except Exception as e:
            traceback.print_exc()
            try:
                send_telegram(f"⚠️ {name}: échec ({type(e).__name__}: {str(e)[:120]})")
            except Exception:
                pass
    send_telegram("🧪 FIN DU TEST COMPLET")


if __name__ == "__main__":
    mode = (sys.argv[1] if len(sys.argv) > 1 else "test").strip()
    manual = {"briefing": R.daily_briefing, "weekly": R.weekly_report}
    if mode == "test":
        m = R.load()
        send_telegram("✅ Gold Watch v3 est connecté.\n\n" + "\n".join(
            R.price_block(i) for i in m.values() if i) + "\n\n" + R.source_line([i for i in m.values() if i]))
    elif mode == "sources":
        mode_sources()
    elif mode == "all":
        mode_all()
    elif mode == "alert":
        mode_alert()
    elif mode == "liquidity":
        seen = load_seen()
        R.liquidity_alerts(seen)
        save_seen(seen)
    elif mode in manual:
        manual[mode]()
    elif "-" in mode and mode.split("-", 1)[0] in ("open", "end") and mode.split("-", 1)[1] in L.SESSION_CFG:
        kind, key = mode.split("-", 1)
        run_session_msg(kind, key)
    else:
        sys.exit(f"Mode inconnu: {mode}")
