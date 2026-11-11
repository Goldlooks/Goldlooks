"""Messages Telegram: sessions, briefing 7h, rapport du dimanche, liquidité, news, publications USD."""
import re
import datetime as dt

import core
import levels as L
from core import TZ, UTC, SEP, section, fr_date, arrow, fmt_event, send_telegram

NAMES = {"XAUUSD": "🥇 XAUUSD", "DXY": "💵 DXY", "XAGUSD": "🥈 XAGUSD (argent)"}
BIAS_LBL = {"bull": "🟢 HAUSSIER", "bear": "🔴 BAISSIER", "neu": "🟡 NEUTRE"}
GOLD = {"bullish": "🟢 HAUSSIER", "bearish": "🔴 BAISSIER", "neutral": "🟡 NEUTRE"}
STATUS = {"confirmed": "✅ Confirmé", "reported": "📣 Rapporté (non confirmé)",
          "analysis": "🧩 Interprétation", "speculation": "❓ Spéculation"}
WHY_USD = {  # pourquoi chaque publication compte pour l'or
    "cpi": "Inflation: plus forte = Fed plus ferme = USD ↑, or ↓ (et inversement)",
    "ppi": "Inflation à la production: anticipe le CPI",
    "nfp": "Emploi US: chiffre fort = USD ↑ / or ↓; faible = l'inverse",
    "non-farm": "Emploi US: chiffre fort = USD ↑ / or ↓; faible = l'inverse",
    "fomc": "Décision de taux: ton ferme = USD ↑ / or ↓; ton souple = l'inverse",
    "powell": "Le ton de la Fed déplace les taux, le dollar et l'or",
    "pce": "Inflation préférée de la Fed",
    "gdp": "Croissance US: forte = USD ↑",
    "ism": "Santé de l'économie US",
    "jobless": "Marché du travail: plus de demandes = USD ↓ / or ↑",
    "jolts": "Offres d'emploi: mesure la tension du marché du travail",
    "retail": "Consommation US",
    "unemployment": "Chômage: plus haut = USD ↓ / or ↑",
}
INVERT = ("unemployment", "jobless", "claims")  # plus haut = mauvais pour l'USD


def why_usd(title):
    t = title.lower()
    for k, v in WHY_USD.items():
        if k in t:
            return v
    return "Publication US pouvant bouger le dollar, les taux et donc l'or"


# ---------- blocs de base ----------
def source_line(insts):
    last = max(i["last"] for i in insts)
    age = (dt.datetime.now(TZ) - last).total_seconds() / 60
    flag = "retardé / indicatif" if age > 20 else "quasi temps réel (indicatif)"
    syms = ", ".join(sorted(set(L.SRC.values())))
    fut = "\n⚠️ Un des prix vient d'un FUTURES (=F), pas du CFD spot: écart possible avec ton broker." \
        if any(v.endswith("=F") for v in L.SRC.values()) else ""
    return (f"ℹ️ Source prix: Yahoo Finance ({syms}) · dernière bougie {last.strftime('%H:%M')} Toronto "
            f"(il y a {int(age)} min) · {flag}{fut}")


def price_block(i):
    n, lv = i["name"], i["lv"]
    d, pd, pw = lv["day"], lv["prev_day"], lv["prev_week"]
    rows = [f"{NAMES[n]}  {L.fp(n, i['price'])}  {arrow(lv['day_chg'])} {lv['day_chg']:+.2f}% (jour)",
            f"   Jour: ⬆️ {L.fp(n, d['high'])}  ⬇️ {L.fp(n, d['low'])}"]
    if pd:
        rows.append(f"   Veille: ⬆️ {L.fp(n, pd['high'])}  ⬇️ {L.fp(n, pd['low'])}")
    w = lv["week"]
    rows.append(f"   Semaine: ⬆️ {L.fp(n, w['high'])}  ⬇️ {L.fp(n, w['low'])}")
    if pw:
        rows.append(f"   Sem. préc.: ⬆️ {L.fp(n, pw['high'])}  ⬇️ {L.fp(n, pw['low'])}")
    return "\n".join(rows)


