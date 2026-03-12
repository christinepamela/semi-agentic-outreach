"""
Semi-Agentic Outreach System v3.6 — Hybrid DeepSeek + Claude
=====================================================
CHANGES FROM v3.5:
  [1] Case study NOW feeds into Theta scoring — case study text is combined with
      research background before scoring, so signals from YOUR content count.
  [2] Cache bypass when case study present — if you provide a case study for a
      company, the old cached research is ignored and fresh research runs.
      Cache is then updated with the new case-study-informed result.
  [3] DeepSeek handles research (cheap) — hunt_new_companies and
      batch_research_companies now use DeepSeek (OpenAI-compatible API).
      Cost for 5 companies drops from ~$0.20 to ~$0.02.
  [4] Claude handles everything requiring judgment — message drafting,
      follow-up replies, CEO weekly review. Quality where it matters.

INSTALL (first time only):
  pip install anthropic pyyaml openai

CONFIG (add to config.yaml):
  anthropic_api_key: sk-ant-...
  deepseek_api_key: sk-...         ← get from platform.deepseek.com
  weekly_budget: 2.00
  user_name: Christine Pamela
  user_short_name: Pam

THREE MODES:
  python outreach_agent_v3_6.py           → Monday: audit + hunt + plan
  python outreach_agent_v3_6.py --status  → Any day: quick check, no AI, no cost
  python outreach_agent_v3_6.py --friday  → Friday: upload JSON, CEO review
"""

import json
import uuid
import sys
import os
import re
import hashlib
import argparse
import random
from datetime import datetime, timezone, timedelta
from pathlib import Path
from collections import defaultdict

try:
    import anthropic
except ImportError:
    print("❌  Run: pip install anthropic")
    sys.exit(1)

try:
    import yaml
except ImportError:
    print("❌  Run: pip install pyyaml")
    sys.exit(1)

try:
    from openai import OpenAI as OpenAIClient
    OPENAI_AVAILABLE = True
except ImportError:
    OPENAI_AVAILABLE = False

# ─────────────────────────────────────────────────────────────
# PATHS & CONFIG
# ─────────────────────────────────────────────────────────────

TRACKER_PATH   = "data/outreach_import.json"
LEARNINGS_PATH = "data/learnings.json"
CACHE_DIR      = Path("data/cache")
OUTPUTS_DIR    = Path("outputs")

EXPORT_PATH    = "outputs/new_contacts_for_import.json"
FOLLOWUP_PATH  = "outputs/followup_actions.json"
TASKS_PATH     = "outputs/tuesday_tasks.json"
COST_PATH      = "outputs/cost_report.json"
REVIEW_PATH    = "outputs/friday_review.txt"

ALL_PLATFORMS = ["LinkedIn", "Email", "Twitter/X", "WhatsApp", "Substack", "Newsletter", "Conference", "Personal Intro"]

ALL_INDUSTRIES = [
    "Automotive", "Aerospace & Defence", "Banking & Financial Services",
    "Chemicals", "Consumer Goods / FMCG", "Energy & Utilities",
    "Food & Beverage", "Healthcare & MedTech", "Industrial / Manufacturing",
    "Insurance", "Logistics & Supply Chain", "Luxury & Fashion",
    "Mining & Metals", "Oil & Gas", "Pharmaceuticals & Life Sciences",
    "Professional Services / Consulting", "Real Estate & Infrastructure",
    "Retail", "Semiconductor & Electronics", "Technology / Software",
    "Telecoms & Media", "Travel & Hospitality",
]

def load_config():
    path = Path("config.yaml")
    if not path.exists():
        print("❌  config.yaml not found. Run from project root.")
        sys.exit(1)
    with open(path) as f:
        cfg = yaml.safe_load(f)
    if "YOUR-KEY-HERE" in cfg.get("anthropic_api_key", ""):
        print("❌  Add your Anthropic API key to config.yaml")
        sys.exit(1)
    return cfg

# ─────────────────────────────────────────────────────────────
# MODEL CLIENTS
# ─────────────────────────────────────────────────────────────

def get_claude_client(config: dict):
    """Claude — for message drafting, follow-ups, CEO review. Quality tasks."""
    return anthropic.Anthropic(api_key=config["anthropic_api_key"])

def get_deepseek_client(config: dict):
    """
    DeepSeek — for company hunting and Theta research. Cheap and fast.
    Uses OpenAI-compatible API. Falls back to Claude if no key configured.
    """
    if not OPENAI_AVAILABLE:
        print("  ⚠️  openai package not found. Run: pip install openai")
        print("  ⚠️  Falling back to Claude for research (more expensive).")
        return None

    ds_key = config.get("deepseek_api_key", "")
    if not ds_key or "YOUR" in ds_key or len(ds_key) < 10:
        print("  ℹ️  No DeepSeek API key in config.yaml — using Claude for research.")
        print("  ℹ️  Add deepseek_api_key to config.yaml to save ~90% on research costs.")
        return None

    return OpenAIClient(
        api_key=ds_key,
        base_url="https://api.deepseek.com/v1"
    )

def call_deepseek(ds_client, prompt: str, max_tokens: int = 4000) -> str:
    """Call DeepSeek with OpenAI-compatible interface."""
    response = ds_client.chat.completions.create(
        model="deepseek-chat",
        messages=[{"role": "user", "content": prompt}],
        max_tokens=max_tokens,
        temperature=0.1,  # Low temp for consistent structured output
    )
    return response.choices[0].message.content.strip()

def call_claude(claude_client, prompt: str, max_tokens: int = 800) -> str:
    """Call Claude for high-quality reasoning tasks."""
    r = claude_client.messages.create(
        model="claude-haiku-4-5-20251001",
        max_tokens=max_tokens,
        messages=[{"role": "user", "content": prompt}]
    )
    return r.content[0].text.strip()

# ─────────────────────────────────────────────────────────────
# TRACKER JSON LOADER
# ─────────────────────────────────────────────────────────────

def load_tracker_json(path: str) -> dict:
    p = Path(path)
    if not p.exists():
        print(f"❌  File not found: {path}")
        sys.exit(1)
    try:
        raw = json.loads(p.read_text(encoding="utf-8"))
    except json.JSONDecodeError as e:
        print(f"❌  JSON parse error in {path}: {e}")
        sys.exit(1)
    if isinstance(raw, list):
        return {"contacts": raw, "learnings": [], "weeklyGoals": {}, "monthlyGoals": {}}
    contacts = raw.get("contacts", [])
    return {
        "contacts":            contacts,
        "learnings":           raw.get("learnings", []),
        "weeklyGoals":         raw.get("weeklyGoals", {}),
        "weeklyGoalsHistory":  raw.get("weeklyGoalsHistory", []),
        "monthlyGoals":        raw.get("monthlyGoals", {}),
        "monthlyGoalsHistory": raw.get("monthlyGoalsHistory", []),
        "templates":           raw.get("templates", []),
        "exportDate":          raw.get("exportDate", ""),
        "version":             raw.get("version", ""),
        "source_file":         path,
    }

# ─────────────────────────────────────────────────────────────
# DISPLAY HELPERS
# ─────────────────────────────────────────────────────────────

def hr(char="─", n=62): print(char * n)
def banner(t): print(); hr("═"); print(f"  {t}"); hr("═")
def section(t): print(); hr(); print(f"  {t}"); hr()

def ask(prompt, options=None):
    if options:
        opts = " / ".join(f"[{o}]" for o in options)
        prompt = f"{prompt}  {opts}: "
    while True:
        r = input(prompt).strip().lower()
        if options is None or r in [o.lower() for o in options]:
            return r
        print(f"  Please enter: {', '.join(options)}")

def day_greeting() -> str:
    day = datetime.now().strftime("%A")
    greetings = {
        "Monday":    "🗓️  Monday — Strategy & Planning day.",
        "Tuesday":   "📨  Tuesday — Outreach execution day. Let's check what's queued.",
        "Wednesday": "✍️   Wednesday — Writing & case study day.",
        "Thursday":  "💬  Thursday — Conversations & follow-up day.",
        "Friday":    "🪞  Friday — Reflection day. Run --friday to review your week.",
        "Saturday":  "🌿  Saturday — Rest. The agent can wait until Monday.",
        "Sunday":    "🌿  Sunday — Rest. The agent can wait until Monday.",
    }
    return greetings.get(day, f"📅  {day}")

# ─────────────────────────────────────────────────────────────
# COST TRACKER
# ─────────────────────────────────────────────────────────────

class CostTracker:
    # DeepSeek costs are ~10x cheaper than Claude Haiku for same tasks
    COSTS = {
        "hunt":                  0.02,   # DeepSeek (was 0.15 with Claude)
        "research":              0.01,   # DeepSeek single (was 0.12)
        "research_batch":        0.03,   # DeepSeek batch 5 companies (was 0.20)
        "cached_research":       0.01,
        "cached_people":         0.00,
        "draft":                 0.05,   # Claude — kept for quality
        "followup_draft":        0.03,   # Claude — kept for quality
        "theta":                 0.00,   # Local scoring — free
        "channels":              0.02,
        "analysis":              0.08,
        "reflection":            0.10,
        "ceo_review":            0.15,   # Claude — kept for quality
    }

    def __init__(self, budget: float):
        self.budget = budget
        self.spent  = 0.0
        self.log    = []

    def charge(self, op: str, note: str = "") -> float:
        cost = self.COSTS.get(op, 0.0)
        self.spent += cost
        self.log.append({"op": op, "cost": cost, "note": note,
                         "ts": datetime.now().isoformat()})
        return cost

    def remaining(self) -> float:
        return max(0.0, self.budget - self.spent)

    def can_afford(self, op: str) -> bool:
        return (self.spent + self.COSTS.get(op, 0)) <= self.budget

    def summary(self) -> str:
        pct = min(100, int((self.spent / self.budget) * 100))
        bar = "█" * (pct // 5) + "░" * (20 - pct // 5)
        return f"[{bar}] ${self.spent:.2f} / ${self.budget:.2f} ({pct}%)"

    def projection(self, remaining_companies: int) -> str:
        per_company = self.COSTS["cached_research"] + self.COSTS["theta"] + self.COSTS["channels"] + self.COSTS["draft"]
        projected   = per_company * remaining_companies
        return (
            f"\n  💰 Current: ${self.spent:.2f} | Remaining: ${self.remaining():.2f}"
            f"\n  📊 Projected for {remaining_companies} companies: ~${projected:.2f} total"
            + (f"\n  ⚠️  May exceed budget by ${projected - self.remaining():.2f}" if projected > self.remaining() else "")
        )

    def save(self):
        OUTPUTS_DIR.mkdir(exist_ok=True)
        with open(COST_PATH, "w") as f:
            json.dump({
                "week_of":     datetime.now().strftime("%Y-%m-%d"),
                "total_spent": round(self.spent, 4),
                "budget":      self.budget,
                "remaining":   round(self.remaining(), 4),
                "operations":  self.log,
            }, f, indent=2)

# ─────────────────────────────────────────────────────────────
# RESEARCH CACHE
# ─────────────────────────────────────────────────────────────

CACHE_TTL = {"background": 90, "news": 7, "people": 30}

def cache_key(company: str) -> str:
    safe = re.sub(r"[^a-z0-9]", "_", company.lower().strip())
    return safe[:40] or hashlib.md5(company.encode()).hexdigest()[:8]

def load_cache(company: str) -> dict:
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    path = CACHE_DIR / f"{cache_key(company)}.json"
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text())
        now  = datetime.now()
        out  = {}
        for field, ttl in CACHE_TTL.items():
            if field in data:
                cached_at = datetime.fromisoformat(data.get(f"{field}_at", "2000-01-01"))
                if (now - cached_at).days < ttl:
                    out[field] = data[field]
        return out
    except Exception:
        return {}

