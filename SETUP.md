# Setup Guide
**Semi-Agentic Outreach System**

---

## Prerequisites

- Python 3.12.4 (you have this ✓)
- Git (you have this ✓)
- Anthropic API key with at least $5 loaded

---

## Step 1 — Install dependencies

Open Command Prompt and run:

```
cd C:\Users\chris\semi-agentic-outreach
pip install -r requirements.txt
```

Expected output:
```
Successfully installed anthropic-0.40.0 pyyaml-6.0 colorama-0.4.6 ...
```

---

## Step 2 — Configure your API key

Open `config.yaml` in any text editor (Notepad works fine):

```yaml
anthropic_api_key: "sk-ant-api03-YOUR-KEY-HERE"  ← replace this
```

Replace `YOUR-KEY-HERE` with your actual key from [console.anthropic.com](https://console.anthropic.com).

**Important:** `config.yaml` is in `.gitignore` — it will never be pushed to GitHub.

---

## Step 3 — Add your outreach data

Your data is already in `data/outreach_import.json` (the backup you provided).

To refresh it in future:
1. Export from your outreach tracker as JSON
2. Overwrite `data/outreach_import.json`

---

## Step 4 — First run

```
cd C:\Users\chris\semi-agentic-outreach
python src/outreach_agent.py
```

First run takes ~5 minutes (no cache yet). Subsequent runs use cached research and are faster + cheaper.

---

## Step 5 — Rotate your API key

After the first successful run, go to [console.anthropic.com](https://console.anthropic.com), revoke the current key, and create a new one. Update `config.yaml` with the new key.

---

## Costs

| Run | Approximate cost |
|-----|-----------------|
| Week 1–4 (cold cache) | ~$2.00 |
| Week 5+ (warm cache)  | ~$1.30 |

The budget is enforced — the agent stops if you're about to exceed `weekly_budget` in `config.yaml`.

---

## Folder structure

```
semi-agentic-outreach/
├── src/                     ← Python code (don't edit unless you want to)
│   ├── outreach_agent.py    ← Main file — run this every Monday
│   ├── theta_framework.py   ← Theta zone logic
│   ├── learning_engine.py   ← Pattern tracking
│   ├── cost_tracker.py      ← Budget management
│   ├── research_module.py   ← Company research
│   └── message_generator.py ← Message drafting
├── data/
│   ├── outreach_import.json ← Your tracker export
│   ├── learnings.json       ← Auto-generated, grows over time
│   └── cache/               ← Cached research (saves money)
├── outputs/                 ← Generated every Monday
│   ├── monday_strategy.json ← Full results
│   ├── tuesday_tasks.json   ← Task list
│   └── cost_report.json     ← Spend report
├── config.yaml              ← Your settings (NEVER commit this)
└── requirements.txt
```