def levels_block(i):
    n = i["name"]
    rows = [f"Structure (1h): {i['struct']['label']}"]
    if i["res"]:
        rows.append("🔺 Résistances: " + " · ".join(L.fp(n, v) for _, v in i["res"]))
    if i["sup"]:
        rows.append("🔻 Supports: " + " · ".join(L.fp(n, v) for _, v in i["sup"]))
    return rows


def liquidity_lines(i, k=3):
    liq, n = i["liq"], i["name"]
    rows = ["ℹ️ Zones de liquidité ESTIMÉES (pas un carnet d'ordres)"]
    for tag, key in (("🔺 BSL (au-dessus)", "bsl"), ("🔻 SSL (en dessous)", "ssl")):
        rows.append(tag)
        for it in liq[key][:k]:
            rows.append(f"   • {it['label']}  {L.fp(n, it['level'])}")
        if not liq[key]:
            rows.append("   • aucun niveau proche")
    if liq["swept"]:
        rows.append("💥 Déjà balayés: " + ", ".join(f"{s['label']} {L.fp(n, s['level'])}" for s in liq["swept"][:3]))
    return rows


def bias(g, d, s):
    """Biais transparent (règles simples sur données réelles). Retourne (code, preuves, invalidation)."""
    score, ev = 0, []
    st = g["struct"]
    if st["code"] == "bull":
        score += 1; ev.append(f"Structure 1h haussière ({st['seq']})")
    elif st["code"] == "bear":
        score -= 1; ev.append(f"Structure 1h baissière ({st['seq']})")
    else:
        ev.append(f"Structure 1h {st['label'].lower()}")
    lv = g["lv"]
    if lv["prev_day"]:
        mid = (lv["prev_day"]["high"] + lv["prev_day"]["low"]) / 2
        if g["price"] > mid:
            score += 1; ev.append("Prix au-dessus du milieu du range de la veille")
        else:
            score -= 1; ev.append("Prix sous le milieu du range de la veille")
    if d:
        c = d["lv"]["day_chg"]
        if abs(c) >= 0.15:
            score += -1 if c > 0 else 1
            ev.append(f"DXY {'en hausse' if c > 0 else 'en baisse'} ({c:+.2f}%): "
                      f"{'pression sur' if c > 0 else 'soutien pour'} l'or")
    if s:
        c = s["lv"]["day_chg"]
        if abs(c) >= 0.3:
            score += 1 if c > 0 else -1
            ev.append(f"Argent {'en hausse' if c > 0 else 'en baisse'} ({c:+.2f}%): "
                      f"{'confirme' if c > 0 else 'contredit'} l'or")
    code = "bull" if score >= 2 else "bear" if score <= -2 else "neu"
    n = g["name"]
    if code == "bull" and g["sup"]:
        inv = f"Cassure sous {L.fp(n, g['sup'][0][1])} (dernier swing bas)"
    elif code == "bear" and g["res"]:
        inv = f"Cassure au-dessus de {L.fp(n, g['res'][0][1])} (dernier swing haut)"
    else:
        inv = "Sortie du range entre " + " et ".join(
            [L.fp(n, g['sup'][0][1]) if g['sup'] else "?", L.fp(n, g['res'][0][1]) if g['res'] else "?"])
    return code, ev, inv


def load(names=("XAUUSD", "DXY", "XAGUSD")):
    out = {}
    for nm in names:
        try:
            out[nm] = L.instrument(nm)
        except Exception as e:
            print("instrument error", nm, e)
            out[nm] = None
    if not out.get("XAUUSD"):
        raise RuntimeError("Prix XAUUSD indisponibles (Yahoo): rapport annulé plutôt qu'inventé")
    return out


