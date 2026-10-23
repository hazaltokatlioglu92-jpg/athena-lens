# Lens: AI search health for restaurants

Marketer's tool: search your restaurant, get a health dashboard of how ChatGPT, Gemini and Claude recommend it, and compare against competitors.

## Run it (Mac, VS Code)

Put the `athena-lens` folder in Downloads, next to `aiso-agent`. It reuses that folder's Python setup and API keys, so there's nothing to install.

1. VS Code: File > New Window, then File > Open Folder > Downloads > athena-lens > Open
2. Terminal > New Terminal, then paste:

```bash
source ../aiso-agent/.venv/bin/activate && python app.py
```

3. Open http://127.0.0.1:5001

It runs on port 5001, so the old app (5000) can run at the same time.

## Screens

- **Start**: type the restaurant name and city. Café Athena is preloaded; any other name is looked up on the web.
- **Health dashboard**: AI Visibility Score, shows-up rate, average position, share of voice, own-site citations, funnel stages (Discover, Occasion, Specific needs, Decide), top issues, and results by AI platform.
- **Compare bar**: add up to 4 competitors (type or click a suggestion). Every number gets a comparison. No extra AI calls are needed.
- Customer searches, Fix it, Track: next.

## How the score works

Each customer prompt is asked on every connected AI. Per answer: named 1st = 100, 2nd = 80, 3rd = 65, 4th = 50, 5th or lower = 40, mentioned outside a list = 30, not named = 0. The score is the average.

## Files

`app.py` routes · `engine.py` lookup, prompts, AI calls, scans · `metrics.py` dashboard numbers · `funnel.json` funnel stages (edit names here) · `presets.json` known restaurants · `data/projects/` saved results.