def save_cache(company: str, fields: dict):
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    path = CACHE_DIR / f"{cache_key(company)}.json"
    data = {}
    if path.exists():
        try: data = json.loads(path.read_text())
        except: pass
    now = datetime.now().isoformat()
    for k, v in fields.items():
        data[k] = v
        data[f"{k}_at"] = now
    path.write_text(json.dumps(data, indent=2))

def load_cached_people(company: str) -> list:
    cached = load_cache(company)
    return cached.get("people", [])

def save_cached_people(company: str, people: list):
    save_cache(company, {"people": people})

# ─────────────────────────────────────────────────────────────
# ARCHETYPE SYSTEM — Simple 1-6, Visual Bars
# ─────────────────────────────────────────────────────────────

VALID_ARCHETYPES = [
    "1. Core Heavy",
    "2. Edge Active",
    "3. Beyond Funded",
    "4. Balanced",
    "5. Core + Edge",
    "6. Theater Risk",
]

def display_archetype(arch: str) -> str:
    if arch and '.' in arch:
        return arch.split('.', 1)[1].strip()
    return arch

def determine_archetype(core: int, edge: int, beyond: int, theater: int) -> str:
    if theater >= 3:
        return "6. Theater Risk"
    if core >= 7 and edge < 3 and beyond < 2:
        return "1. Core Heavy"
    if beyond >= 4:
        return "3. Beyond Funded"
    if core >= 5 and edge >= 5 and beyond < 2:
        return "5. Core + Edge"
    if edge >= 5 and core >= 4 and beyond < 3:
        return "2. Edge Active"
    if 4 <= core <= 7 and 4 <= edge <= 7 and 2 <= beyond <= 4:
        return "4. Balanced"
    return "4. Balanced"

def get_focus_area(archetype: str) -> str:
    if "Core Heavy"    in archetype: return "protecting the core and building what comes next"
    if "Edge Active"   in archetype: return "scaling Edge bets without destabilizing the core"
    if "Beyond Funded" in archetype: return "bridging long-term research to commercial reality"
    if "Balanced"      in archetype: return "balancing today's performance with tomorrow's growth"
    if "Core + Edge"   in archetype: return "building the next curve while setting up long-term bets"
    if "Theater Risk"  in archetype: return "moving from pilots and showcases to scalable impact"
    return "managing the tension between today's core and tomorrow's bets"

def display_theta_visual(company_name: str, theta: dict):
    zones = theta['zone_distribution']
    core_bar   = "🟩" * zones['core']   + "⬜" * (10 - zones['core'])
    edge_bar   = "🟨" * zones['edge']   + "⬜" * (10 - zones['edge'])
    beyond_bar = "🟥" * zones['beyond'] + "⬜" * (10 - zones['beyond'])
    print(f"\n  Zones:")
    print(f"    Core   {core_bar}  {zones['core']}/10")
    print(f"    Edge   {edge_bar}  {zones['edge']}/10")
    print(f"    Beyond {beyond_bar}  {zones['beyond']}/10")
    print(f"\n  Pattern:  {display_archetype(theta.get('archetype',''))}")
    print(f"  Pain:     {theta.get('pain_point','')}")
    print(f"  Angle:    {theta.get('messaging_angle','')}")
    if theta.get('theater_risk', 0) >= 2:
        print(f"  ⚠️  Theater risk score: {theta['theater_risk']}/10")
    if theta.get("case_study_informed"):
        print(f"  📎  Scores informed by your case study")

# ─────────────────────────────────────────────────────────────
# LEARNING ENGINE
# ─────────────────────────────────────────────────────────────

ANGLE_KEYWORDS = {
    "case_study":           ["case study", "example", "dbs", "siemens", "telco", "intel"],
    "portfolio_governance": ["portfolio", "governance", "portfolio governance"],
    "research_observation": ["i noticed", "i've been studying", "i've been following", "i look at"],
    "pain_point_question":  ["how are you thinking", "how do you", "curious how", "what's your approach"],
    "market_insight":       ["market", "trend", "shift", "disruption", "s-curve"],
    "technical_depth":      ["digital twin", "xcelerator", "ecosystem", "platform", "architecture"],
    "peer_acknowledgment":  ["spot on", "you're right", "great point", "i agree", "resonates"],
    "article_share":        ["i wrote", "i published", "article", "medium", "substack"],
}

def classify_seniority(title: str) -> str:
    t = title.lower()
    if any(x in t for x in ["cto","chief technology","chief digital","vp engineering"]):
        return "cto"
    if any(x in t for x in ["ceo","chief executive","president","managing director"," md "]):
        return "ceo"
    if any(x in t for x in ["cso","chief strategy","chief innovation","chief transformation"]):
        return "cso"
    if any(x in t for x in ["partner","principal","director","vp","vice president"]):
        return "vp_director"
    if any(x in t for x in ["innovation","r&d","research","transformation","strategy"]):
        return "innovation_lead"
    return "other"