def correl_line(m):
    try:
        c = L.correlation(m["XAUUSD"]["bars"], m["DXY"]["bars"])
    except Exception:
        c = None
    if c is None:
        return "Corrélation or/DXY (5 j, horaire): non calculable"
    return f"Corrélation observée or/DXY (5 j, horaire): {c:+.2f}"


def ai_text(prompt, tokens=500):
    if not core.has_ai():
        return None
    try:
        return core.claude(prompt, tokens).strip()
    except Exception as e:
        print("ai error", e)
        return None


def facts(m):
    rows = []
    for nm, i in m.items():
        if i:
            rows.append(f"{nm} {L.fp(nm, i['price'])} jour {i['lv']['day_chg']:+.2f}% structure {i['struct']['label']}")
    g = m["XAUUSD"]
    rows.append("BSL: " + ", ".join(f"{x['label']} {x['level']:.2f}" for x in g["liq"]["bsl"][:3]))
    rows.append("SSL: " + ", ".join(f"{x['label']} {x['level']:.2f}" for x in g["liq"]["ssl"][:3]))
    return "\n".join(rows)


def expect_text(m, evs):
    g = m["XAUUSD"]
    txt = ai_text("En français, texte brut, 2 phrases MAX (45 mots), dis à quoi s'attendre pour l'or. "
                  "Utilise UNIQUEMENT ces données, n'invente aucun chiffre.\n" + facts(m)
                  + "\nÉvénements USD: " + "; ".join(e["title"] for e in evs[:4]))
    if txt:
        return txt
    n = g["name"]
    up = g["liq"]["bsl"][0] if g["liq"]["bsl"] else None
    dn = g["liq"]["ssl"][0] if g["liq"]["ssl"] else None
    parts = []
    if up:
        parts.append(f"liquidité au-dessus: {up['label']} {L.fp(n, up['level'])}")
    if dn:
        parts.append(f"liquidité en dessous: {dn['label']} {L.fp(n, dn['level'])}")
    return "Le prix peut aller chercher la " + " ou la ".join(parts) + "." if parts else "Pas de niveau net."


def bias_section(m):
    code, ev, inv = bias(m["XAUUSD"], m.get("DXY"), m.get("XAGUSD"))
    rows = [BIAS_LBL[code], "Preuves:"] + [f"  • {e}" for e in ev] + [f"❌ Invalidation: {inv}"]
    return section("🧭", "BIAIS XAUUSD", rows), code


def sec_instr(m, nm, why):
    i = m.get(nm)
    if not i:
        return section(NAMES[nm].split()[0], nm, ["Données indisponibles (source Yahoo)."])
    rows = [price_block(i)] + levels_block(i) + [why]
    return section(NAMES[nm].split()[0], nm, rows)


DXY_WHY = "Pourquoi: or coté en USD → DXY↑ = pression sur l'or (relation habituelle, vérifiée ci-dessous)"
AG_WHY = "Pourquoi: l'argent suit l'or de près; une divergence peut signaler une faiblesse/force du mouvement"


