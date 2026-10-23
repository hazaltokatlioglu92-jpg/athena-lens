"""Lens - AI search health for restaurants.  Run:  python app.py  ->  http://127.0.0.1:5001"""
import json
import os

from dotenv import load_dotenv

HERE = os.path.dirname(os.path.abspath(__file__))
# Use this folder's .env, or reuse the one in ../aiso-agent so keys live in one place
for env in (os.path.join(HERE, ".env"), os.path.join(HERE, "..", "aiso-agent", ".env")):
    if os.path.exists(env):
        load_dotenv(env)
        break

from flask import Flask, abort, jsonify, redirect, render_template, request, url_for  # noqa: E402

import engine  # noqa: E402
import metrics  # noqa: E402

app = Flask(__name__)
PRESETS = json.load(open(os.path.join(HERE, "presets.json"), encoding="utf-8"))
app.jinja_env.filters["pct"] = lambda x: "–" if x is None else f"{round(x * 100)}%"


@app.context_processor
def globals_():
    return {"platforms": engine.platforms(), "funnel": engine.FUNNEL}


@app.route("/")
def home():
    return render_template("home.html", projects=engine.list_projects())


@app.post("/find")
def find():
    name, city = request.form.get("name", "").strip(), request.form.get("city", "").strip()
    if not name:
        return redirect(url_for("home"))
    preset = next((v for k, v in PRESETS.items() if engine.same(k, name)), None)
    try:
        prof = preset or engine.lookup(name, city or "United States")
    except Exception as e:
        return render_template("home.html", projects=engine.list_projects(), error=f"Couldn't look that up: {e}")
    return render_template("find.html", prof=prof, prof_json=json.dumps(prof, ensure_ascii=False))


@app.post("/scan")
def scan():
    prof = json.loads(request.form["profile"])
    prof["aliases"] = [a.strip() for a in request.form.get("aliases", "").split(",") if a.strip()] or prof.get("aliases", [prof["name"]])
    job = engine.start_scan(prof, int(request.form.get("per_stage", 5)))
    return redirect(url_for("scanning", job=job))


@app.post("/p/<slug>/rescan")
def rescan(slug):
    proj = engine.load(slug) or abort(404)
    return redirect(url_for("scanning", job=engine.start_scan(proj["profile"])))


@app.route("/scanning/<job>")
def scanning(job):
    return render_template("scanning.html", job=job)


@app.get("/job/<job>")
def job(job):
    return jsonify(engine.JOBS.get(job, {"status": "Unknown job", "finished": True, "errors": []}))


@app.route("/p/<slug>/")
def dashboard(slug):
    proj = engine.load(slug) or abort(404)
    if not proj["scans"]:
        return redirect(url_for("home"))
    d = metrics.dashboard(proj, proj["scans"][0])
    return render_template("dashboard.html", proj=proj, p=proj["profile"], d=d, scan=proj["scans"][0], page="dashboard")


@app.post("/p/<slug>/competitors")
def competitors(slug):
    proj = engine.load(slug) or abort(404)
    name = request.form.get("name", "").strip()
    if request.form.get("remove"):
        proj["competitors"] = [c for c in proj["competitors"] if c != request.form["remove"]]
    elif name and not any(engine.same(name, c) for c in proj["competitors"]) and len(proj["competitors"]) < 4:
        proj["competitors"].append(name)
    engine.save(proj)
    return redirect(url_for("dashboard", slug=slug))


@app.route("/p/<slug>/searches")
def searches(slug):
    proj = engine.load(slug) or abort(404)
    return render_template("soon.html", proj=proj, p=proj["profile"], page="searches",
                           title="Customer searches", stage=request.args.get("stage"))


if __name__ == "__main__":
    app.run(debug=True, port=5001)
