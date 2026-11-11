"""Niveaux de prix, sessions et liquidité estimée (XAUUSD, DXY, XAGUSD).

Tout est calculé sur de vraies bougies de 15 min (Yahoo Finance). Les zones de liquidité sont des
ESTIMATIONS faites à partir des prix: ce n'est pas de l'information du carnet d'ordres.
"""
import os
import math
import datetime as dt

import core

TZ = core.TZ
UTC = core.UTC

# nom: (symbole Yahoo, décimales, pas des niveaux psychologiques, tolérance equal highs/lows en %)
# symboles Yahoo à essayer dans l'ordre. Les futures (=F) ne sont PAS du CFD spot: écart possible, signalé dans les rapports.
INSTR = {
    "XAUUSD": (["XAUUSD=X", "GC=F"], 2, 50, 0.04),
    "XAGUSD": (["XAGUSD=X", "SI=F"], 3, 1, 0.10),
    "DXY": (["DX-Y.NYB", "DX=F"], 3, 1, 0.05),
}
SRC = {}   # nom -> symbole réellement utilisé
SWING_N = int(os.getenv("SWING_N", "3"))  # bougies 1h de chaque côté pour valider un swing

# clé: (nom, (h, m) ouverture, (h, m) fermeture, jours d'ouverture lun=0) - heure de Toronto
# Modifiable ici sans toucher au reste. Londres et New York se chevauchent de 08:00 à 12:00 (voulu).
SESSION_CFG = {
    "asia": ("ASIAN", (19, 0), (3, 0), {6, 0, 1, 2, 3}),
    "london": ("LONDON", (3, 0), (12, 0), {0, 1, 2, 3, 4}),
    "ny": ("NEW YORK", (8, 0), (17, 0), {0, 1, 2, 3, 4}),
}
SESSION_EMOJI = {"asia": "🌏", "london": "🇬🇧", "ny": "🇺🇸"}


# ---------- données ----------
_cache = {}


def get_bars(name):
    """Bougies 15 min (≈ 1 mois): [(ts_utc, open, high, low, close)]."""
    if name not in _cache:
        errs = []
        for sym in INSTR[name][0]:
            try:
                bars = core.yahoo_bars(sym, "15m", "1mo")
                if len(bars) < 50:
                    raise RuntimeError(f"seulement {len(bars)} bougies")
                _cache[name], SRC[name] = bars, sym
                break
            except Exception as e:
                errs.append(f"{sym}: {str(e)[:60]}")
        else:
            raise RuntimeError(f"{name}: aucune source de prix ({' | '.join(errs)})")
    return _cache[name]


def fp(name, x):
    """Prix avec le même nombre de décimales partout."""
    return f"{x:,.{INSTR[name][1]}f}"


# ---------- jours, semaines, sessions ----------
def _day_key(ts):
    """Journée FX: de 17:00 à 17:00 (Toronto). Les bougies de 17:00+ comptent pour le jour suivant."""
    return (ts.astimezone(TZ) + dt.timedelta(hours=7)).date()


def day_start(d):
    return dt.datetime.combine(d - dt.timedelta(days=1), dt.time(17, 0), TZ)


def daily(bars):
    days = {}
    for ts, o, h, l, c in bars:
        k = _day_key(ts)
        d = days.get(k)
        if d is None:
            days[k] = {"date": k, "open": o, "high": h, "low": l, "close": c, "n": 1}
        else:
            d["high"], d["low"], d["close"], d["n"] = max(d["high"], h), min(d["low"], l), c, d["n"] + 1
    return {k: d for k, d in days.items() if d["n"] >= 8}  # au moins 2 h de données


def weekly(days):
    weeks = {}
    for k in sorted(days):
        d = days[k]
        wk = k.isocalendar()[:2]
        w = weeks.get(wk)
        if w is None:
            weeks[wk] = {"open": d["open"], "high": d["high"], "low": d["low"], "close": d["close"], "start": k}
        else:
            w["high"], w["low"], w["close"] = max(w["high"], d["high"]), min(w["low"], d["low"]), d["close"]
    return weeks


def levels(bars):
    days = daily(bars)
    dk = sorted(days)
    wk = weekly(days)
    wkeys = sorted(wk)
    price = bars[-1][4]
    out = {"price": price, "day": days[dk[-1]], "prev_day": days[dk[-2]] if len(dk) > 1 else None,
           "week": wk[wkeys[-1]], "prev_week": wk[wkeys[-2]] if len(wkeys) > 1 else None}
    base = out["prev_day"]["close"] if out["prev_day"] else out["day"]["open"]
    out["day_chg"] = (price / base - 1) * 100
    return out


def session_windows(key, now, back=1, forward=1):
    """[(ouverture, fermeture)] en heure de Toronto (l'heure d'été est gérée automatiquement)."""
    _, (oh, om), (ch, cm), days = SESSION_CFG[key]
    today = now.astimezone(TZ).date()
    out = []
    for off in range(-back, forward + 1):
        d = today + dt.timedelta(days=off)
        if d.weekday() not in days:
            continue
        o = dt.datetime.combine(d, dt.time(oh, om), TZ)
        cd = d if (ch, cm) > (oh, om) else d + dt.timedelta(days=1)
        out.append((o, dt.datetime.combine(cd, dt.time(ch, cm), TZ)))
    return out


