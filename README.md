# Semi-Agentic Outreach System
**AI-powered Monday morning intelligence for Theta Framework consulting**

Run once every Monday → Get 10 researched companies, Theta assessments, and 5 personalized message drafts each.

---

## Quick Start (5 minutes)

### 1. Add your API key
Open `config.yaml` and replace `YOUR-KEY-HERE` with your Anthropic API key:
```yaml
anthropic_api_key: "sk-ant-api03-your-actual-key"
```

### 2. Export your outreach data
Export your tracker as JSON → save it as `data/outreach_import.json`
(Or keep the existing backup file already there.)

### 3. Run the agent
```bash
cd C:\Users\chris\semi-agentic-outreach
python src/outreach_agent.py
```

### 4. Review outputs
- `outputs/monday_strategy.json` — Full research + Theta assessments + message drafts
- `outputs/tuesday_tasks.json` — Ready-to-use task list for your todo tracker
- `outputs/cost_report.json` — What was spent this week

---

## Weekly Workflow

| Day | Action |
|-----|--------|
| **Monday 9am** | Run `python src/outreach_agent.py` → Review outputs (15 min) |
| **Tuesday** | Send 10 messages using the drafted variants |
| **Wednesday–Thursday** | Conversations + writing |
| **Friday** | Update your tracker with responses → agent learns next week |

---

## What It Does

1. **Loads** your full outreach history (all 103+ contacts)
2. **Analyzes patterns** — which messages got responses, which channels work, best timing
3. **Selects 10 companies** to research (Hot priority first, then Medium, timed by funnel stage)
4. **Theta assessment** — maps each company to Core / Edge / Beyond zones, identifies gaps
5. **Drafts 5 message variants** per company — personalized to role, industry, and framework angle
6. **Outputs** strategy JSON + task list + cost report

---

## Theta Framework Zones

| Zone | Timeline | Focus |
|------|----------|-------|
| 🟩 Core | 0–5 years | Optimize what works (90% of business) |
| 🟨 Edge | 3–10 years | Build next S-curve |
| 🟥 Beyond | 7–15+ years | Moonshots and frontier |

**Four Moves:** Deep Audit → Rewire the System → Measure What Matters → Builders Over Storytellers

---

## Budget

- Week 1–4 (cold cache): ~$2.00/week
- Week 5+ (warm cache): ~$1.30/week
- Hard budget limit set in `config.yaml` — agent stops if exceeded

---

## Files

```
semi-agentic-outreach/
├── src/
│   ├── outreach_agent.py      # Run this every Monday
│   ├── theta_framework.py     # Theta zone assessment
│   ├── learning_engine.py     # Pattern tracking
│   ├── cost_tracker.py        # Budget management
│   ├── research_module.py     # Company research (Claude API)
│   └── message_generator.py  # Personalized drafting
├── data/
│   ├── outreach_import.json   # Your tracker export
│   └── learnings.json         # Auto-generated learnings
├── outputs/                   # Auto-generated each Monday
├── config.yaml                # Your settings (never commit this)
└── requirements.txt
```

---

## Security Note
`config.yaml` is in `.gitignore` — your API key will **never** be pushed to GitHub.
After initial setup, rotate your key at [console.anthropic.com](https://console.anthropic.com).