# ---------- sessions ----------
def session_report(key, start, end, kind="open", now=None):
    now = now or dt.datetime.now(TZ)
    m = load()
    g = m["XAUUSD"]
    name = L.SESSION_CFG[key][0]
    emo = L.SESSION_EMOJI[key]
    n = "XAUUSD"
    if kind == "open":
        head = f"{emo} OPEN OF {name} SESSION\n🕒 {start.strftime('%H:%M')} → {end.strftime('%H:%M')} (Toronto)"
    else:
        head = f"🏁 END OF {name} SESSION\n🕒 {start.strftime('%H:%M')} → {end.strftime('%H:%M')} (Toronto)"
    parts = [head]
    if kind == "end":
        rows = []
        for nm in ("XAUUSD", "DXY", "XAGUSD"):
            s = L.window_stats(nm, start, end) if m.get(nm) else None
            if s:
                rows.append(f"{NAMES[nm]}  clôture {L.fp(nm, s['close'])}  {arrow(s['chg'])} {s['chg']:+.2f}%\n"
                            f"   ⬆️ {L.fp(nm, s['high'])}   ⬇️ {L.fp(nm, s['low'])}")
        parts.append(section("💰", "PRIX À LA FERMETURE (clôture de session)", rows or ["Indisponible."]))
    else:
        parts.append(section("💰", "PRIX À L'OUVERTURE", [price_block(g)]))
    parts.append(section("📐", "STRUCTURE & NIVEAUX", levels_block(g)))
    # hauts/bas de sessions: finalisés vs en formation
    srows = []
    for k in L.SESSION_CFG:
        st = g["sessions"][k]
        if not st:
            continue
        cur = st[-1]
        if kind == "end" and k == key:        # bilan: la session qui se termine, en niveaux FINAUX
            cur = next((x for x in st if x["open"] == start), cur)
        lab = L.SESSION_CFG[k][0].title()
        srows.append(f"{L.SESSION_EMOJI[k]} {lab} ({'FINAL' if cur['status'] == 'final' else 'en formation'}): "
                     f"⬆️ {L.fp(n, cur['high'])}  ⬇️ {L.fp(n, cur['low'])}")
    parts.append(section("🕘", "HAUTS / BAS DES SESSIONS", srows or ["Indisponible."]))
    parts.append(section("💧", "LIQUIDITÉ", liquidity_lines(g)))
    code, _, _ = bias(g, m.get("DXY"), m.get("XAGUSD"))
    if kind == "open":
        b, _ = bias_section(m)
        parts.append(b)
    if kind == "open":
        parts.append(sec_instr(m, "DXY", DXY_WHY))
        parts.append(sec_instr(m, "XAGUSD", AG_WHY))
    evs = [e for e in core.calendar_events(usd_only=True) if start <= e["t"] < end and e["impact"] != "Holiday"]
    if kind == "open":
        if key == "ny" or evs:
            parts.append(section("📅", "NEWS USD PENDANT LA SESSION",
                                 [fmt_event(e) + f"\n   ↳ {why_usd(e['title'])}" for e in evs] or ["Aucune news USD importante."]))
        parts.append(section("🔭", "À QUOI S'ATTENDRE", [expect_text(m, evs)]))
    else:
        parts.append(section("🧠", "RÉSUMÉ", [expect_text(m, [])]))
    parts.append(source_line([i for i in m.values() if i]))
    send_telegram("\n\n".join(parts))


# ---------- briefing 7:00 ----------
def daily_briefing():
    now = dt.datetime.now(TZ)
    m = load()
    g = m["XAUUSD"]
    evs = [e for e in core.calendar_events(usd_only=True) if e["t"].date() == now.date()]
    parts = [f"☀️ BRIEFING XAUUSD · {fr_date(now).upper()}"]
    b, code = bias_section(m)
    parts += [section("🌍", "VUE D'ENSEMBLE", [price_block(g), correl_line(m)]), b,
              section("📐", "STRUCTURE & NIVEAUX", levels_block(g)),
              section("💧", "CARTE DE LIQUIDITÉ", liquidity_lines(g, 4)),
              sec_instr(m, "DXY", DXY_WHY), sec_instr(m, "XAGUSD", AG_WHY)]
    rows = []
    for e in evs:
        if e["impact"] == "Holiday":
            rows.append(f"🏖 {e['title']} (congé bancaire US)")
            continue
        rows.append(f"{fmt_event(e)}\n   ↳ {why_usd(e['title'])}")
    parts.append(section("📅", "CALENDRIER USD (aujourd'hui)", rows or ["Aucune news USD importante."]))
    parts.append(section("🔭", "ATTENTES DU JOUR", [expect_text(m, evs)]))
    parts.append(source_line([i for i in m.values() if i]))
    send_telegram("\n\n".join(parts))


