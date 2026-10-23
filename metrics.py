"""Turns a scan (prompts x AI answers) into dashboard numbers for you and each competitor."""
from collections import defaultdict

from engine import FUNNEL, same

POINTS = {1: 100, 2: 80, 3: 65, 4: 50}  # rank 5+ = 40, named but not in a list = 30, absent = 0


def answers(scan):
    for r in scan["results"]:
        for plat, a in r["answers"].items():
            if a.get("answer"):
                yield r, plat, a


def position(a, aliases):
    for i, n in enumerate(a.get("restaurants", []), 1):
        if any(same(n, al) for al in aliases):
            return i
    return None


def mentioned(a, aliases, rank):
    return rank is not None or any(al.lower() in a["answer"].lower() for al in aliases)


def entity_stats(scan, aliases, domain=None):
    rows = []
    for r, plat, a in answers(scan):
        rank = position(a, aliases)
        m = mentioned(a, aliases, rank)
        pts = 0 if not m else (POINTS.get(rank, 40) if rank else 30)
        cited = bool(domain) and any(domain in (s.get("domain") or "") for s in a.get("sources", []))
        rows.append({"stage": r["stage"], "plat": plat, "m": m, "rank": rank, "pts": pts, "cited": cited, "pid": r["id"]})

    def agg(rs):
        n = len(rs)
        ranks = [x["rank"] for x in rs if x["rank"]]
        return {"n": n, "hits": sum(x["m"] for x in rs),
                "rate": sum(x["m"] for x in rs) / n if n else 0,
                "avg_rank": round(sum(ranks) / len(ranks), 1) if ranks else None,
                "score": round(sum(x["pts"] for x in rs) / n) if n else 0,
                "cited": sum(x["cited"] for x in rs) / n if n else 0}

    out = agg(rows)
    out["prompts_hit"] = len({x["pid"] for x in rows if x["m"]})
    out["stages"] = {f["key"]: agg([x for x in rows if x["stage"] == f["key"]]) for f in FUNNEL}
    out["platforms"] = {p: agg([x for x in rows if x["plat"] == p]) for p in scan["platforms"]}
    return out


def all_names(scan):
    """Every restaurant the AIs named, merged by similar spelling, with counts."""
    counts = []
    for _, _, a in answers(scan):
        for n in set(a.get("restaurants", [])):
            hit = next((c for c in counts if same(c["name"], n)), None)
            if hit:
                hit["count"] += 1
            else:
                counts.append({"name": n, "count": 1})
    return sorted(counts, key=lambda c: -c["count"])


def dashboard(proj, scan):
    prof = proj["profile"]
    me = entity_stats(scan, prof["aliases"], prof.get("domain"))
    names = all_names(scan)
    total_mentions = sum(c["count"] for c in names) or 1

    me["sov"] = me["hits"] / total_mentions
    comps = []
    for c in proj["competitors"]:
        s = entity_stats(scan, [c])
        s["sov"] = s["hits"] / total_mentions
        s["name"] = c
        comps.append(s)

    is_me = lambda n: any(same(n, al) for al in prof["aliases"])
    taken = lambda n: any(same(n, c) for c in proj["competitors"])
    suggestions = [c for c in names if not is_me(c["name"]) and not taken(c["name"])][:6]

    return {"me": me, "comps": comps, "suggestions": suggestions, "issues": issues(proj, scan, me, names),
            "prompts": len(scan["results"]), "answers": me["n"]}


def issues(proj, scan, me, names):
    prof = proj["profile"]
    out = []
    labels = {f["key"]: f["name"] for f in FUNNEL}
    stages = [(k, v) for k, v in me["stages"].items() if v["n"]]
    if stages:
        k, v = min(stages, key=lambda kv: kv[1]["rate"])
        # who wins that stage most when we're missing
        winners = defaultdict(int)
        for r, _, a in answers(scan):
            if r["stage"] == k and position(a, prof["aliases"]) is None and a.get("restaurants"):
                winners[a["restaurants"][0]] += 1
        top = max(winners.items(), key=lambda kv: kv[1])[0] if winners else None
        out.append({"title": f"Weakest stage: {labels[k]} ({round(v['rate']*100)}% visibility)",
                    "detail": f"When customers ask {labels[k].lower()} questions, {prof['name']} is usually left out"
                              + (f"; {top} is named first most often." if top else ".")})
    rivals = [c for c in names if not any(same(c["name"], al) for al in prof["aliases"])]
    if rivals and rivals[0]["count"] > me["hits"]:
        out.append({"title": f"{rivals[0]['name']} is named more often than you",
                    "detail": f"It appears in {rivals[0]['count']} answers vs your {me['hits']}. Add it as a competitor to compare stage by stage."})
    if prof.get("domain") and me["cited"] < 0.15:
        out.append({"title": "AI rarely cites your own website",
                    "detail": f"Only {round(me['cited']*100)}% of answers link to {prof['domain']}. AI is describing you from Yelp, TripAdvisor and listings instead."})
    plats = [(p, v) for p, v in me["platforms"].items() if v["n"]]
    if len(plats) > 1:
        p, v = min(plats, key=lambda kv: kv[1]["rate"])
        best = max(plats, key=lambda kv: kv[1]["rate"])
        if best[1]["rate"] - v["rate"] >= 0.15:
            out.append({"title": f"Weakest on {p}", "detail": f"{round(v['rate']*100)}% visibility on {p} vs {round(best[1]['rate']*100)}% on {best[0]}."})
    return out[:3]
