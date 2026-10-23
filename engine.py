"""Lens engine: find the restaurant, write customer prompts, ask each AI, read where everyone lands."""
import json
import os
import re
import threading
import time
import unicodedata
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from urllib.parse import urlparse

import requests

CLAUDE_MODEL = os.getenv("CLAUDE_MODEL", "claude-sonnet-4-5")
FAST_MODEL = os.getenv("CLAUDE_FAST_MODEL", "claude-haiku-4-5")
OPENAI_MODEL = os.getenv("OPENAI_MODEL", "gpt-4o-mini")
GEMINI_MODEL = os.getenv("GEMINI_MODEL", "gemini-2.5-flash")

ROOT = os.path.dirname(os.path.abspath(__file__))
PROJ_DIR = os.path.join(ROOT, "data", "projects")
FUNNEL = json.load(open(os.path.join(ROOT, "funnel.json"), encoding="utf-8"))
JOBS = {}


# ---------------- helpers ----------------

def norm(s):
    s = unicodedata.normalize("NFKD", s or "").encode("ascii", "ignore").decode().lower()
    s = re.sub(r"[^a-z0-9 ]", " ", s)
    s = re.sub(r"\b(the|restaurant|cafe|bar|grill|kitchen|and|&)\b", " ", s)
    return re.sub(r"\s+", " ", s).strip()


def same(a, b):
    a, b = norm(a), norm(b)
    return bool(a and b) and (a == b or a in b or b in a)


def slugify(s):
    s = unicodedata.normalize("NFKD", s or "").encode("ascii", "ignore").decode().lower()
    return re.sub(r"[^a-z0-9]+", "-", s).strip("-") or "restaurant"


def _domain(url):
    d = urlparse(url or "").netloc.lower()
    return d[4:] if d.startswith("www.") else d


def _json(text, kind="{"):
    m = re.search(r"\{.*\}" if kind == "{" else r"\[.*\]", text or "", re.S)
    return json.loads(m.group(0)) if m else ({} if kind == "{" else [])


def _claude(prompt, model=None, max_tokens=2000, web=False, system=None):
    import anthropic
    kw = dict(model=model or CLAUDE_MODEL, max_tokens=max_tokens, messages=[{"role": "user", "content": prompt}])
    if system:
        kw["system"] = system
    if web:
        kw["tools"] = [{"type": "web_search_20250305", "name": "web_search", "max_uses": 4}]
    return anthropic.Anthropic().messages.create(**kw)


def _text(msg):
    return "".join(b.text for b in msg.content if getattr(b, "type", "") == "text")


def platforms():
    p = []
    if os.getenv("OPENAI_API_KEY"):
        p.append("ChatGPT")
    if os.getenv("GEMINI_API_KEY"):
        p.append("Gemini")
    if os.getenv("ANTHROPIC_API_KEY"):
        p.append("Claude")
    return p


# ---------------- 1. find the restaurant ----------------

def lookup(name, city):
    prompt = f"""Find the restaurant "{name}" in "{city}" on the web.
Return only JSON with keys: name, address, area (neighborhood + city), website, cuisine (short), aliases (other spellings of its name, list).
If you are not sure it exists, still return your best match."""
    data = _json(_text(_claude(prompt, web=True, max_tokens=1500)))
    data["domain"] = _domain(data.get("website", ""))
    data["aliases"] = list({*(data.get("aliases") or []), data.get("name", name)})
    return data


# ---------------- 2. write customer prompts ----------------

def write_prompts(profile, per_stage):
    stages = "\n".join(f"- {f['key']} ({f['name']}): {f['desc']}" for f in FUNNEL)
    prompt = f"""You write realistic questions diners type into ChatGPT, Gemini or Claude when choosing where to eat.

Area: {profile['area']}
Restaurant being audited: {profile['name']} ({profile['cuisine']}), {profile['address']}

Write {per_stage} questions for EACH funnel stage:
{stages}

Rules: Only "decide" questions may name {profile['name']}. All others must NOT name it.
Use local place names and how locals talk. Mix short and long, conversational questions.
Return only JSON: {{"discover": [...], "occasion": [...], "needs": [...], "decide": [...]}}"""
    data = _json(_text(_claude(prompt, max_tokens=3000)))
    out, i = [], 1
    for f in FUNNEL:
        for q in data.get(f["key"], [])[:per_stage]:
            out.append({"id": f"P{i}", "q": q, "stage": f["key"]})
            i += 1
    return out


# ---------------- 3. ask the AIs ----------------

def _system(profile):
    return (f"You are a helpful assistant. The user is in {profile['area']}. When they ask where to eat, "
            "recommend specific real restaurants by name, best first, with a short reason each. Search the web if you can.")


def _with_retry(fn):
    for wait in (0, 8, 20):
        time.sleep(wait)
        try:
            return fn()
        except requests.HTTPError as e:
            if e.response is not None and e.response.status_code in (429, 503) and wait != 20:
                continue
            raise


def ask_claude(q, system):
    import anthropic
    msg = anthropic.Anthropic().messages.create(
        model=CLAUDE_MODEL, max_tokens=1200, system=system,
        tools=[{"type": "web_search_20250305", "name": "web_search", "max_uses": 3}],
        messages=[{"role": "user", "content": q}])
    text, src = [], []
    for b in msg.content:
        t = getattr(b, "type", "")
        if t == "text":
            text.append(b.text)
            for c in (getattr(b, "citations", None) or []):
                if getattr(c, "url", None):
                    src.append({"title": getattr(c, "title", ""), "url": c.url})
    return {"answer": "".join(text).strip(), "sources": src}