# ---------- rapport du dimanche 17:00 ----------
GEO_KW = ["iran", "israel", "china", "russia", "ukraine", "tariff", "sanction", "war", "ceasefire",
          "trump", "taiwan", "opec", "missile", "gaza"]


def weekly_report():
    now = dt.datetime.now(TZ)
    m = load()
    g = m["XAUUSD"]
    parts = ["📊 RAPPORT HEBDOMADAIRE · DIMANCHE"]
    rows = []
    for nm in ("XAUUSD", "DXY", "XAGUSD"):
        i = m.get(nm)
        if not i or not i["lv"]["prev_week"] and not i["lv"]["week"]:
            continue
        w = i["lv"]["week"] if now.weekday() != 6 or i["lv"]["week"]["start"] < now.date() \
            else i["lv"]["prev_week"]
        pw = i["lv"]["prev_week"] if w is i["lv"]["week"] else None
        chg = (w["close"] / w["open"] - 1) * 100
        rows.append(f"{NAMES[nm]}  {L.fp(nm, w['open'])} → {L.fp(nm, w['close'])}  {arrow(chg)} {chg:+.2f}%\n"
                    f"   ⬆️ {L.fp(nm, w['high'])}  ⬇️ {L.fp(nm, w['low'])}")
    parts.append(section("🗓", "BILAN DE LA SEMAINE", rows))
    news = [it for it in core.fetch_all(60) if any(k in it["title"].lower() for k in GEO_KW)]
    news = core.score_items(news[:25])
    news = sorted([n for n in news if n["impact"] >= 2], key=lambda x: -x["impact"])[:6]
    geo = []
    for it in news:
        geo.append(f"{core.EMO[it['impact']]} {it['source']} · {it['t']}"
                   + (f"\n   ↳ {STATUS.get(it['status'], '')}" if it["status"] else ""))
    parts.append(section("🌍", "WEEK-END: GÉOPOLITIQUE & NEWS",
                         geo or ["Rien de marquant détecté dans les sources disponibles."]))
    monday = (now + dt.timedelta(days=(7 - now.weekday()) % 7)).date() if now.weekday() != 0 else now.date()
    evs = [e for e in core.calendar_events(usd_only=True)
           if monday <= e["t"].date() <= monday + dt.timedelta(days=4)]
    parts.append(section("📅", "SEMAINE PROCHAINE (USD)",
                         [fmt_event(e, True) + (f"\n   ↳ {why_usd(e['title'])}" if e["impact"] != "Holiday" else "")
                          for e in evs] or ["Aucune news USD importante."]))
    b, code = bias_section(m)
    parts.append(b)
    n = "XAUUSD"
    up = g["liq"]["bsl"][0] if g["liq"]["bsl"] else None
    dn = g["liq"]["ssl"][0] if g["liq"]["ssl"] else None
    sc = []
    if up:
        sc.append(f"🟢 Haussier: cassure et tenue au-dessus de {up['label']} {L.fp(n, up['level'])}")
    if dn:
        sc.append(f"🔴 Baissier: cassure et tenue sous {dn['label']} {L.fp(n, dn['level'])}")
    sc.append("🟡 Neutre: range entre ces deux zones tant qu'aucun n'est cassé")
    parts.append(section("🔭", "PERSPECTIVES (SCÉNARIOS)", sc + [expect_text(m, evs)]))
    parts.append(section("💧", "CARTE DE LIQUIDITÉ", liquidity_lines(g, 3)))
    parts.append(source_line([i for i in m.values() if i]))
    send_telegram("\n\n".join(parts))