def session_stats(bars, key, now):
    """Hauts/bas de chaque session récente (chronologique). 'en cours' = niveaux en formation."""
    out = []
    for o, c in session_windows(key, now, back=7, forward=0):
        if o > now:
            continue
        bs = [b for b in bars if o <= b[0] < min(c, now)]
        if not bs:
            continue
        out.append({"key": key, "open": o, "close": c, "high": max(b[2] for b in bs),
                    "low": min(b[3] for b in bs), "first": bs[0][1], "last": bs[-1][4],
                    "status": "final" if now >= c else "en cours"})
    return out


def window_stats(name, start, end):
    bs = [b for b in get_bars(name) if start <= b[0] < end]
    if not bs:
        return None
    o, c = bs[0][1], bs[-1][4]
    return {"open": o, "close": c, "high": max(b[2] for b in bs), "low": min(b[3] for b in bs),
            "chg": (c / o - 1) * 100}


# ---------- structure et swings (bougies 1h) ----------
def hourly(bars):
    out = []
    for ts, o, h, l, c in bars:
        hr = ts.replace(minute=0, second=0, microsecond=0)
        if out and out[-1][0] == hr:
            _, oo, hh, ll, _ = out[-1]
            out[-1] = (hr, oo, max(hh, h), min(ll, l), c)
        else:
            out.append((hr, o, h, l, c))
    return out


def swings(hb, n=SWING_N):
    """Swing highs/lows: [(index, ts, prix)]."""
    highs, lows = [], []
    for i in range(n, len(hb) - n):
        lh, rh = max(x[2] for x in hb[i - n:i]), max(x[2] for x in hb[i + 1:i + n + 1])
        ll, rl = min(x[3] for x in hb[i - n:i]), min(x[3] for x in hb[i + 1:i + n + 1])
        if hb[i][2] > lh and hb[i][2] >= rh:
            highs.append((i, hb[i][0], hb[i][2]))
        if hb[i][3] < ll and hb[i][3] <= rl:
            lows.append((i, hb[i][0], hb[i][3]))
    return highs, lows


def structure(hb):
    hs, ls = swings(hb)
    if len(hs) < 2 or len(ls) < 2:
        return {"code": "?", "label": "Indéterminée (pas assez de swings)", "seq": "n/d"}
    hh, hl = hs[-1][2] > hs[-2][2], ls[-1][2] > ls[-2][2]
    seq = f"{'HH' if hh else 'LH'} + {'HL' if hl else 'LL'}"
    if hh and hl:
        return {"code": "bull", "label": f"Haussière ({seq})", "seq": seq}
    if not hh and not hl:
        return {"code": "bear", "label": f"Baissière ({seq})", "seq": seq}
    return {"code": "mixed", "label": f"Mixte ({seq})", "seq": seq}


def active_swings(hb, price):
    """Supports/résistances = swings jamais cassés depuis, les plus proches d'abord."""
    hs, ls = swings(hb)
    res = [(t, v) for i, t, v in hs if v > price and all(b[2] <= v for b in hb[i + 1:])]
    sup = [(t, v) for i, t, v in ls if v < price and all(b[3] >= v for b in hb[i + 1:])]
    return sorted(sup, key=lambda x: -x[1]), sorted(res, key=lambda x: x[1])


def equal_levels(name, hb, price):
    """Equal highs / equal lows non encore balayés (tolérance configurable dans INSTR)."""
    tol = INSTR[name][3] / 100
    hs, ls = swings(hb)
    out = []
    for kind, pts, side in (("EQH", hs, 2), ("EQL", ls, 3)):
        active = []
        for i, t, v in pts:
            later = [b[side] for b in hb[i + 1:]]
            if kind == "EQH" and (not later or max(later) <= v * (1 + tol)):
                active.append((v, t))
            elif kind == "EQL" and (not later or min(later) >= v * (1 - tol)):
                active.append((v, t))
        active.sort()
        groups, group = [], []
        for v, t in active:
            if group and abs(v - group[-1][0]) / v <= tol:
                group.append((v, t))
            else:
                if len(group) >= 2:
                    groups.append(group)
                group = [(v, t)]
        if len(group) >= 2:
            groups.append(group)
        for g in groups:
            vals = [v for v, _ in g]
            out.append({"type": kind, "level": max(vals) if kind == "EQH" else min(vals),
                        "count": len(g), "since": min(t for _, t in g)})
    return sorted(out, key=lambda x: abs(x["level"] - price))


def psych(name, price):
    step = INSTR[name][2]
    dn = math.floor(price / step) * step
    if dn >= price:
        dn -= step
    return dn, dn + step if dn + step > price else dn + 2 * step


def change_over(bars, hours):
    limit = bars[-1][0] - dt.timedelta(hours=hours)
    for b in reversed(bars):
        if b[0] <= limit:
            return (bars[-1][4] / b[4] - 1) * 100
    return None