def ask_chatgpt(q, system):
    h = {"Authorization": f"Bearer {os.getenv('OPENAI_API_KEY')}"}

    def web():
        r = requests.post("https://api.openai.com/v1/responses", headers=h, timeout=120, json={
            "model": OPENAI_MODEL, "instructions": system, "input": q, "tools": [{"type": "web_search_preview"}]})
        r.raise_for_status()
        text, src = [], []
        for item in r.json().get("output", []):
            for c in item.get("content", []) if item.get("type") == "message" else []:
                if c.get("type") == "output_text":
                    text.append(c.get("text", ""))
                    src += [{"title": a.get("title", ""), "url": a["url"]} for a in c.get("annotations", []) if a.get("url")]
        return {"answer": "".join(text).strip(), "sources": src}

    try:
        return _with_retry(web)
    except Exception:
        def plain():
            r = requests.post("https://api.openai.com/v1/chat/completions", headers=h, timeout=120, json={
                "model": OPENAI_MODEL, "messages": [{"role": "system", "content": system}, {"role": "user", "content": q}]})
            r.raise_for_status()
            return {"answer": r.json()["choices"][0]["message"]["content"], "sources": []}
        return _with_retry(plain)


def ask_gemini(q, system):
    url = (f"https://generativelanguage.googleapis.com/v1beta/models/{GEMINI_MODEL}:generateContent"
           f"?key={os.getenv('GEMINI_API_KEY')}")
    body = {"system_instruction": {"parts": [{"text": system}]}, "contents": [{"parts": [{"text": q}]}]}

    def call(grounded):
        r = requests.post(url, timeout=120, json={**body, "tools": [{"google_search": {}}]} if grounded else body)
        r.raise_for_status()
        return r.json()

    try:
        j = _with_retry(lambda: call(True))
    except Exception:
        j = _with_retry(lambda: call(False))
    cand = j["candidates"][0]
    text = "".join(p.get("text", "") for p in cand.get("content", {}).get("parts", []))
    src = [{"title": c["web"].get("title", ""), "url": c["web"].get("uri", ""), "domain": c["web"].get("title", "")}
           for c in cand.get("groundingMetadata", {}).get("groundingChunks", []) if c.get("web")]
    return {"answer": text.strip(), "sources": src}


ASK = {"ChatGPT": ask_chatgpt, "Gemini": ask_gemini, "Claude": ask_claude}


def read_names(answers):
    """Ordered restaurant names recommended in each platform's answer (one fast Claude call)."""
    blocks = "\n\n".join(f"### {p}\n{a['answer']}" for p, a in answers.items() if a.get("answer"))
    if not blocks:
        return {}
    try:
        msg = _claude("For each answer below, list every restaurant it recommends, in the order recommended.\n\n"
                      f"{blocks}\n\nReturn only JSON: {{\"<platform>\": [\"name\", ...]}}", model=FAST_MODEL, max_tokens=1000)
        return _json(_text(msg))
    except Exception:
        return {}


def ask_all(prompt, profile, plats):
    system = _system(profile)
    answers = {}
    with ThreadPoolExecutor(max_workers=3) as ex:
        futs = {p: ex.submit(ASK[p], prompt["q"], system) for p in plats}
        for p, f in futs.items():
            try:
                answers[p] = f.result()
            except Exception as e:
                answers[p] = {"answer": "", "sources": [], "error": str(e)[:200]}
    names = read_names(answers)
    for p, a in answers.items():
        a["restaurants"] = [n for n in names.get(p, []) if isinstance(n, str)]
        for s in a["sources"]:
            s["domain"] = s.get("domain") or _domain(s.get("url"))
    return {**prompt, "answers": answers}


# ---------------- projects + scan job ----------------

def path(slug):
    return os.path.join(PROJ_DIR, f"{slug}.json")


def load(slug):
    if not os.path.exists(path(slug)):
        return None
    return json.load(open(path(slug), encoding="utf-8"))


def save(proj):
    os.makedirs(PROJ_DIR, exist_ok=True)
    json.dump(proj, open(path(proj["slug"]), "w", encoding="utf-8"), indent=2, ensure_ascii=False)


def list_projects():
    if not os.path.isdir(PROJ_DIR):
        return []
    return [json.load(open(os.path.join(PROJ_DIR, f), encoding="utf-8")) for f in sorted(os.listdir(PROJ_DIR)) if f.endswith(".json")]


def start_scan(profile, per_stage=5):
    slug = slugify(profile["name"])
    proj = load(slug) or {"slug": slug, "profile": profile, "competitors": [], "prompts": [], "scans": []}
    proj["profile"] = profile
    job = f"{slug}-{int(time.time())}"
    JOBS[job] = {"status": "Writing customer prompts…", "done": 0, "total": 1, "slug": slug, "finished": False, "errors": []}

    def work():
        try:
            plats = platforms()
            if not proj["prompts"]:
                proj["prompts"] = write_prompts(profile, per_stage)
            prompts = proj["prompts"]
            JOBS[job].update(status="Asking ChatGPT, Gemini and Claude…", total=len(prompts))
            results = []

            def one(p):
                r = ask_all(p, profile, plats)
                JOBS[job]["done"] += 1
                for plat, a in r["answers"].items():
                    if a.get("error"):
                        JOBS[job]["errors"].append(f"{plat}: {a['error'][:120]}")
                return r

            with ThreadPoolExecutor(max_workers=3) as ex:
                results = list(ex.map(one, prompts))
            proj["scans"].insert(0, {"date": datetime.now().strftime("%Y-%m-%d %H:%M"), "platforms": plats, "results": results})
            save(proj)
            JOBS[job]["status"] = "Done"
        except Exception as e:
            JOBS[job]["errors"].append(str(e)[:300])
            JOBS[job]["status"] = "Failed"
        JOBS[job]["finished"] = True

    threading.Thread(target=work, daemon=True).start()
    return job