# ---------- alerte liquidité ----------
def sweep_events(name="XAUUSD", lookback=8):
    """Balayages récents de niveaux de référence (confirmé / possible / cassure)."""
    bars = L.get_bars(name)
    now = bars[-1][0] + dt.timedelta(minutes=15)
    win = bars[-lookback:]
    start = win[0][0]
    tol = L.INSTR[name][3] / 100
    out = []
    for label, level, kind, frm in L.reference_levels(name, bars, start):
        if frm is None:
            continue
        before = L.check_level(bars, level, kind, frm, start, L.INSTR[name][3])
        if before != "intact":
            continue
        tol_abs = level * tol
        idx = next((k for k, b in enumerate(win)
                    if (kind == "high" and b[2] > level + tol_abs) or (kind == "low" and b[3] < level - tol_abs)), None)
        if idx is None:
            continue
        last = win[-1][4]
        beyond = last > level if kind == "high" else last < level
        back_in = sum(1 for b in win[idx:] if (b[4] < level if kind == "high" else b[4] > level))
        status = "BREAKOUT (continuation)" if beyond else "SWEEP CONFIRMÉ" if back_in >= 2 else "SWEEP POSSIBLE"
        ext = max(b[2] for b in win[idx:]) if kind == "high" else min(b[3] for b in win[idx:])
        out.append({"label": label, "level": level, "kind": kind, "status": status, "extreme": ext,
                    "t": win[idx][0].astimezone(TZ), "price": last})
    return out


def liquidity_alerts(seen):
    sent = 0
    try:
        evs = sweep_events()
    except Exception as e:
        print("sweep error", e)
        return 0
    for ev in evs:
        key = f"liq:{ev['label']}:{ev['level']:.2f}:{ev['status'][:6]}:{ev['t'].date()}"
        if key in seen:
            continue
        side = "BSL (au-dessus)" if ev["kind"] == "high" else "SSL (en dessous)"
        msg = (f"💧 XAUUSD — LIQUIDITY ALERT\n{SEP}\n"
               f"{'🔺' if ev['kind'] == 'high' else '🔻'} {ev['label']} · {side}\n"
               f"Niveau: {ev['level']:,.2f} · extrême atteint: {ev['extreme']:,.2f}\n"
               f"Statut: {ev['status']} (vers {ev['t'].strftime('%H:%M')} Toronto)\n"
               f"Prix actuel: {ev['price']:,.2f}\n"
               f"ℹ️ Estimation à partir des prix (Yahoo, indicatif), pas un carnet d'ordres.\n"
               f"{'⚠️ Confirmation à vérifier: attendre la clôture de la bougie suivante.' if 'POSSIBLE' in ev['status'] else ''}")
        send_telegram(msg.strip())
        seen.append(key)
        sent += 1
    return sent


# ---------- news (format du cahier) ----------
def news_alert(it):
    t = it["ts"].astimezone(TZ)
    lv = {"3": "⚠️ ÉLEVÉE", "2": "⚠️ MOYENNE"}.get(str(it["impact"]), "⚠️ FAIBLE")
    rows = [f"🚨 {it['t']}", SEP,
            f"🌍 ÉVÉNEMENT: {it.get('event') or it['t']}"]
    gold = GOLD.get(it.get("gold"))
    if gold:
        rows.append(f"{gold.split()[0]} IMPACT POTENTIEL SUR L'OR: {gold.split()[1]}")
    if it.get("why"):
        rows.append(f"📊 POURQUOI: {it['why']}")
    for k, emo, nm in (("dxy", "💵", "DXY"), ("silver", "🥈", "ARGENT"), ("gold", "🥇", "XAUUSD")):
        if GOLD.get(it.get(k)):
            rows.append(f"{emo} {nm}: {GOLD[it[k]].split()[1].lower()}")
    rows.append(f"⚠️ IMPORTANCE: {lv.split(' ', 1)[1]}")
    rows.append(f"🏷 {STATUS.get(it.get('status'), 'Statut non évalué (pas d IA)')}")
    rows.append(f"📰 Source: {it['source']}")
    rows.append(f"🕒 Publié: {t.strftime('%H:%M')} Toronto (le {fr_date(t)})")
    return "\n".join(rows)