def analyze_patterns(contacts: list) -> dict:
    channel_stats    = defaultdict(lambda: {"sent":0,"responded":0,"positive":0})
    engagement_stats = defaultdict(lambda: {"count":0,"responses":0})
    angle_wins       = defaultdict(int)
    angle_attempts   = defaultdict(int)
    seniority_stats  = defaultdict(lambda: {"sent":0,"responded":0,"positive":0,"angles":[]})
    industry_stats   = defaultdict(lambda: {"sent":0,"responded":0})
    timing           = []

    for c in contacts:
        logs      = c.get("communicationLog", [])
        title     = c.get("jobTitle", "")
        seniority = classify_seniority(title)
        industry  = c.get("industryFocus", "Other")

        for log in logs:
            ch        = log.get("channel", "LinkedIn")
            eng_type  = log.get("engagementType", "")
            response  = (log.get("response","") or "").strip()
            sentiment = log.get("sentiment","")
            msg       = (log.get("message","") or "").lower()
            date_str  = log.get("date","")
            responded = bool(response) and response.lower() not in ["no response","none"]
            positive  = sentiment in ("Positive","Very positive")

            channel_stats[ch]["sent"] += 1
            if responded: channel_stats[ch]["responded"] += 1
            if positive:  channel_stats[ch]["positive"]  += 1

            if eng_type:
                engagement_stats[eng_type]["count"] += 1
                if responded: engagement_stats[eng_type]["responses"] += 1

            seniority_stats[seniority]["sent"] += 1
            if responded: seniority_stats[seniority]["responded"] += 1
            if positive:  seniority_stats[seniority]["positive"]  += 1

            industry_stats[industry]["sent"] += 1
            if responded: industry_stats[industry]["responded"] += 1

            for angle, kws in ANGLE_KEYWORDS.items():
                if any(kw in msg for kw in kws):
                    angle_attempts[angle] += 1
                    if responded:
                        angle_wins[angle] += 1
                        if angle not in seniority_stats[seniority]["angles"]:
                            seniority_stats[seniority]["angles"].append(angle)

            if date_str and responded:
                try:
                    dt = datetime.fromisoformat(date_str.replace("Z","+00:00"))
                    timing.append({"day": dt.strftime("%A"), "hour": dt.hour, "channel": ch})
                except: pass

    channel_rates = {}
    for ch, s in channel_stats.items():
        rate = (s["responded"] / s["sent"] * 100) if s["sent"] else 0
        channel_rates[ch] = {**s, "rate_pct": round(rate,1)}

    angle_rates = {}
    for angle in set(list(angle_wins.keys()) + list(angle_attempts.keys())):
        attempts = angle_attempts.get(angle, 0)
        wins     = angle_wins.get(angle, 0)
        rate     = (wins / attempts * 100) if attempts else 0
        angle_rates[angle] = {"attempts": attempts, "wins": wins, "rate_pct": round(rate,1)}

    seniority_rates = {}
    for level, s in seniority_stats.items():
        rate = (s["responded"] / s["sent"] * 100) if s["sent"] else 0
        seniority_rates[level] = {**s, "rate_pct": round(rate,1)}

    industry_rates = {}
    for ind, s in industry_stats.items():
        rate = (s["responded"] / s["sent"] * 100) if s["sent"] else 0
        industry_rates[ind] = {**s, "rate_pct": round(rate,1)}

    day_counts  = defaultdict(int)
    hour_counts = defaultdict(int)
    for t in timing:
        day_counts[t["day"]] += 1
        hour_counts[t["hour"] // 3 * 3] += 1
    best_day  = max(day_counts,  key=day_counts.get)  if day_counts  else "Tuesday"
    best_hour = max(hour_counts, key=hour_counts.get) if hour_counts else 9

    top_channel  = max(channel_rates,  key=lambda k: channel_rates[k]["rate_pct"],  default="Email")
    top_angle    = max(angle_rates,    key=lambda k: angle_rates[k]["rate_pct"],    default="research_observation")
    top_industry = max(industry_rates, key=lambda k: industry_rates[k]["rate_pct"], default="")

    return {
        "analyzed_at":      datetime.now().isoformat(),
        "channel_rates":    channel_rates,
        "angle_rates":      angle_rates,
        "seniority_rates":  seniority_rates,
        "industry_rates":   industry_rates,
        "engagement_stats": {k: {**v, "rate_pct": round(v["responses"]/v["count"]*100,1) if v["count"] else 0}
                             for k, v in engagement_stats.items()},
        "top_channel":      top_channel,
        "top_angle":        top_angle,
        "top_industry":     top_industry,
        "best_day":         best_day,
        "best_hour":        best_hour,
        "best_send_window": f"{best_day} {best_hour:02d}:00–{best_hour+3:02d}:00",
    }

def load_learnings() -> dict:
    path = Path(LEARNINGS_PATH)
    if path.exists():
        try: return json.loads(path.read_text())
        except: pass
    return {
        "history":               [],
        "patterns":              {},
        "strategy_notes":        [],
        "archetype_corrections": {},
        "industry_performance":  {},
        "last_tracker_file":     "",
    }

def save_learnings(data: dict):
    Path(LEARNINGS_PATH).parent.mkdir(exist_ok=True)
    with open(LEARNINGS_PATH, "w") as f:
        json.dump(data, f, indent=2)

def get_messaging_strategy(contact: dict, patterns: dict) -> dict:
    title     = contact.get("jobTitle","")
    seniority = classify_seniority(title)
    top_angle = patterns.get("top_angle","research_observation")
    top_ch    = patterns.get("top_channel","LinkedIn")

    role_strategies = {
        "cto": {
            "style": "technical_depth",
            "opening": "specific_tech_observation",
            "note": "CTOs respond to ecosystem/platform language.",
        },
        "ceo": {
            "style": "strategic_vision",
            "opening": "market_insight",
            "note": "CEOs respond to portfolio-level questions and peer pressure framing.",
        },
        "cso": {
            "style": "portfolio_governance",
            "opening": "pain_point_question",
            "note": "Strategy officers respond to governance and KPI misalignment framing.",
        },
        "vp_director": {
            "style": "problem_focused",
            "opening": "research_observation",
            "note": "Directors appreciate specific observations over vision-level openers.",
        },
        "innovation_lead": {
            "style": "practitioner_peer",
            "opening": "peer_acknowledgment",
            "note": "Innovation leads respond to 'builders over storytellers' framing.",
        },
    }

    strategy = role_strategies.get(seniority, {
        "style": "curiosity_led",
        "opening": "observation",
        "note": "Default: open with curiosity, no framework mention.",
    })

    return {
        **strategy,
        "seniority":            seniority,
        "top_angle":            top_angle,
        "recommended_channel":  top_ch,
        "best_send":            patterns.get("best_send_window","Tuesday 09:00–12:00"),
    }

# ─────────────────────────────────────────────────────────────
# INDUSTRY GAP ANALYSIS
# ─────────────────────────────────────────────────────────────

def analyze_industry_gaps(contacts: list, patterns: dict) -> dict:
    industry_counts = defaultdict(int)
    for c in contacts:
        ind = c.get("industryFocus", "").strip()
        if ind:
            industry_counts[ind] += 1

    covered = {k: v for k, v in industry_counts.items() if v > 0}
    gaps = []
    for ind in ALL_INDUSTRIES:
        covered_count = sum(v for k, v in covered.items()
                            if any(w.lower() in k.lower() for w in ind.split(" / ")[0].split()))
        gaps.append({"industry": ind, "count": covered_count})

    gaps_sorted       = sorted(gaps, key=lambda x: x["count"])
    underrepresented  = [g["industry"] for g in gaps_sorted if g["count"] == 0][:8]
    present           = [g["industry"] for g in sorted(gaps, key=lambda x: -x["count"]) if g["count"] > 0]

    ind_rates        = patterns.get("industry_rates", {})
    best_performing  = sorted(ind_rates.items(), key=lambda x: -x[1]["rate_pct"])[:3]

    return {
        "covered":            covered,
        "underrepresented":   underrepresented,
        "present_industries": present,
        "best_performing":    [b[0] for b in best_performing if b[1]["rate_pct"] > 0],
    }

def select_industries_interactively(contacts: list, patterns: dict) -> list:
    section("INDUSTRY SELECTION")
    gaps = analyze_industry_gaps(contacts, patterns)

    print("\n  Your tracker by industry:")
    for ind, cnt in sorted(gaps["covered"].items(), key=lambda x: -x[1])[:10]:
        bar = "█" * min(cnt, 20)
        print(f"    {ind:40s} {bar} {cnt}")

    if gaps["best_performing"]:
        print(f"\n  🏆 Best responding industries in your history: {', '.join(gaps['best_performing'])}")

    print(f"\n  Industries with NO contacts yet (fresh territory):")
    for i, ind in enumerate(gaps["underrepresented"], 1):
        print(f"    {i:2}. {ind}")

    print()
    choice = ask("  Industry selection?", ["recommended", "pick", "random"])

    if choice == "recommended":
        suggested = gaps["underrepresented"][:2]
        if gaps["best_performing"]:
            suggested.append(gaps["best_performing"][0])
        elif gaps["present_industries"]:
            suggested.append(gaps["present_industries"][0])
        if len(suggested) < 3 and gaps["present_industries"]:
            suggested.append(gaps["present_industries"][0])
        suggested = list(dict.fromkeys(suggested))[:4]
        print(f"\n  Suggested mix: {', '.join(suggested)}")
        confirm = ask("  Use this?", ["y", "n"])
        if confirm == "y":
            return suggested

    if choice == "pick" or (choice == "recommended" and confirm == "n"):
        print(f"\n  All industries:")
        for i, ind in enumerate(ALL_INDUSTRIES, 1):
            print(f"    {i:2}. {ind}")
        print()
        raw = input("  Enter numbers, comma-separated (e.g. 3,7,12): ").strip()
        try:
            indices  = [int(x.strip()) - 1 for x in raw.split(",") if x.strip().isdigit()]
            selected = [ALL_INDUSTRIES[i] for i in indices if 0 <= i < len(ALL_INDUSTRIES)]
            if selected:
                print(f"  Selected: {', '.join(selected)}")
                return selected
        except Exception:
            pass
        print("  Invalid selection, using random.")

    selected = random.sample(ALL_INDUSTRIES, min(3, len(ALL_INDUSTRIES)))
    print(f"  Random: {', '.join(selected)}")
    return selected

def select_target_count() -> int:
    print()
    raw = input("  How many new target companies this week? [5 minimum, default 5]: ").strip()
    if raw.isdigit():
        n = int(raw)
        return max(5, n)
    return 5

def select_country_focus() -> str:
    print()
    raw = input("  Country/region focus? (e.g. 'Germany', 'Southeast Asia') or Enter to skip: ").strip()
    return raw

# ─────────────────────────────────────────────────────────────
# THETA FRAMEWORK SIGNALS
# ─────────────────────────────────────────────────────────────

CORE_SIGNALS   = [
    "operational excellence","efficiency","optimization","cost reduction",
    "process improvement","quality","reliability","scale","margin","profitability",
    "customer retention","traditional","incumbent","legacy","core business",
    "existing products","sustaining","agile","lean","continuous improvement",
    "cost discipline","productivity","supply chain optimization","erp","six sigma",
    "reformulation","line extension","pricing","mix management","revenue management",
]
EDGE_SIGNALS   = [
    "pilot","experiment","venture","new business","adjacent","digital transformation",
    "platform","ecosystem","partnership","spin-off","incubator","accelerator",
    "next generation","growth initiative","s-curve","new market","emerging","startup",
    "product launch","beta","new revenue","digital twin","xcelerator","open innovation",
    "business model innovation","new venture","corporate venture","innovation unit",
    "digital business","data platform","ai transformation","cloud transformation",
    "direct-to-consumer","dtc","e-commerce","personalization","plant-based",
]
BEYOND_SIGNALS = [
    "moonshot","quantum","deep tech","10x","breakthrough","research lab",
    "fundamental research","2030","2035","2040","future of","reinvent","disruption",
    "frontier","autonomous","fusion","biotech","nanotechnology","ai research",
    "basic research","horizon 3","beyond","long-term bet","venture studio",
    "exponential","synthetic biology","space","climate tech","net zero 2040",
    "microbiome","precision nutrition","cellular agriculture","fermentation",
    "gut-brain","neuronutrition","biofortification","precision fermentation",
    "metabolic","clinical","mayo clinic","r&d lab","research center","deep tech center",
]
THEATER_SIGNALS = [
    "innovation lab","innovation hub","digital lab","center of excellence",
    "hackathon","ideation","prototype","proof of concept","poc",
    "we're exploring","looking into","vision for","roadmap for 2030",
    "innovation theater","announce","showcase","award",
    "innovation day","pitch competition","startup program","innovation challenge",
]

THETA_FRAMEWORK_BRIEF = """
THE THETA FRAMEWORK — Core Definitions:

ZONES:
- 🟩 CORE: Incremental improvements to existing products/markets. Optimizes current business.
- 🟨 EDGE: Next S-curve bets. High tech change or new market. Not yet mainstream.
- 🟥 BEYOND: Speculative, long-horizon. Transformational or Frontier R&D. 7–15+ year horizon.

ARCHETYPES (simple pattern labels):
1. Core Heavy   — Core dominant, little Edge, no Beyond. Optimizing into potential irrelevance.
2. Edge Active  — Building next S-curve. Active Edge, limited long-horizon bets.
3. Beyond Funded — Long-term bets exist but may lack commercial bridge.
4. Balanced     — Active across all three zones. Governance is the opportunity.
5. Core + Edge  — Strong present + next curve. No long-term vision yet.
6. Theater Risk — Labs/announcements, little evidence of real scaling.

KEY PRINCIPLE: Beyond ≠ moonshots only. Includes any transformational bet with 7–15+ year horizon.
Do NOT miss Beyond investments — companies like Nestlé have significant ones
(microbiome, precision nutrition, cellular agriculture, gut-brain research).
"""

def score_signals(text: str, signals: list) -> int:
    return min(10, sum(1 for s in signals if s in text.lower()))

ARCHETYPE_PAIN = {
    "1. Core Heavy":    "Over-indexed on Core — next S-curve missing, disruption risk growing",
    "2. Edge Active":   "Building next curve but no long-horizon bets — may run out of runway",
    "3. Beyond Funded": "Long-term bets exist but lack a bridge from R&D to commercial Edge",
    "4. Balanced":      "Active across all zones — portfolio governance and metrics alignment is the opportunity",
    "5. Core + Edge":   "Strong present + next curve, but no long-term vision being funded",
    "6. Theater Risk":  "Labs and pilots don't ship — strong on announcement, weak on scaling",
}

def theta_assess(research_text: str, company_name: str, industry: str,
                 case_study_text: str = "",
                 override_archetype: str = None) -> dict:
    """
    [FIX v3.6] Case study text is now included in scoring.
    Combined text = research background + case study content.
    This means signals from YOUR case study count toward the zone scores.
    """
    # Combine research + case study for scoring
    combined_text = research_text
    case_study_informed = False
    if case_study_text and len(case_study_text) > 50:
        combined_text = research_text + "\n\n" + case_study_text
        case_study_informed = True

    text    = combined_text.lower()
    core    = score_signals(text, CORE_SIGNALS)
    edge    = score_signals(text, EDGE_SIGNALS)
    beyond  = score_signals(text, BEYOND_SIGNALS)
    theater = score_signals(text, THEATER_SIGNALS)

    if override_archetype and any(override_archetype in a for a in VALID_ARCHETYPES):
        archetype = next((a for a in VALID_ARCHETYPES if override_archetype in a), override_archetype)
    elif override_archetype and override_archetype in VALID_ARCHETYPES:
        archetype = override_archetype
    else:
        archetype = determine_archetype(core, edge, beyond, theater)

    pain    = ARCHETYPE_PAIN.get(archetype, "Portfolio tension visible")
    angle   = f"How does {company_name} govern the tension between {get_focus_area(archetype)}?"
    primary = max({"core":core,"edge":edge,"beyond":beyond}, key=lambda k:{"core":core,"edge":edge,"beyond":beyond}[k])
    zone_emoji = {"core":"🟩","edge":"🟨","beyond":"🟥"}

    gaps = []
    if edge < 3:     gaps.append("No visible Edge / next S-curve work")
    if beyond < 2:   gaps.append("No long-horizon Beyond bets detected")
    if theater >= 3: gaps.append("Innovation theater risk: labs don't ship")
    if core >= 7 and edge < 3: gaps.append("Core-heavy: disruption vulnerability")
    if not gaps:     gaps.append("Portfolio reasonably active — governance clarity is the opportunity")

    return {
        "zone_distribution":   {"core": core, "edge": edge, "beyond": beyond},
        "theater_risk":        theater,
        "primary_zone":        primary,
        "archetype":           archetype,
        "pain_point":          pain,
        "gaps":                gaps,
        "messaging_angle":     angle,
        "zone_summary":        f"{zone_emoji.get(primary,'🟨')} | {display_archetype(archetype)}",
        "case_study_informed": case_study_informed,
    }

# ─────────────────────────────────────────────────────────────
# STATUS CHECK
# ─────────────────────────────────────────────────────────────

def status_check(contacts: list, learnings: dict):
    banner(f"📋  DAILY STATUS CHECK  |  {datetime.now().strftime('%A %B %d %Y')}")
    print(f"  {day_greeting()}\n")

    now        = datetime.now(timezone.utc)
    today      = datetime.now().date()
    week_start = today - timedelta(days=today.weekday())

    urgent = []
    for c in contacts:
        logs  = c.get("communicationLog",[])
        real  = lambda l: (l.get("response","") or "").strip() and \
                           (l.get("response","") or "").lower() not in ["no response","none"]
        if len(logs) == 1 and real(logs[0]):
            urgent.append(c)
        elif len(logs) >= 2:
            last = logs[0]
            if real(last) and not real(logs[1] if len(logs)>1 else {}):
                urgent.append(c)

    print(f"  🔴  NEED YOUR REPLY ({len(urgent)}):")
    if urgent:
        for c in urgent[:5]:
            resp = (c.get("communicationLog",[{}])[0].get("response","") or "")[:80]
            print(f"     • {c['name']} @ {c['company']} — \"{resp}{'...' if len(resp)==80 else ''}\"")
    else:
        print("     None — you're up to date.")

    due_today = []
    overdue   = []
    for c in contacts:
        nd = c.get("nextActionDate")
        if not nd: continue
        try:
            nd_date = datetime.fromisoformat(nd.replace("Z","")).date()
            if nd_date == today:
                due_today.append(c)
            elif nd_date < today:
                overdue.append({**c, "_days_over": (today - nd_date).days})
        except: pass

    print(f"\n  📅  DUE TODAY ({len(due_today)}):")
    for c in due_today[:5]:
        print(f"     • {c['name']} @ {c['company']} [{c.get('priority','')}]")
    if not due_today:
        print("     Nothing due today.")

    print(f"\n  ⚠️   OVERDUE ({len(overdue)}):")
    for c in sorted(overdue, key=lambda x: -x["_days_over"])[:5]:
        print(f"     • {c['name']} @ {c['company']} — {c['_days_over']}d overdue")
    if not overdue:
        print("     Nothing overdue.")

    funnel_counts = defaultdict(int)
    for c in contacts:
        funnel_counts[c.get("funnelStage","Unaware")] += 1

    print(f"\n  📊  PIPELINE:")
    for stage in ["Unaware","Awareness","Engaged","Consideration","Active Conversation","Conversion"]:
        count = funnel_counts.get(stage, 0)
        print(f"     {stage:22s} {'█' * count} {count}")

    this_week_touches   = 0
    this_week_responses = 0
    for c in contacts:
        for log in c.get("communicationLog",[]):
            try:
                ld = datetime.fromisoformat(log["date"].replace("Z","")).date()
                if ld >= week_start:
                    this_week_touches += 1
                    if (log.get("response","") or "").strip():
                        this_week_responses += 1
            except: pass

    print(f"\n  📬  THIS WEEK: {this_week_touches} touches sent, {this_week_responses} responses received")

    strategy_notes = learnings.get("strategy_notes",[])
    if strategy_notes:
        print(f"\n  💡  LAST STRATEGY NOTE:")
        for line in strategy_notes[-1].splitlines()[:4]:
            if line.strip(): print(f"     {line}")
    print()

# ─────────────────────────────────────────────────────────────
# AUDIT
# ─────────────────────────────────────────────────────────────

def audit_tracker(contacts: list) -> tuple:
    urgent, warm, stale = [], [], []
    now = datetime.now(timezone.utc)

    for c in contacts:
        logs   = c.get("communicationLog",[])
        status = c.get("connectionStatus","")
        real_response = lambda l: (l.get("response","") or "").strip() and \
                                   (l.get("response","") or "").lower() not in ["no response","none"]

        if logs and real_response(logs[0]) and (len(logs) == 1 or not real_response(logs[1] if len(logs) > 1 else {})):
            urgent.append({**c,
                           "_their_response": logs[0]["response"],
                           "_our_message":    logs[0].get("message",""),
                           "_channel":        logs[0].get("channel","LinkedIn"),
                           "_date":           logs[0].get("date","")})
        elif status == "Connected" and not logs:
            warm.append(c)
        elif logs and not any(real_response(l) for l in logs):
            last_date = logs[0].get("date","") if logs else ""
            days_ago  = 999
            if last_date:
                try:
                    sent = datetime.fromisoformat(last_date.replace("Z","+00:00"))
                    days_ago = (now - sent).days
                except: pass
            if days_ago > 10:
                stale.append({**c, "_days_ago": days_ago, "_touches": len(logs)})

    return urgent, warm, stale

def display_audit(urgent, warm, stale, patterns):
    banner("STEP 1 — TRACKER AUDIT")
    print(f"  {day_greeting()}\n")

    cr = patterns.get("channel_rates",{})
    if cr:
        print("  📊 Your historical response rates:")
        for ch, s in sorted(cr.items(), key=lambda x: -x[1]["rate_pct"]):
            bar = "█" * int(s["rate_pct"]/10) + "░" * (10 - int(s["rate_pct"]/10))
            print(f"     {ch:14s} [{bar}] {s['rate_pct']:.0f}%  ({s['responded']}/{s['sent']})")
        ir = patterns.get("industry_rates",{})
        if ir:
            top = sorted(ir.items(), key=lambda x: -x[1]["rate_pct"])
            if top: print(f"\n  🏭 Best responding industry: {top[0][0]} ({top[0][1]['rate_pct']:.0f}%)")
        print(f"  🎯 Best angle: {patterns.get('top_angle','—')}  |  Best time: {patterns.get('best_send_window','—')}")

    print(f"\n  🔴  URGENT — Responded, awaiting your reply: {len(urgent)}")
    for i, c in enumerate(urgent, 1):
        resp       = (c.get("_their_response","") or "")[:100]
        resp_lower = resp.lower()
        touches    = len(c.get("communicationLog",[]))
        print(f"\n  {i}. {c['name']} @ {c['company']} [{c.get('priority','')}]")
        if c.get("jobTitle"): print(f"     {c['jobTitle']}")
        print(f"     Channel: {c.get('_channel','LinkedIn')}")
        print(f"     Their reply: \"{resp}{'...' if len(resp)==100 else ''}\"")
        if not resp.strip() or resp_lower in ["no response","none",""]:
            print(f"     ⚠️  No real response — consider dropping")
        elif any(phrase in resp_lower for phrase in ["not part of","not in","just curious","not my area","not my team"]) and touches >= 2:
            print(f"     ⚠️  Weak signal (not decision-maker) — consider dropping")

    print(f"\n  🟡  WARM — Connected, never messaged: {len(warm)}")
    for c in warm:
        print(f"     • {c.get('name','')} @ {c.get('company','')} [{c.get('priority','')}]  {c.get('jobTitle','')}")

    print(f"\n  ⬜  STALE — {len(stale)} contacts messaged 10+ days ago, no response")
    warm_by_company = {}
    for w in warm:
        co = (w.get("company","") or "").strip().lower()
        if co: warm_by_company.setdefault(co, []).append(w)

    for c in sorted(stale, key=lambda x: x.get("_days_ago",0), reverse=True)[:5]:
        co_key = (c.get("company","") or "").strip().lower()
        line   = f"     • {c.get('name','')} @ {c.get('company','')} ({c.get('_days_ago',0)}d ago, {c.get('_touches',1)} touch)"
        if co_key in warm_by_company:
            alt_names = ", ".join(a.get("name","") for a in warm_by_company[co_key][:2])
            line += f"\n       ⚡ Stronger contact at same company: {alt_names} — consider pivoting"
        print(line)

# ─────────────────────────────────────────────────────────────
# FOLLOW-UP DRAFTS (Claude — quality matters)
# ─────────────────────────────────────────────────────────────

def draft_reply(claude_client, contact: dict, config: dict, patterns: dict) -> str:
    strategy   = get_messaging_strategy(contact, patterns)
    name       = (contact.get("name","") or "").split()[0] or "there"
    company    = contact.get("company","")
    title      = contact.get("jobTitle","")
    their_resp = contact.get("_their_response","")
    our_msg    = contact.get("_our_message","")
    channel    = contact.get("_channel","LinkedIn")
    user_name  = config.get("user_short_name","Pam")

    prompt = f"""You are drafting a reply for {config.get('user_name','Christine Pamela')}, an innovation consultant (Theta Framework).

CONTACT: {name} — {title} at {company}
CHANNEL: {channel}
SENIORITY TYPE: {strategy['seniority']}

PAM'S ORIGINAL MESSAGE:
{our_msg}

THEIR RESPONSE:
{their_resp}

Draft 3 reply variants. Each should:
- Acknowledge their specific response genuinely
- Move toward a 20-minute conversation naturally
- Warm, not salesy — {name} already responded positively
- For {channel}: {'max 4 sentences' if channel == 'LinkedIn' else 'max 6 sentences'}
- Sign off as "{user_name}"
- Variant 3: suggest a specific time slot or soft Calendly ask

Format:
---REPLY 1---
[message]

---REPLY 2---
[message]

---REPLY 3---
[message]"""

    try:
        return call_claude(claude_client, prompt, max_tokens=700)
    except Exception as e:
        return f"---REPLY 1---\nHi {name},\n\nThank you for responding — I'd love to set up a brief call.\n\nWould 20 minutes work this week?\n\nBest,\n{user_name}"

def approval_loop_followup(contact: dict, draft_text: str):
    section(f"FOLLOW-UP: {contact.get('name','')} @ {contact.get('company','')}")
    print(f"  {contact.get('jobTitle','')} | {contact.get('priority','')} priority")
    resp = contact.get("_their_response","")
    print(f"\n  Their reply:\n  \"{resp[:200]}{'...' if len(resp)>200 else ''}\"")
    print("\n  ── REPLY DRAFTS ──\n")

    replies = []
    for block in draft_text.split("---REPLY")[1:]:
        text = block.strip()
        if text and text[:2].strip().isdigit():
            text = text[3:].strip()
        if text and not text.startswith("---"):
            replies.append(text)

    for i, reply in enumerate(replies[:3], 1):
        print(f"  [{i}]")
        for line in reply.splitlines():
            print(f"      {line}")
        print()

    choice = input("  Send? [1/2/3=pick / e=edit / n=skip]: ").strip().lower()
    if choice == "n": return None
    if choice in ["1","2","3"]:
        idx = int(choice)-1
        return replies[idx] if idx < len(replies) else replies[0]
    if choice == "e":
        print("  Paste reply (blank line to finish):")
        lines = []
        while True:
            line = input()
            if line == "": break
            lines.append(line)
        return "\n".join(lines).strip()
    return replies[0] if replies else None

# ─────────────────────────────────────────────────────────────
# HUNT NEW COMPANIES — DeepSeek (cheap, fast)
# ─────────────────────────────────────────────────────────────

def hunt_new_companies(ds_client, claude_client, existing_companies: list, config: dict,
                       industries: list, country_focus: str, target_count: int) -> list:
    section("STEP 2 — HUNTING NEW COMPANIES")
    industry_str = ", ".join(industries) if industries else "any major industry"
    country_str  = f"Prefer companies in or with major operations in: {country_focus}." if country_focus else "Global — any geography."
    print(f"  Industries: {industry_str}")
    print(f"  Geography:  {country_str}")
    print(f"  Count:      {target_count}")
    model_name = "DeepSeek" if ds_client else "Claude"
    print(f"  Model:      {model_name} (researching...)")

    existing_str = ", ".join(sorted(set(existing_companies)))

    prompt = f"""{THETA_FRAMEWORK_BRIEF}

You are a business intelligence researcher for Christine Pamela, an innovation consultant (Theta Framework).

INDUSTRIES THIS WEEK: {industry_str}
GEOGRAPHY: {country_str}

DO NOT SUGGEST (already in tracker): {existing_str}

Identify exactly {target_count} large enterprises (2000+ employees) with visible tension between
optimizing their core business and building next-generation growth.

You MUST output exactly {target_count} companies.

Output EXACTLY this format for each:

---COMPANY---
NAME: [Company]
INDUSTRY: [Industry]
COUNTRY: [HQ country]
SIZE: [Approx employees]
WHY_THETA_FIT: [2 sentences on innovation tension]
THETA_ARCHETYPE: [1. Core Heavy / 2. Edge Active / 3. Beyond Funded / 4. Balanced / 5. Core + Edge / 6. Theater Risk]
STRENGTHS: [1 sentence on what they do well]
PAIN_POINTS: [1-2 sentences on visible innovation gaps]
RECENT_SIGNAL: [One specific recent initiative or announcement]
TARGET_ROLES: ["Company" "Chief Innovation Officer" | "Company" "VP Digital Transformation" | etc — 3-5 ranked]
BEST_CHANNEL: [LinkedIn / Email / Twitter/X / Other — with brief reason]"""

    try:
        if ds_client:
            raw = call_deepseek(ds_client, prompt, max_tokens=4500)
        else:
            raw = call_claude(claude_client, prompt, max_tokens=4500)

        companies = []
        for block in raw.split("---COMPANY---")[1:]:
            c = {}
            for line in block.strip().splitlines():
                for field in ["NAME","INDUSTRY","COUNTRY","SIZE","WHY_THETA_FIT",
                              "THETA_ARCHETYPE","STRENGTHS","PAIN_POINTS","RECENT_SIGNAL",
                              "TARGET_ROLES","BEST_CHANNEL"]:
                    if line.startswith(f"{field}:"):
                        c[field.lower()] = line[len(field)+1:].strip()
            if c.get("name"):
                companies.append(c)
        print(f"  ✓ {len(companies)} companies identified via {model_name}")
        return companies[:target_count]
    except Exception as e:
        print(f"  ✗ Hunt failed: {e}")
        return []

# ─────────────────────────────────────────────────────────────
# CASE STUDY / ARTICLE FETCH
# ─────────────────────────────────────────────────────────────

def ask_case_study_context(company_name: str, industry: str) -> dict:
    print(f"\n  ── YOUR CONTENT FOR {company_name.upper()} ──")
    context = {"has_case_study": False, "building_case_study": False,
               "case_study_note": "", "case_study_text": "", "articles": [], "article_texts": []}

    has_cs = ask(f"  Case study relevant to {company_name} or {industry}?", ["y","n"])
    if has_cs == "y":
        context["has_case_study"] = True
        raw = input("  Paste URL or brief description: ").strip()
        if raw.startswith("http"):
            context["case_study_url"]  = raw
            context["case_study_note"] = f"case study at {raw}"
            try:
                import urllib.request
                req = urllib.request.Request(raw, headers={"User-Agent": "Mozilla/5.0"})
                with urllib.request.urlopen(req, timeout=8) as resp:
                    html = resp.read().decode("utf-8", errors="ignore")
                text = re.sub(r"<[^>]+>", " ", html)
                text = re.sub(r"\s+", " ", text).strip()
                context["case_study_text"] = text[:4000]
                print(f"  ✓ Case study fetched ({len(context['case_study_text'])} chars) — will inform Theta scoring")
            except Exception as e:
                print(f"  ⚠️  Could not fetch URL ({e}). Using description only.")
                context["case_study_note"] = raw
                context["case_study_text"] = raw  # Use description as fallback text
        else:
            context["case_study_note"] = raw
            context["case_study_text"] = raw  # Description also counts for scoring
    else:
        building = ask(f"  Building one?", ["y","n"])
        if building == "y":
            context["building_case_study"] = True

    has_art = ask(f"  Articles you've written relevant to them?", ["y","n"])
    if has_art == "y":
        print("  Paste URLs one per line (blank line to finish):")
        articles = []
        while True:
            line = input("  > ").strip()
            if not line: break
            articles.append(line)
        context["articles"] = articles
        for url in articles[:2]:
            try:
                import urllib.request
                req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
                with urllib.request.urlopen(req, timeout=8) as resp:
                    html = resp.read().decode("utf-8", errors="ignore")
                text = re.sub(r"<[^>]+>", " ", html)
                text = re.sub(r"\s+", " ", text).strip()
                context["article_texts"].append(text[:2000])
                print(f"  ✓ Article fetched: {url[:60]}...")
            except:
                pass

    return context

# ─────────────────────────────────────────────────────────────
# BATCH RESEARCH — DeepSeek (cheap) + case study scoring fix
# ─────────────────────────────────────────────────────────────

def batch_research_companies(ds_client, claude_client, companies: list, cost: CostTracker,
                              cs_contexts: dict = None) -> dict:
    """
    Research all companies via DeepSeek (cheap) or Claude (fallback).

    [FIX v3.6 — Case Study]:
    - If a case study exists for a company, the cache is BYPASSED so fresh
      research incorporates the case study content.
    - case_study_text is passed into theta_assess() so signals from YOUR
      content count toward zone scores.

    [FIX v3.6 — DeepSeek]:
    - Uses DeepSeek for research (10x cheaper than Claude).
    - Falls back to Claude if no DeepSeek key configured.
    """
    results     = {}
    to_research = []
    cs_contexts = cs_contexts or {}

    for company in companies:
        name       = company.get("name","")
        cs_context = cs_contexts.get(name, {})
        has_cs     = bool(cs_context.get("case_study_text",""))

        # [FIX] Bypass cache if case study present — old cache won't have it
        if has_cs:
            print(f"  📎  {name}: has case study — bypassing cache for fresh scoring")
            to_research.append(company)
            continue

        cached = load_cache(name)
        if "background" in cached:
            cs_text = cs_context.get("case_study_text","")
            theta   = theta_assess(cached["background"], name,
                                   company.get("industry",""),
                                   case_study_text=cs_text)
            results[name] = {
                "background": cached["background"],
                "theta":      theta,
                "was_cached": True,
            }
            cost.charge("cached_research", note=f"{name} (cached)")
        else:
            to_research.append(company)

    if not to_research:
        return results

    model_name = "DeepSeek" if ds_client else "Claude"
    print(f"\n  📡  Batch researching {len(to_research)} companies via {model_name}...")

    # Build case study inserts for the prompt
    cs_inserts = ""
    for company in to_research:
        name = company.get("name","")
        ctx  = cs_contexts.get(name, {})
        if ctx.get("case_study_text"):
            cs_inserts += f"\n\nPAM'S CASE STUDY FOR {name.upper()}:\n{ctx['case_study_text'][:2000]}"
            cs_inserts += "\nIMPORTANT: Use this case study to inform your Theta analysis of this company. The case study reveals real Beyond investments and R&D that may not be in public news."
        elif ctx.get("case_study_note"):
            cs_inserts += f"\n\nPam has a case study on {name}: {ctx['case_study_note']}"

    company_list = "\n".join(
        f"{i+1}. {c.get('name','')} ({c.get('industry','')}, {c.get('country','')})"
        for i, c in enumerate(to_research)
    )

    prompt = f"""{THETA_FRAMEWORK_BRIEF}
{cs_inserts}

Analyse each of these companies using the Theta Framework above:

{company_list}

For EACH company provide:
1. CORE zone: Key Core initiatives (existing products, efficiency, optimization, revenue %)
2. EDGE zone: Active Edge bets (new platforms, adjacent markets, DTC, digital transformation)
3. BEYOND zone: Beyond investments — BE THOROUGH. Do NOT miss microbiome, precision nutrition,
   cellular agriculture, fermentation, clinical research, deep tech, or any R&D with 7-15yr horizon.
   If the case study mentions Beyond investments, include them here.
4. ARCHETYPE: Which of the 6 patterns fits?
5. BIGGEST GAP: Where is their portfolio weakest?
6. WHO OWNS BREAKTHROUGH: Which specific named role owns Edge and Beyond innovation?

Keep each company under 350 words. Be specific — name actual programs and initiatives.

Format EXACTLY:

---COMPANY: [Exact company name]---
[analysis]

---COMPANY: [Next company name]---
[analysis]
"""

    try:
        if ds_client:
            raw = call_deepseek(ds_client, prompt, max_tokens=6000)
        else:
            raw = call_claude(claude_client, prompt, max_tokens=5000)

        cost.charge("research_batch", note=f"{len(to_research)} companies via {model_name}")

        blocks = raw.split("---COMPANY:")
        for company in to_research:
            name       = company.get("name","")
            cs_context = cs_contexts.get(name, {})
            cs_text    = cs_context.get("case_study_text","")
            background = ""

            for block in blocks[1:]:
                block_name = block.split("---")[0].strip()
                if name.lower().split()[0] in block_name.lower() or block_name.lower() in name.lower():
                    background = block.split("---", 1)[1].strip() if "---" in block else block.strip()
                    break

            if not background:
                idx = to_research.index(company)
                if idx + 1 < len(blocks):
                    raw_block  = blocks[idx + 1]
                    background = raw_block.split("---", 1)[1].strip() if "---" in raw_block else raw_block.strip()

            if not background:
                background = f"Research not available for {name}"

            # Save research to cache (without case study text — that's personal)
            save_cache(name, {"background": background})

            # [FIX] Combine research + case study for scoring
            combined = f"{company.get('why_theta_fit','')} {company.get('recent_signal','')} {background}"
            theta    = theta_assess(combined, name, company.get("industry",""),
                                    case_study_text=cs_text)
            results[name] = {
                "background": background,
                "theta":      theta,
                "was_cached": False,
            }

        return results

    except Exception as e:
        print(f"  ⚠️  Batch research failed: {e}. Falling back to individual calls.")
        for company in to_research:
            name   = company.get("name","")
            ctx    = cs_contexts.get(name, {})
            result = research_company_single(ds_client, claude_client, company, cost, ctx)
            results[name] = result
        return results


def research_company_single(ds_client, claude_client, company: dict, cost: CostTracker,
                             cs_context: dict = None) -> dict:
    """Single-company research fallback."""
    name     = company.get("name","")
    industry = company.get("industry","")
    cs_text  = (cs_context or {}).get("case_study_text","")
    has_cs   = bool(cs_text)

    # Bypass cache if case study present
    if not has_cs:
        cached = load_cache(name)
        if "background" in cached:
            cost.charge("cached_research", note=f"{name} (cached)")
            return {
                "background": cached["background"],
                "theta":      theta_assess(cached["background"], name, industry),
                "was_cached": True,
            }

    cs_insert = ""
    if cs_context:
        if cs_text:
            cs_insert = f"\n\nPAM'S CASE STUDY (use to inform Beyond scoring):\n{cs_text[:3000]}"
        elif cs_context.get("case_study_note"):
            cs_insert = f"\n\nPam has a case study on {name}: {cs_context['case_study_note']}"

    model_name = "DeepSeek" if ds_client else "Claude"
    prompt = f"""{THETA_FRAMEWORK_BRIEF}
Analyse {name} ({industry}) using the Theta Framework above.{cs_insert}

Provide: CORE zone (with revenue %), EDGE zone, BEYOND zone (thorough — do not miss Beyond investments),
ARCHETYPE (1-6), BIGGEST GAP, WHO OWNS BREAKTHROUGH.
Under 400 words. Be specific — name actual programs."""

    try:
        if ds_client:
            background = call_deepseek(ds_client, prompt, max_tokens=700)
        else:
            background = call_claude(claude_client, prompt, max_tokens=700)
        save_cache(name, {"background": background})
        cost.charge("research", note=f"{name} (single, {model_name})")
    except Exception as e:
        background = f"Research unavailable: {e}"
        cost.charge("cached_research", note=f"{name} (error)")

    combined = f"{company.get('why_theta_fit','')} {company.get('recent_signal','')} {background}"
    return {
        "background": background,
        "theta":      theta_assess(combined, name, industry, case_study_text=cs_text),
        "was_cached": False,
    }

# ─────────────────────────────────────────────────────────────
# FIND TARGET PEOPLE — DeepSeek + 30-day cache
# ─────────────────────────────────────────────────────────────

def find_target_people(ds_client, claude_client, company: dict, research_bg: str,
                       cost: CostTracker) -> list:
    name     = company.get("name","")
    industry = company.get("industry","")

    cached_people = load_cached_people(name)
    if cached_people:
        print(f"  ✓ People loaded from cache (free)")
        cost.charge("cached_people", note=f"{name} people (cached)")
        return cached_people

    model_name = "DeepSeek" if ds_client else "Claude"
    prompt = f"""{THETA_FRAMEWORK_BRIEF}

Based on this Theta analysis of {name} ({industry}):
{research_bg[:1500]}

Identify 3–5 REAL people who own Edge and Beyond innovation at {name}.

INSTRUCTIONS:
- Search your knowledge for ACTUAL named individuals currently in these roles
- If you know their real name with HIGH confidence, provide it
- If you do NOT know a specific person's name, say "Search required" — do NOT invent names
- Rank by likelihood they own portfolio-level innovation (not just a single project)

TARGET ROLES (priority order):
Chief Innovation Officer, Head of Innovation Portfolio, Chief Strategy Officer,
Chief Technology Officer, VP R&D Ventures, Head of Corporate Venture,
Head of Digital Transformation, VP New Business Development

For each:

---PERSON---
RANK: [1-5]
NAME: [Full real name OR "Search required"]
TITLE: [Exact current title]
WHY: [One sentence — what they specifically own relevant to Theta]
LINKEDIN_SEARCH: [Precise search string]
CONFIDENCE: [High = verified real person / Medium = likely / Low = guessing]"""

    try:
        if ds_client:
            raw = call_deepseek(ds_client, prompt, max_tokens=900)
        else:
            raw = call_claude(claude_client, prompt, max_tokens=900)

        people = []
        for block in raw.split("---PERSON---")[1:]:
            p = {"rank":"","name":"","title":"","why":"","linkedin_search":"","confidence":""}
            for line in block.strip().splitlines():
                s = line.strip()
                for field in ["RANK","NAME","TITLE","WHY","LINKEDIN_SEARCH","CONFIDENCE"]:
                    if s.startswith(f"{field}:"):
                        p[field.lower()] = s[len(field)+1:].strip()
            if not p.get("name") or p["name"].strip() in ["","Unknown","N/A"]:
                p["name"] = "Search required"
            if p.get("title"):
                people.append(p)

        cost.charge("research", note=f"{name} people (fresh, {model_name})")

        if people:
            save_cached_people(name, people)
            print(f"  ✓ {len(people)} people found via {model_name} — cached for next time")

        return people[:5]

    except Exception as e:
        print(f"  ⚠️  People lookup failed: {e}")
        return []


def check_public_channels(claude_client, person: dict, company_name: str, cost: CostTracker) -> dict:
    """Channel assessment — Claude for nuanced judgment on outreach approach."""
    name  = person.get("name","")
    title = person.get("title","")

    if name in ("Search required", "Unknown", ""):
        return {
            "recommended": "LinkedIn",
            "rationale":   "No confirmed name — use LinkedIn search string to find first",
            "channels":    {"LinkedIn": "Search → connect → message after accepted"}
        }

    prompt = f"""For {name}, {title} at {company_name}:

Assess public channel availability:
1. LinkedIn: active poster? (likely/unlikely/unknown)
2. Twitter/X: public account? (likely/unlikely/unknown)
3. Substack/blog: public writing? (likely/unlikely/unknown)
4. Email: findable publicly? (likely/unlikely/unknown)

For each: Ease of first contact (Easy/Medium/Hard) and one-line reason.
Then: which channel gives the EASIEST warm first touch?

Format:
LINKEDIN: [likely/unlikely/unknown] | [Easy/Medium/Hard] | [reason]
TWITTER: [likely/unlikely/unknown] | [Easy/Medium/Hard] | [reason]
SUBSTACK: [likely/unlikely/unknown] | [Easy/Medium/Hard] | [reason]
EMAIL: [likely/unlikely/unknown] | [Easy/Medium/Hard] | [reason]
RECOMMENDED: [channel]
RATIONALE: [one sentence]"""

    try:
        raw         = call_claude(claude_client, prompt, max_tokens=250)
        channels    = {}
        recommended = "LinkedIn"
        rationale   = ""
        for line in raw.splitlines():
            s = line.strip()
            for ch in ["LINKEDIN","TWITTER","SUBSTACK","EMAIL"]:
                if s.startswith(f"{ch}:"):
                    channels[ch.capitalize()] = s[len(ch)+1:].strip()
            if s.startswith("RECOMMENDED:"):
                recommended = s[12:].strip()
            if s.startswith("RATIONALE:"):
                rationale = s[10:].strip()
        cost.charge("channels", note=f"channels {name}")
        return {"recommended": recommended, "rationale": rationale, "channels": channels}
    except:
        return {"recommended": "LinkedIn", "rationale": "Default — LinkedIn safest first touch",
                "channels": {"LinkedIn": "likely | Easy | Standard"}}

# ─────────────────────────────────────────────────────────────
# DRAFT MESSAGES — Claude (quality matters here)
# ─────────────────────────────────────────────────────────────

def draft_connection_and_message(claude_client, company: dict, person: dict, theta: dict,
                                  channel_info: dict, cs_context: dict,
                                  config: dict, patterns: dict,
                                  linkedin_notes_available: bool = True) -> dict:
    user_name    = config.get("user_short_name","Pam")
    company_name = company.get("name","")
    person_name  = person.get("name","")
    title        = person.get("title","")
    why          = person.get("why","")
    recommended  = channel_info.get("recommended","LinkedIn")

    content_hook = ""
    if cs_context.get("case_study_text"):
        content_hook = f"Pam has a detailed case study on {company_name}. Key content: {cs_context['case_study_text'][:500]}"
    elif cs_context.get("case_study_note"):
        content_hook = f"Pam has a case study: {cs_context['case_study_note']}"
    if cs_context.get("article_texts"):
        content_hook += f" Pam also has a published article. Excerpt: {cs_context['article_texts'][0][:300]}"

    person_ref = person_name if person_name not in ("Search required","") else f"the {title}"

    if linkedin_notes_available:
        note_instruction = """Draft BOTH:

---CONNECTION NOTE--- (LinkedIn invite, MAX 300 characters — hard limit)
[message]

---POST CONNECTION MESSAGE--- (send after they accept)
CHANNEL: {channel}
BODY:
[message]""".format(channel=recommended)
        extra_rule = "- Connection note: ONE curiosity hook, no ask, under 300 chars"
    else:
        note_instruction = """IMPORTANT: Pam has NO connection notes available.
She will send a BLANK invite. This message is the FIRST thing this person reads.
It must stand alone without any prior context.

---POST CONNECTION MESSAGE---
CHANNEL: {channel}
BODY:
[message]""".format(channel=recommended)
        extra_rule = "- No connection note was sent — message must be self-contained and warm"

    prompt = f"""You are drafting outreach for {config.get('user_name','Christine Pamela')}, an innovation consultant (Theta Framework).

TARGET: {person_ref} — {title} at {company_name}
WHY THEM: {why}
RECOMMENDED CHANNEL: {recommended}

THETA READ ON {company_name.upper()}:
- Pattern: {display_archetype(theta.get('archetype',''))}
- Zones: Core={theta['zone_distribution']['core']} Edge={theta['zone_distribution']['edge']} Beyond={theta['zone_distribution']['beyond']}
- Pain: {theta.get('pain_point','')}
- Angle: {theta.get('messaging_angle','')}
- Case study informed: {theta.get('case_study_informed', False)}

PAM'S CONTENT: {content_hook if content_hook else "No specific content for this company yet."}

RULES:
- No selling, no framework name-dropping in first touch
- Lead with ONE sharp observation about {company_name}'s innovation portfolio
- Post-connection message: 3–5 sentences max, ends with a light question
- If Pam has a case study on this exact company, reference it naturally
- Sign as "{user_name}"
- Tone: peer-to-peer, curious, not consultant-pitching
{extra_rule}

{note_instruction}"""

    try:
        raw = call_claude(claude_client, prompt, max_tokens=600)

        connection_note = ""
        post_msg        = ""
        in_post         = False

        for line in raw.splitlines():
            s = line.strip()
            if "---CONNECTION NOTE---" in s:
                in_post = False
                continue
            if "---POST CONNECTION MESSAGE---" in s:
                in_post = True
                continue
            if not in_post and s and "---" not in s and "BODY:" not in s and "CHANNEL:" not in s:
                connection_note += s + " "
            if in_post and not s.startswith("CHANNEL:") and not s.startswith("BODY:") and "---" not in s:
                post_msg += line + "\n"

        connection_note = connection_note.strip()[:300] if linkedin_notes_available else ""
        post_msg        = post_msg.strip()

        return {
            "connection_note": connection_note,
            "post_connection":  post_msg,
            "channel":          recommended,
        }
    except Exception as e:
        fallback_note = f"Hi — I've been researching {company_name}'s innovation portfolio and would value your perspective." if linkedin_notes_available else ""
        fallback_msg  = f"Hi — thanks for connecting. I've been studying {company_name}'s innovation portfolio and had a few questions I thought you'd find interesting. Would you be open to a quick conversation?\n\nBest,\n{user_name}"
        return {
            "connection_note": fallback_note,
            "post_connection":  fallback_msg,
            "channel":          recommended,
        }

# ─────────────────────────────────────────────────────────────
# ARCHETYPE CORRECTION LOOP
# ─────────────────────────────────────────────────────────────

def prompt_archetype_correction(current_archetype: str, company_name: str,
                                 theta: dict, research_bg: str, industry: str,
                                 learnings: dict, cs_text: str = "") -> dict:
    print(f"\n  Current pattern: {display_archetype(current_archetype)}")
    print(f"\n  Zone scores:")
    zones = theta['zone_distribution']
    print(f"    Core   {'🟩' * zones['core']}{'⬜' * (10-zones['core'])}  {zones['core']}/10")
    print(f"    Edge   {'🟨' * zones['edge']}{'⬜' * (10-zones['edge'])}  {zones['edge']}/10")
    print(f"    Beyond {'🟥' * zones['beyond']}{'⬜' * (10-zones['beyond'])}  {zones['beyond']}/10")
    if theta.get("case_study_informed"):
        print(f"    📎  These scores include signals from your case study")
    print(f"\n  Archetype options:")
    for arch in VALID_ARCHETYPES:
        print(f"    {arch}")
    print(f"    (Enter to add a new one)")

    while True:
        raw = input(f"\n  Correct number (1-{len(VALID_ARCHETYPES)}) or Enter to skip: ").strip()
        if raw == "":
            print("  Keeping current archetype.")
            return theta
        if raw.isdigit():
            idx = int(raw) - 1
            if 0 <= idx < len(VALID_ARCHETYPES):
                correct = VALID_ARCHETYPES[idx]
                corrections = learnings.get("archetype_corrections", {})
                corrections[company_name] = {
                    "was":          current_archetype,
                    "corrected_to": correct,
                    "date":         datetime.now().isoformat(),
                }
                learnings["archetype_corrections"] = corrections
                save_learnings(learnings)
                new_theta = theta_assess(research_bg, company_name, industry,
                                         case_study_text=cs_text,
                                         override_archetype=correct)
                print(f"  ✓ Corrected to: {display_archetype(correct)}")
                return new_theta
            else:
                print(f"  Please enter a number between 1 and {len(VALID_ARCHETYPES)}, or Enter to skip.")
        else:
            match = next((a for a in VALID_ARCHETYPES if raw.lower() in a.lower()), None)
            if match:
                correct   = match
                new_theta = theta_assess(research_bg, company_name, industry,
                                         case_study_text=cs_text,
                                         override_archetype=correct)
                corrections = learnings.get("archetype_corrections", {})
                corrections[company_name] = {"was": current_archetype, "corrected_to": correct,
                                              "date": datetime.now().isoformat()}
                learnings["archetype_corrections"] = corrections
                save_learnings(learnings)
                print(f"  ✓ Corrected to: {display_archetype(correct)}")
                return new_theta
            else:
                print(f"  Not recognised. Enter a number 1-{len(VALID_ARCHETYPES)}, or Enter to skip.")

# ─────────────────────────────────────────────────────────────
# BUILD CONTACT RECORD
# ─────────────────────────────────────────────────────────────

def build_contact_record(company: dict, theta: dict, chosen_variant: dict,
                          cs_context: dict) -> dict:
    now = datetime.now(timezone.utc).isoformat().replace("+00:00","Z")

    target_roles_raw = company.get("target_roles","")
    search_strings   = [s.strip() for s in target_roles_raw.split("|") if s.strip()]
    linkedin_block   = "\n".join(f"LinkedIn search {i}: {s}" for i, s in enumerate(search_strings[:5], 1))

    content_note = ""
    if cs_context.get("has_case_study"):
        content_note = f"\nCase study on file: {cs_context.get('case_study_note','yes')}"
    if cs_context.get("building_case_study"):
        content_note += f"\nBuilding case study: {company.get('industry','')}"
    if cs_context.get("articles"):
        content_note += f"\nArticles: {', '.join(cs_context['articles'])}"

    return {
        "id":               str(uuid.uuid4()),
        "company":          company.get("name","").strip(),
        "name":             "",
        "jobTitle":         "",
        "industryFocus":    company.get("industry",""),
        "country":          company.get("country",""),
        "tier":             "Decision-maker",
        "connectionMethod": chosen_variant.get("channel","LinkedIn"),
        "connectionStatus": "Not yet connected",
        "priority":         "Medium",
        "opportunityType":  "Strategic fit",
        "funnelStage":      "Unaware",
        "nextActionDate":   None,
        "tags":             ["theta-hunt"],
        "communicationLog": [],
        "lastMessage":      "",
        "createdAt":        now,
        "notes": (
            f"Theta pattern: {display_archetype(theta.get('archetype',''))}\n"
            f"Zone: Core={theta['zone_distribution']['core']} "
            f"Edge={theta['zone_distribution']['edge']} "
            f"Beyond={theta['zone_distribution']['beyond']}\n"
            f"Case study informed: {theta.get('case_study_informed', False)}\n"
            f"Pain point: {theta.get('pain_point','')}\n"
            f"Strengths: {company.get('strengths','')}\n"
            f"Recent signal: {company.get('recent_signal','')}\n"
            f"Best channel: {company.get('best_channel','LinkedIn')}\n"
            f"\n── FIND YOUR CONTACT ──\n{linkedin_block}"
            f"{content_note}"
        ),
        "draft_message": {
            "channel": chosen_variant.get("channel","LinkedIn"),
            "subject": chosen_variant.get("subject",""),
            "body":    chosen_variant.get("body",""),
            "tone":    chosen_variant.get("tone",""),
            "note":    "Name blank — fill after LinkedIn search. Send when ready.",
        },
    }

# ─────────────────────────────────────────────────────────────
# TASK LIST GENERATOR
# ─────────────────────────────────────────────────────────────

def generate_tuesday_tasks(approved_new, followup_actions, warm, patterns) -> dict:
    tasks     = []
    best_send = patterns.get("best_send_window","Tuesday 09:00–12:00")

    for f in followup_actions:
        tasks.append({
            "priority": "1-URGENT",
            "task":     f"Reply to {f['name']} @ {f['company']}",
            "channel":  f.get("channel","LinkedIn"),
            "action":   "Send reply — they are waiting",
            "message":  f.get("message",""),
            "send_time": "First thing",
        })

    for c in approved_new:
        draft   = c.get("draft_message",{})
        notes   = c.get("notes","")
        searches = [l.strip() for l in notes.splitlines() if "LinkedIn search" in l]
        channel  = draft.get("channel","LinkedIn")
        tasks.append({
            "priority":          "2-NEW",
            "task":              f"First touch: [Find contact] @ {c['company']}",
            "channel":           channel,
            "action":            "1. Find contact → 2. Connect/reach out → 3. Send message",
            "linkedin_searches": searches,
            "message":           draft.get("body",""),
            "subject":           draft.get("subject",""),
            "send_time":         best_send,
        })

    for c in warm[:3]:
        tasks.append({
            "priority": "3-WARM",
            "task":     f"First message: {c.get('name','')} @ {c.get('company','')} (already connected)",
            "channel":  c.get("connectionMethod","LinkedIn"),
            "action":   "Send first message — they accepted your connection",
            "message":  "[Draft using Theta angle from their notes]",
            "send_time": best_send,
        })

    return {
        "generated_at":     datetime.now().isoformat(),
        "week_of":          datetime.now().strftime("%Y-%m-%d"),
        "best_send_window": best_send,
        "total_tasks":      len(tasks),
        "tasks":            tasks,
    }

def save_all(approved_new, followup_actions, warm, patterns, cost):
    OUTPUTS_DIR.mkdir(exist_ok=True)

    if approved_new:
        export = {
            "version":    "1.4",
            "exportDate": datetime.now(timezone.utc).isoformat().replace("+00:00","Z"),
            "source":     "semi-agentic-outreach-v3.6",
            "note":       (
                "Import into tracker via '↑ Import CSV' button.\n"
                "Name and jobTitle are blank — fill after you find the person on LinkedIn.\n"
                "draft_message contains your approved first-touch message."
            ),
            "contacts": approved_new,
        }
        with open(EXPORT_PATH,"w",encoding="utf-8") as f:
            json.dump(export, f, indent=2, ensure_ascii=False)

    if followup_actions:
        with open(FOLLOWUP_PATH,"w",encoding="utf-8") as f:
            json.dump({"followups": followup_actions}, f, indent=2, ensure_ascii=False)

    tasks = generate_tuesday_tasks(approved_new, followup_actions, warm, patterns)
    with open(TASKS_PATH,"w",encoding="utf-8") as f:
        json.dump(tasks, f, indent=2, ensure_ascii=False)

    cost.save()

# ─────────────────────────────────────────────────────────────
# FRIDAY REVIEW — Claude (judgment-heavy)
# ─────────────────────────────────────────────────────────────

def friday_review(claude_client, config: dict, learnings: dict):
    banner("🪞  FRIDAY REVIEW")
    print("  Upload this week's tracker export for a CEO-level summary.\n")
    raw_path = input("  Path to this week's tracker JSON: ").strip().strip('"').strip("'")

    if not raw_path or not Path(raw_path).exists():
        print(f"  ❌  File not found: {raw_path}")
        return

    tracker  = load_tracker_json(raw_path)
    contacts = tracker["contacts"]
    print(f"\n  ✓ Loaded {len(contacts)} contacts from {Path(raw_path).name}")

    Path(TRACKER_PATH).parent.mkdir(exist_ok=True)
    Path(TRACKER_PATH).write_text(
        json.dumps(tracker, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"  ✓ Saved as data/outreach_import.json (Monday will pick this up automatically)")

    patterns   = analyze_patterns(contacts)
    today      = datetime.now().date()
    week_start = today - timedelta(days=today.weekday())

    touches_this_week   = 0
    responses_this_week = 0
    new_connections     = 0
    funnel_moves        = defaultdict(int)

    for c in contacts:
        for log in c.get("communicationLog",[]):
            try:
                ld = datetime.fromisoformat(log["date"].replace("Z","")).date()
                if ld >= week_start:
                    touches_this_week += 1
                    if (log.get("response","") or "").strip():
                        responses_this_week += 1
            except: pass
        created = c.get("createdAt","")
        if created:
            try:
                cd = datetime.fromisoformat(created.replace("Z","")).date()
                if cd >= week_start: new_connections += 1
            except: pass
        funnel_moves[c.get("funnelStage","Unaware")] += 1

    response_rate = round(responses_this_week / max(touches_this_week,1) * 100)

    section("THIS WEEK'S NUMBERS")
    print(f"  Touches sent:      {touches_this_week}")
    print(f"  Responses:         {responses_this_week}")
    print(f"  Response rate:     {response_rate}%")
    print(f"  New contacts:      {new_connections}")
    print(f"\n  Pipeline snapshot:")
    for stage in ["Unaware","Awareness","Engaged","Consideration","Active Conversation","Conversion"]:
        count = funnel_moves.get(stage,0)
        print(f"    {stage:22s} {count}")

    section("YOUR REFLECTION")
    notes     = []
    questions = [
        "What message angle got the best reaction this week?",
        "Who surprised you — who responded that you didn't expect?",
        "Which industry or role type felt most receptive?",
        "Did any article or case study reference land particularly well?",
        "What do you want to try differently next Monday?",
    ]
    for q in questions:
        print(f"  Q: {q}")
        ans = input("  A: ").strip()
        if ans: notes.append({"q": q, "a": ans})
        print()

    section("CEO SUMMARY — GENERATING...")

    funnel_str  = "\n".join(f"  {k}: {v}" for k, v in funnel_moves.items())
    channel_str = "\n".join(
        f"  {ch}: {s['rate_pct']:.0f}% ({s['responded']}/{s['sent']})"
        for ch, s in patterns.get("channel_rates",{}).items())
    notes_str   = "\n".join(f"Q: {n['q']}\nA: {n['a']}" for n in notes) if notes else "No reflection notes."

    prompt = f"""You are reviewing Christine Pamela's outreach week for her innovation consulting business (Theta Framework).

THIS WEEK:
- Touches sent: {touches_this_week}
- Responses: {responses_this_week} ({response_rate}% rate)
- New contacts: {new_connections}

PIPELINE:
{funnel_str}

CHANNEL PERFORMANCE:
{channel_str}

PAM'S REFLECTION:
{notes_str}

Write a CEO-level weekly review:
HEADLINE (1 sentence)
WHAT WORKED (2-3 bullets)
WHAT DIDN'T (1-2 bullets)
PIPELINE HEALTH (1 paragraph)
RECOMMENDATION FOR MONDAY (3 numbered actions)

Board-meeting level. No fluff."""

    try:
        review_text = call_claude(claude_client, prompt, max_tokens=600)
    except Exception as e:
        review_text = f"[CEO review generation failed: {e}]"

    print()
    for line in review_text.splitlines():
        print(f"  {line}")

    OUTPUTS_DIR.mkdir(exist_ok=True)
    review_output = (
        f"WEEKLY REVIEW — {datetime.now().strftime('%A %B %d %Y')}\n{'='*60}\n\n"
        f"METRICS:\n  Touches: {touches_this_week} | Responses: {responses_this_week} ({response_rate}%)\n"
        f"  New contacts: {new_connections}\n\n{review_text}\n\n{'='*60}\n"
        f"REFLECTION NOTES:\n" + "\n".join(f"Q: {n['q']}\nA: {n['a']}\n" for n in notes)
    )
    with open(REVIEW_PATH, "w", encoding="utf-8") as f:
        f.write(review_output)
    print(f"\n  ✓ Review saved to {REVIEW_PATH}")

    learnings["history"].append({
        "week_of":  datetime.now().strftime("%Y-%m-%d"),
        "notes":    notes,
        "patterns": patterns,
        "review":   review_text,
        "metrics":  {"touches": touches_this_week, "responses": responses_this_week,
                     "response_rate_pct": response_rate, "new_contacts": new_connections},
    })
    learnings["patterns"]          = patterns
    learnings["last_tracker_file"] = str(raw_path)
    if notes: learnings["strategy_notes"].append(review_text)
    save_learnings(learnings)
    print(f"  ✓ Learnings saved — Monday will build on this.")
    print(f"\n  📋  Next Monday: python outreach_agent_v3_6.py\n")

# ─────────────────────────────────────────────────────────────
# MAIN
# ─────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="Semi-Agentic Outreach System v3.6")
    parser.add_argument("--friday", action="store_true")
    parser.add_argument("--status", action="store_true")
    args = parser.parse_args()

    config    = load_config()
    learnings = load_learnings()
    OUTPUTS_DIR.mkdir(exist_ok=True)
    CACHE_DIR.mkdir(parents=True, exist_ok=True)

    # ── STATUS MODE ──────────────────────────────────────────
    if args.status:
        if not Path(TRACKER_PATH).exists():
            print(f"❌  No tracker file found at {TRACKER_PATH}")
            sys.exit(1)
        tracker  = load_tracker_json(TRACKER_PATH)
        contacts = tracker["contacts"]
        status_check(contacts, learnings)
        return

    # ── FRIDAY MODE ──────────────────────────────────────────
    if args.friday:
        claude_client = get_claude_client(config)
        friday_review(claude_client, config, learnings)
        return

    # ── MONDAY MODE ──────────────────────────────────────────
    banner("🧠  SEMI-AGENTIC OUTREACH v3.6  |  Theta Framework")
    print(f"  {datetime.now().strftime('%A, %B %d %Y  %H:%M')}")
    print(f"  {day_greeting()}")
    print(f"\n  v3.6 improvements active:")
    print(f"    ✓ Case study scoring fix — your case study now feeds into zone scores")
    print(f"    ✓ Cache bypass — case study companies always get fresh research")
    print(f"    ✓ DeepSeek for research (hunt + Theta analysis) — ~10x cheaper")
    print(f"    ✓ Claude for messages + CEO review — quality where it matters")

    # Initialise clients
    claude_client = get_claude_client(config)
    ds_client     = get_deepseek_client(config)

    if ds_client:
        print(f"\n  🔀 Model routing:")
        print(f"     Research (hunt + Theta) → DeepSeek  (~$0.03 for 5 companies)")
        print(f"     Messages + CEO review   → Claude    (quality)")
    else:
        print(f"\n  ℹ️  Using Claude for all tasks (add deepseek_api_key to config.yaml to save ~90% on research)")

    if datetime.now().weekday() != 0:
        print(f"\n  ℹ️   Note: Today is {datetime.now().strftime('%A')}. This is designed for Monday.")
        confirm = ask("  Continue anyway?", ["y","n"])
        if confirm == "n":
            print("  Tip: Use --status for a quick daily check.")
            return

    # Load tracker
    if not Path(TRACKER_PATH).exists():
        print(f"\n  ❌  Tracker not found: {TRACKER_PATH}")
        alt = input("  Or paste full path to your JSON export: ").strip().strip('"').strip("'")
        if alt and Path(alt).exists():
            Path(TRACKER_PATH).parent.mkdir(exist_ok=True)
            Path(TRACKER_PATH).write_text(
                Path(alt).read_text(encoding="utf-8"), encoding="utf-8")
            print(f"  ✓ Copied to {TRACKER_PATH}")
        else:
            print("  ❌  No file found. Exiting.")
            sys.exit(1)

    tracker  = load_tracker_json(TRACKER_PATH)
    contacts = tracker["contacts"]
    print(f"\n  {len(contacts)} contacts loaded")

    export_date = tracker.get("exportDate","")
    if export_date:
        try:
            ed = datetime.fromisoformat(export_date.replace("Z","")).date()
            days_old = (datetime.now().date() - ed).days
            if days_old > 7:
                print(f"  ⚠️   This file is {days_old} days old. Consider re-exporting.")
        except: pass

    print("\n  Analyzing patterns...")
    cost     = CostTracker(budget=config.get("weekly_budget", 2.00))
    patterns = analyze_patterns(contacts)
    cost.charge("analysis")
    learnings["patterns"] = patterns
    save_learnings(learnings)

    urgent, warm, stale = audit_tracker(contacts)
    display_audit(urgent, warm, stale, patterns)

    followup_actions = []
    approved_new     = []

    # Follow-ups (Claude — quality)
    if urgent:
        do_fu = ask(f"\n  Draft replies for {len(urgent)} people who responded?", ["y","n"])
        if do_fu == "y":
            for c in urgent:
                print(f"\n  Drafting reply for {c.get('name','')} @ {c.get('company','')}...")
                draft  = draft_reply(claude_client, c, config, patterns)
                cost.charge("followup_draft", note=c.get("name",""))
                chosen = approval_loop_followup(c, draft)
                if chosen:
                    followup_actions.append({
                        "name":    c.get("name",""),
                        "company": c.get("company",""),
                        "channel": c.get("_channel","LinkedIn"),
                        "action":  "Send reply",
                        "message": chosen,
                        "priority": c.get("priority",""),
                    })
                    print("  ✓ Approved")

    do_hunt = ask("\n  Hunt for NEW target companies this week?", ["y","n"])

    if do_hunt == "y":
        print("\n  LinkedIn connection note status:")
        li_notes = ask("  Can you still send LinkedIn connection notes this week?", ["y","n"])
        linkedin_notes_available = (li_notes == "y")

        industries    = select_industries_interactively(contacts, patterns)
        country_focus = select_country_focus()
        target_count  = select_target_count()

        existing      = list(set(c.get("company","").strip() for c in contacts if c.get("company")))
        new_companies = hunt_new_companies(ds_client, claude_client, existing, config,
                                           industries, country_focus, target_count)
        cost.charge("hunt")

        if new_companies:
            print(f"\n  Reviewing {len(new_companies)} companies.")

            # Collect case study contexts upfront
            print(f"\n  First, let's collect your content for each company.")
            print(f"  Your case studies will feed into the Theta scoring (not just the prompt).\n")
            cs_contexts = {}
            for company in new_companies:
                cname = company.get("name","")
                print(f"  ── {cname.upper()} ──")
                cs_contexts[cname] = ask_case_study_context(cname, company.get("industry",""))

            # Batch research (DeepSeek preferred)
            print(f"\n  {cost.projection(len(new_companies))}")
            research_results = batch_research_companies(
                ds_client, claude_client, new_companies, cost, cs_contexts)

            print(f"\n  ✓ Research complete. Reviewing companies one at a time.")
            input("  Press Enter to start... ")

            for i, company in enumerate(new_companies, 1):
                if not cost.can_afford("draft"):
                    print(f"\n  ⚠️  Budget limit (${cost.spent:.2f}). Stopping.")
                    break

                cname      = company.get("name","")
                cs_context = cs_contexts.get(cname, {})
                research   = research_results.get(cname, {"background":"","theta":{},"was_cached":False})
                theta      = research.get("theta", {})

                print(f"\n  [{i}/{len(new_companies)}] ── {cname.upper()} ──")
                cached_tag = " (cached)" if research.get("was_cached") else ""
                cs_tag     = " 📎" if theta.get("case_study_informed") else ""
                print(f"  {company.get('industry','')} | {company.get('country','')} | ~{company.get('size','?')} employees")

                section(f"THETA BRIEF — {cname}{cached_tag}{cs_tag}")
                display_theta_visual(cname, theta)

                bg = research.get("background","")
                print(f"\n  Research summary:")
                for line in bg.splitlines()[:10]:
                    if line.strip(): print(f"    {line}")
                if len(bg.splitlines()) > 10:
                    print(f"    ... ({len(bg.splitlines())} lines total)")

                # Archetype correction
                correct_arch = ask("\n  Archetype correct?", ["y","n"])
                if correct_arch == "n":
                    cs_text = cs_context.get("case_study_text","")
                    theta = prompt_archetype_correction(
                        theta.get("archetype",""), cname, theta,
                        bg, company.get("industry",""), learnings, cs_text=cs_text
                    )
                    research["theta"] = theta

                skip_company = ask("\n  Continue with this company?", ["y","n"])
                if skip_company == "n":
                    print(f"  Skipped {cname}.")
                    continue

                # Find target people (DeepSeek + cache)
                print(f"\n  Finding who owns breakthrough innovation at {cname}...")
                people = find_target_people(ds_client, claude_client, company, bg, cost)

                if not people:
                    print("  ⚠️  Could not identify target people. Skipping.")
                    continue

                print(f"\n  🎯  Target people at {cname} ({len(people)} found):")
                for p in people:
                    conf_icon    = "✅" if p.get("confidence","").lower() == "high" else "🔍"
                    name_display = p['name'] if p['name'] != "Search required" else "🔍 Search required"
                    print(f"\n  [{p['rank']}] {conf_icon} {name_display} — {p['title']}")
                    print(f"       Why: {p['why']}")
                    if p.get("linkedin_search"):
                        print(f"       Search: {p['linkedin_search']}")

                print()
                raw_pick = input(f"  Which person to target? [1–{len(people)} / all / n=skip]: ").strip().lower()
                if raw_pick == "n":
                    continue
                elif raw_pick == "all":
                    selected_people = people
                elif raw_pick.isdigit() and 1 <= int(raw_pick) <= len(people):
                    selected_people = [people[int(raw_pick)-1]]
                else:
                    selected_people = [people[0]]

                # Per person: channels (Claude) + messages (Claude)
                for person in selected_people:
                    pname         = person.get("name","Search required")
                    pname_display = pname if pname != "Search required" else f"[{person.get('title','')}]"
                    print(f"\n  Checking public channels for {pname_display}...")
                    channel_info = check_public_channels(claude_client, person, cname, cost)

                    print(f"\n  Channel assessment for {pname_display}:")
                    for ch, detail in channel_info.get("channels",{}).items():
                        print(f"     {ch:12s} {detail}")
                    print(f"\n  ✅  Recommended: {channel_info['recommended']}")
                    print(f"     {channel_info.get('rationale','')}")

                    print(f"\n  Drafting messages (Claude)...")
                    drafts = draft_connection_and_message(
                        claude_client, company, person, theta, channel_info,
                        cs_context, config, patterns,
                        linkedin_notes_available=linkedin_notes_available)
                    cost.charge("draft", note=pname_display)

                    section(f"OUTREACH DRAFTS — {pname_display} @ {cname}")
                    print(f"  {person.get('title','')} | Channel: {drafts['channel']}")
                    if theta.get("case_study_informed"):
                        print(f"  📎  Message informed by your case study")

                    if drafts.get("connection_note") and linkedin_notes_available:
                        print(f"\n  ── A) LINKEDIN CONNECTION NOTE (max 300 chars) ──")
                        print(f"  \"{drafts['connection_note']}\"")
                        print(f"  [{len(drafts['connection_note'])} chars]")
                        print(f"\n  ── B) POST-CONNECTION MESSAGE ──")
                    else:
                        if not linkedin_notes_available:
                            print(f"\n  ℹ️  No connection note (limit reached) — blank invite.")
                            print(f"  Your first message after they accept:\n")
                        else:
                            print(f"\n  ── FIRST MESSAGE ──")

                    for line in drafts["post_connection"].splitlines():
                        print(f"  {line}")

                    print()
                    if linkedin_notes_available:
                        choice = input("  [a=approve both / p=post-msg only / e=edit / n=skip]: ").strip().lower()
                    else:
                        choice = input("  [a=approve message / e=edit / n=skip]: ").strip().lower()
                        if choice == "a": choice = "p"

                    if choice == "n":
                        continue
                    if choice == "e":
                        print("  Paste your edited message (blank line to finish):")
                        lines = []
                        while True:
                            line = input()
                            if line == "": break
                            lines.append(line)
                        drafts["post_connection"] = "\n".join(lines).strip()
                        edits = learnings.get("message_edits",[])
                        edits.append({"company": cname, "person": pname_display,
                                      "date": datetime.now().isoformat()})
                        learnings["message_edits"] = edits
                        save_learnings(learnings)
                        choice = "a" if linkedin_notes_available else "p"

                    if choice in ["a", "p"]:
                        save_note = (choice == "a" and linkedin_notes_available
                                     and drafts.get("connection_note"))
                        record = build_contact_record(
                            company, theta,
                            {"variant":1, "channel": drafts["channel"],
                             "tone":"connection", "subject":"",
                             "body": drafts["post_connection"]},
                            cs_context)
                        note_parts = []
                        if save_note:
                            note_parts.append(f"LinkedIn connection note: {drafts['connection_note']}\n")
                        note_parts.append(f"First message (post-connection):\n{drafts['post_connection']}")
                        record["notes"]                = "\n".join(note_parts)
                        record["name"]                 = person.get("name","") if person.get("name") not in ("Search required","") else ""
                        record["jobTitle"]             = person.get("title","")
                        record["_linkedin_search"]     = person.get("linkedin_search","")
                        record["_has_connection_note"] = save_note
                        approved_new.append(record)
                        status_msg = "connection note + message" if save_note else "message only (blank invite)"
                        print(f"  ✓ Added: {pname_display} — {status_msg}")

                print(f"\n  💰 {cost.summary()}")

    # Save outputs
    banner("💾  SAVING OUTPUTS")
    save_all(approved_new, followup_actions, warm, patterns, cost)

    if approved_new:
        print(f"  ✓ {len(approved_new)} new contacts → {EXPORT_PATH}")
        print(f"    Import via '↑ Import CSV' in your tracker")
    if followup_actions:
        print(f"  ✓ {len(followup_actions)} reply drafts → {FOLLOWUP_PATH}")
    print(f"  ✓ Task list → {TASKS_PATH}")
    print(f"  ✓ Cost report → {COST_PATH}")

    banner("✅  DONE — v3.6")
    print(f"  New companies approved:  {len(approved_new)}")
    print(f"  Follow-ups drafted:      {len(followup_actions)}")
    print(f"  Warm contacts queued:    {len(warm)}")
    print(f"  💰 {cost.summary()}")
    print()
    print("  YOUR WEEK:")
    print("  ──────────────────────────────────────────────────────")
    if followup_actions:
        print(f"  Tue  → Send follow-up replies first (see {FOLLOWUP_PATH})")
    if approved_new:
        print(f"  Tue  → Import {EXPORT_PATH} into tracker")
        print(f"  Tue  → Use LinkedIn searches in notes to find each person")
    print(f"  Any day → python outreach_agent_v3_6.py --status  (quick check)")
    print(f"  Fri  → Export tracker → python outreach_agent_v3_6.py --friday")
    print()

if __name__ == "__main__":
    main()
