# Weekly Workflow Guide
**Semi-Agentic Outreach System | Theta Framework**

---

## Monday — Run the Agent (20 min)

### 1. Open Command Prompt
```
cd C:\Users\chris\semi-agentic-outreach
```

### 2. Run the agent
```
python src/outreach_agent.py
```

You'll see live progress:
```
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
  🚀  SEMI-AGENTIC OUTREACH — MONDAY CYCLE
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
  Date: Monday, February 24 2026 09:00

📂  Loading outreach data...
  ✓ 103 contacts loaded, 45 historical touchpoints

📊  Analyzing historical patterns...
  Channel response rates:
    LinkedIn     [██████░░░░] 68%  (19/28 replied)
    Email        [████████░░] 75%  (3/4 replied)

🎯  Selecting this week's targets...
  ✓ 10 companies selected

[1/10] BCG
  🔍 Researching...
  ✓ Research complete
  🧩 Running Theta assessment...
  ✓ 🟩 Primary zone: Core | Archetype: Stuck in Core
  ✍️  Drafting messages...
  ✓ 5 message variants drafted

... (continues for all 10)

✅  Complete!
  Processed: 10 companies
  💰 Budget: [█████████░░░░░░░░░░░] $1.78 / $2.00 (89%)
```

### 3. Review outputs (15 min)
Open `outputs/monday_strategy.json` — this has everything:
- Theta assessment for each company
- 5 message variants per company
- Recommended send time
- Rationale for each angle

---

## Tuesday — Execute Outreach

Use `outputs/tuesday_tasks.json` as your task list:
- 10 tasks, one per company
- Recommended message already included
- Ordered by priority (Hot first)

**Tips:**
- Pick the variant that feels most natural to you
- Personalize the `[specific detail]` placeholders
- Stick to the send time window — timing matters

---

## Wednesday–Thursday — Conversations

When someone responds:
1. Log the response in your outreach tracker
2. Note the sentiment (positive / neutral / negative)
3. Use the "second touch" strategy from `monday_strategy.json`

---

## Friday — Reflection

Update your tracker with:
- Who responded
- What they said
- Sentiment

This data feeds the learning engine. The more you log, the smarter the agent gets next Monday.

**Key question to reflect on:**
> *What angle or observation triggered the best conversations this week?*

---

## Troubleshooting

### "API key not configured"
Open `config.yaml` and replace `YOUR-KEY-HERE` with your real Anthropic API key.

### "Outreach data not found"
Export your tracker as JSON and save it to `data/outreach_import.json`.

### "Budget limit reached"
Reduce `companies_per_week` to 8 in `config.yaml`. Costs drop after week 4 as cache fills up.

### Agent runs slowly
Normal — each company requires 2–3 API calls. 10 companies takes ~3–5 minutes.