# ---------- publications USD ----------
def num(s):
    m = re.search(r"-?\d+(?:\.\d+)?", (s or "").replace(",", ""))
    return float(m.group()) if m else None


def verdict(title, actual, forecast):
    a, f = num(actual), num(forecast)
    if a is None or f is None:
        return None
    inv = any(k in title.lower() for k in INVERT)
    if abs(a - f) < 1e-9:
        return "neutral", "En ligne avec les attentes"
    better = (a > f) != inv        # True = meilleur que prévu pour l'USD
    return ("bearish" if better else "bullish"), \
        f"{'Meilleur' if better else 'Moins bon'} que prévu → USD {'↑' if better else '↓'}, or {'↓' if better else '↑'} (réaction initiale logique)"


def reaction(t):
    try:
        bars = L.get_bars("XAUUSD")
    except Exception:
        return None
    before = [b for b in bars if b[0] <= t.astimezone(UTC)]
    after = [b for b in bars if b[0] > t.astimezone(UTC)]
    if not before or not after:
        return None
    p0, hi, lo, p1 = before[-1][4], max(b[2] for b in after), min(b[3] for b in after), after[-1][4]
    return (f"XAUUSD: {p0:,.2f} → {p1:,.2f} ({(p1 / p0 - 1) * 100:+.2f}%) · "
            f"extrêmes depuis: ⬆️ {hi:,.2f} ⬇️ {lo:,.2f}")


def release_alerts(seen, now=None):
    """Alerte USD: 'en attente' si l'actual n'est pas encore publié, puis publication réelle. Jamais d'actual inventé."""
    now = now or dt.datetime.now(TZ)
    n = 0
    for e in core.calendar_events(usd_only=True):
        if e["impact"] == "Holiday":
            continue
        age = (now - e["t"]).total_seconds() / 60
        if not 0 <= age <= 180:
            continue
        base = core.uid(f"{e['title']}{e['t'].isoformat()}")
        if e["actual"]:
            key = f"rel:{base}:actual"
            if key in seen:
                continue
            v = verdict(e["title"], e["actual"], e["forecast"])
            rows = [f"{'🔴' if e['impact'] == 'High' else '🟠'} USD — {e['title']} · RÉSULTAT", SEP,
                    f"Précédent: {e['previous'] or '-'}   Prévu: {e['forecast'] or '-'}   Actuel: {e['actual']}"]
            if v:
                rows.append(f"{GOLD[v[0]].split()[0]} {v[1]}")
            else:
                rows.append("Interprétation automatique impossible (valeurs non numériques)")
            rows.append(f"Pourquoi ça compte: {why_usd(e['title'])}")
            r = reaction(e["t"])
            rows.append(("📈 Réaction réelle — " + r) if r else "📈 Réaction du marché: données indisponibles")
            try:
                rows.append("💧 " + ("; ".join(f"{s['label']} {s['status']}" for s in sweep_events()[:2]) or
                                    "aucun balayage de liquidité détecté depuis"))
            except Exception:
                pass
            rows.append("Source: Forex Factory (flux gratuit)")
            send_telegram("\n".join(rows))
            seen.append(key)
            n += 1
        elif age >= 8:
            key = f"rel:{base}:pending"
            if key in seen:
                continue
            send_telegram(f"⏳ USD — {e['title']}\n{SEP}\nPublié à {e['t'].strftime('%H:%M')} Toronto, "
                          f"mais la valeur « actuel » n'est PAS encore disponible dans la source gratuite "
                          f"(Forex Factory). Prévu: {e['forecast'] or '-'} · Précédent: {e['previous'] or '-'}.\n"
                          "Statut: EN ATTENTE — je ne devine jamais un résultat.")
            seen.append(key)
            n += 1
    return n