def correlation(bars_a, bars_b, hours=120):
    """Corrélation observée des rendements horaires (pas une hypothèse)."""
    def rets(bars):
        hb = hourly(bars)[-(hours + 1):]
        return {hb[i][0]: hb[i][4] / hb[i - 1][4] - 1 for i in range(1, len(hb))}
    ra, rb = rets(bars_a), rets(bars_b)
    ks = sorted(set(ra) & set(rb))
    if len(ks) < 24:
        return None
    xs, ys = [ra[k] for k in ks], [rb[k] for k in ks]
    mx, my = sum(xs) / len(xs), sum(ys) / len(ys)
    sx = math.sqrt(sum((x - mx) ** 2 for x in xs))
    sy = math.sqrt(sum((y - my) ** 2 for y in ys))
    if sx == 0 or sy == 0:
        return None
    return sum((x - mx) * (y - my) for x, y in zip(xs, ys)) / (sx * sy)


# ---------- liquidité estimée ----------
def reference_levels(name, bars, asof):
    """Niveaux de référence connus à `asof`: [(label, niveau, 'high'|'low', from_ts)]."""
    past = [b for b in bars if b[0] < asof]
    days = daily(past)
    dk = sorted(days)
    refs = []
    if len(dk) >= 2:
        pd, frm = days[dk[-2]], day_start(dk[-1])
        refs += [("Previous Day High", pd["high"], "high", frm), ("Previous Day Low", pd["low"], "low", frm)]
    wk = weekly(days)
    wkeys = sorted(wk)
    if len(wkeys) >= 2:
        pw, frm = wk[wkeys[-2]], day_start(wk[wkeys[-1]]["start"])
        refs += [("Previous Week High", pw["high"], "high", frm), ("Previous Week Low", pw["low"], "low", frm)]
    for key, cfg in SESSION_CFG.items():
        st = session_stats(past, key, asof)
        if not st:
            continue
        cur = st[-1]
        frm = None if cur["status"] == "en cours" else cur["close"]
        nm = cfg[0].title()
        refs += [(f"{nm} High", cur["high"], "high", frm), (f"{nm} Low", cur["low"], "low", frm)]
    return refs


def check_level(bars, level, kind, frm, to, tol, start=None):
    """intact | balayé (dépassé puis retour dans le range, confirmation à vérifier) | cassé (continuation)."""
    lo = max(frm, start) if start else frm
    bs = [b for b in bars if b[0] >= lo and (to is None or b[0] < to)]
    if not bs:
        return "intact"
    tol_abs = level * tol / 100
    if kind == "high" and max(b[2] for b in bs) > level + tol_abs:
        return "balayé" if bs[-1][4] < level else "cassé"
    if kind == "low" and min(b[3] for b in bs) < level - tol_abs:
        return "balayé" if bs[-1][4] > level else "cassé"
    return "intact"


def evaluate(name, bars, refs, to=None, start=None):
    tol = INSTR[name][3]
    out = []
    for label, level, kind, frm in refs:
        if frm is None:
            if start:   # session encore en formation: ignorée pour un bilan de fin de session
                continue
            st = "en formation"
        else:
            st = check_level(bars, level, kind, frm, to, tol, start)
        out.append({"label": label, "level": level, "kind": kind, "status": st})
    return out


def liquidity_map(name, bars, now):
    price = bars[-1][4]
    items = evaluate(name, bars, reference_levels(name, bars, now))
    eq = equal_levels(name, bars_h := hourly(bars), price)
    for e in eq:
        kind = "high" if e["type"] == "EQH" else "low"
        items.append({"label": f"Equal {'Highs' if kind == 'high' else 'Lows'} ×{e['count']}",
                      "level": e["level"], "kind": kind, "status": "intact"})
    dn, up = psych(name, price)
    items += [{"label": "Psychological", "level": up, "kind": "high", "status": "intact"},
              {"label": "Psychological", "level": dn, "kind": "low", "status": "intact"}]
    live = ("intact", "en formation")
    return {
        "price": price, "items": items, "equal": eq, "psych": (dn, up),
        "bsl": sorted([i for i in items if i["kind"] == "high" and i["level"] > price and i["status"] in live],
                      key=lambda i: i["level"]),
        "ssl": sorted([i for i in items if i["kind"] == "low" and i["level"] < price and i["status"] in live],
                      key=lambda i: -i["level"]),
        "swept": [i for i in items if i["status"] == "balayé"],
        "broken": [i for i in items if i["status"] == "cassé"],
    }


def instrument(name, now=None):
    """Tout ce qu'il faut savoir sur un instrument, calculé sur de vraies bougies."""
    now = now or dt.datetime.now(TZ)
    bars = get_bars(name)
    hb = hourly(bars)
    lv = levels(bars)
    sup, res = active_swings(hb, lv["price"])
    return {"name": name, "bars": bars, "price": lv["price"], "lv": lv, "struct": structure(hb),
            "sup": sup[:2], "res": res[:2], "chg4h": change_over(bars, 4),
            "liq": liquidity_map(name, bars, now),
            "sessions": {k: session_stats(bars, k, now) for k in SESSION_CFG},
            "last": bars[-1][0].astimezone(TZ)}
