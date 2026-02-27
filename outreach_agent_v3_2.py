"""
Semi-Agentic Outreach System v3.2 — Phase 1 Complete
=====================================================
Now fully aligned to your tracker's JSON export format.

THREE MODES:
  python outreach_agent_v3_2.py           → Monday: audit + hunt + plan
  python outreach_agent_v3_2.py --status  → Any day: quick check, no AI, no cost
  python outreach_agent_v3_2.py --friday  → Friday: upload JSON, CEO review, save learnings

TRACKER DATA MODEL (from your HTML export):
  contacts[]
    .id, .name, .company, .jobTitle, .tier, .industryFocus, .country
    .connectionMethod, .connectionStatus, .priority, .funnelStage
    .opportunityType, .nextActionDate, .tags[], .notes
    .communicationLog[]
      .date, .channel, .engagementType, .message, .response, .sentiment
      .duration, .location
    .createdAt, .lastMessage

  learnings[]  — your weekly learning entries (also in export)
  weeklyGoals  — tracked in export
  monthlyGoals — tracked in export

WORKFLOW:
  Monday 9am   → export JSON from tracker → run this script → import new_contacts JSON
  Tue–Thu      → outreach in tracker, log everything
  Friday 4pm   → export updated JSON → run --friday → read CEO review
  Next Monday  → script auto-reads Friday's learnings file, no prep needed
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

# Platforms we can suggest beyond LinkedIn
ALL_PLATFORMS = ["LinkedIn", "Email", "Twitter/X", "WhatsApp", "Substack", "Newsletter", "Conference", "Personal Intro"]

# Industries for gap analysis
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
# TRACKER JSON LOADER
# Handles both raw contacts array and full backup format
# ─────────────────────────────────────────────────────────────

def load_tracker_json(path: str) -> dict:
    """
    Load tracker JSON. Handles two formats:
    1. Full backup: { version, contacts, learnings, weeklyGoals, monthlyGoals, ... }
    2. Simple import: { contacts: [...] }
    3. Legacy: just an array of contacts
    """
    p = Path(path)
    if not p.exists():
        print(f"❌  File not found: {path}")
        sys.exit(1)
    
    try:
        raw = json.loads(p.read_text(encoding="utf-8"))
    except json.JSONDecodeError as e:
        print(f"❌  JSON parse error in {path}: {e}")
        sys.exit(1)
    
    # Handle array directly
    if isinstance(raw, list):
        return {"contacts": raw, "learnings": [], "weeklyGoals": {}, "monthlyGoals": {}}
    
    # Handle dict formats
    contacts = raw.get("contacts", [])
    return {
        "contacts":          contacts,
        "learnings":         raw.get("learnings", []),
        "weeklyGoals":       raw.get("weeklyGoals", {}),
        "weeklyGoalsHistory":raw.get("weeklyGoalsHistory", []),
        "monthlyGoals":      raw.get("monthlyGoals", {}),
        "monthlyGoalsHistory":raw.get("monthlyGoalsHistory", []),
        "templates":         raw.get("templates", []),
        "exportDate":        raw.get("exportDate", ""),
        "version":           raw.get("version", ""),
        "source_file":       path,
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
    COSTS = {
        "hunt":             0.15,
        "research":         0.12,
        "cached_research":  0.01,
        "draft":            0.05,
        "followup_draft":   0.03,
        "theta":            0.02,
        "analysis":         0.08,
        "reflection":       0.10,
        "ceo_review":       0.15,
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

    def save(self):
        OUTPUTS_DIR.mkdir(exist_ok=True)
        with open(COST_PATH, "w") as f:
            json.dump({
                "week_of": datetime.now().strftime("%Y-%m-%d"),
                "total_spent": round(self.spent, 4),
                "budget": self.budget,
                "remaining": round(self.remaining(), 4),
                "operations": self.log,
            }, f, indent=2)

# ─────────────────────────────────────────────────────────────
# RESEARCH CACHE
# ─────────────────────────────────────────────────────────────

CACHE_TTL = {"background": 90, "news": 7}

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

# ─────────────────────────────────────────────────────────────
# LEARNING ENGINE — reads tracker's own data model
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
    """Full pattern analysis across all communication logs."""
    channel_stats   = defaultdict(lambda: {"sent":0,"responded":0,"positive":0})
    engagement_stats= defaultdict(lambda: {"count":0,"responses":0})
    angle_wins      = defaultdict(int)
    angle_attempts  = defaultdict(int)
    seniority_stats = defaultdict(lambda: {"sent":0,"responded":0,"positive":0,"angles":[]})
    industry_stats  = defaultdict(lambda: {"sent":0,"responded":0})
    timing          = []

    for c in contacts:
        logs      = c.get("communicationLog", [])
        title     = c.get("jobTitle", "")
        seniority = classify_seniority(title)
        industry  = c.get("industryFocus", "Other")

        for log in logs:
            ch            = log.get("channel", "LinkedIn")
            eng_type      = log.get("engagementType", "")
            response      = (log.get("response","") or "").strip()
            sentiment     = log.get("sentiment","")
            msg           = (log.get("message","") or "").lower()
            date_str      = log.get("date","")
            responded     = bool(response) and response.lower() not in ["no response","none"]
            positive      = sentiment in ("Positive","Very positive")

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

    top_channel = max(channel_rates, key=lambda k: channel_rates[k]["rate_pct"], default="Email")
    top_angle   = max(angle_rates,   key=lambda k: angle_rates[k]["rate_pct"],   default="research_observation")
    top_industry= max(industry_rates, key=lambda k: industry_rates[k]["rate_pct"], default="")

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
        "history": [],
        "patterns": {},
        "strategy_notes": [],
        "archetype_corrections": {},
        "industry_performance": {},
        "last_tracker_file": "",
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
            "framework_intro": "second_touch",
            "proof": "digital_twin_or_platform_case",
            "note": "CTOs respond to ecosystem/platform language. Siemens Xcelerator angle works well.",
        },
        "ceo": {
            "style": "strategic_vision",
            "opening": "market_insight",
            "framework_intro": "second_touch",
            "proof": "business_model_case",
            "note": "CEOs respond to portfolio-level questions and peer pressure framing.",
        },
        "cso": {
            "style": "portfolio_governance",
            "opening": "pain_point_question",
            "framework_intro": "first_touch_light",
            "proof": "portfolio_case",
            "note": "Strategy officers respond to governance and KPI misalignment framing.",
        },
        "vp_director": {
            "style": "problem_focused",
            "opening": "research_observation",
            "framework_intro": "second_touch",
            "proof": "sector_case",
            "note": "Directors appreciate specific observations over vision-level openers.",
        },
        "innovation_lead": {
            "style": "practitioner_peer",
            "opening": "peer_acknowledgment",
            "framework_intro": "first_touch_light",
            "proof": "builders_over_storytellers",
            "note": "Innovation leads respond to 'builders over storytellers' framing.",
        },
    }

    strategy = role_strategies.get(seniority, {
        "style": "curiosity_led",
        "opening": "observation",
        "framework_intro": "third_touch",
        "proof": "relevant_case",
        "note": "Default: open with curiosity, no framework mention.",
    })

    return {
        **strategy,
        "seniority": seniority,
        "top_angle": top_angle,
        "recommended_channel": top_ch,
        "best_send": patterns.get("best_send_window","Tuesday 09:00–12:00"),
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

    gaps_sorted = sorted(gaps, key=lambda x: x["count"])
    underrepresented = [g["industry"] for g in gaps_sorted if g["count"] == 0][:8]
    present = [g["industry"] for g in sorted(gaps, key=lambda x: -x["count"]) if g["count"] > 0]

    # Overlay performance data
    ind_rates = patterns.get("industry_rates", {})
    best_performing = sorted(ind_rates.items(), key=lambda x: -x[1]["rate_pct"])[:3]

    return {
        "covered":           covered,
        "underrepresented":  underrepresented,
        "present_industries": present,
        "best_performing":   [b[0] for b in best_performing if b[1]["rate_pct"] > 0],
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
        # 2 underrepresented + 1 best-performing (deepen) + 1 present (deepen)
        suggested = gaps["underrepresented"][:2]
        if gaps["best_performing"]:
            suggested.append(gaps["best_performing"][0])
        elif gaps["present_industries"]:
            suggested.append(gaps["present_industries"][0])
        if len(suggested) < 3 and gaps["present_industries"]:
            suggested.append(gaps["present_industries"][0])
        suggested = list(dict.fromkeys(suggested))[:4]  # dedup
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
            indices = [int(x.strip()) - 1 for x in raw.split(",") if x.strip().isdigit()]
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
# THETA FRAMEWORK
# ─────────────────────────────────────────────────────────────

CORE_SIGNALS   = [
    "operational excellence","efficiency","optimization","cost reduction",
    "process improvement","quality","reliability","scale","margin","profitability",
    "customer retention","traditional","incumbent","legacy","core business",
    "existing products","sustaining","agile","lean","continuous improvement",
    "cost discipline","productivity","supply chain optimization","erp","six sigma",
]
EDGE_SIGNALS   = [
    "pilot","experiment","venture","new business","adjacent","digital transformation",
    "platform","ecosystem","partnership","spin-off","incubator","accelerator",
    "next generation","growth initiative","s-curve","new market","emerging","startup",
    "product launch","beta","new revenue","digital twin","xcelerator","open innovation",
    "business model innovation","new venture","corporate venture","innovation unit",
    "digital business","data platform","ai transformation","cloud transformation",
]
BEYOND_SIGNALS = [
    "moonshot","quantum","deep tech","10x","breakthrough","research lab",
    "fundamental research","2030","2035","2040","future of","reinvent","disruption",
    "frontier","autonomous","fusion","biotech","nanotechnology","ai research",
    "basic research","horizon 3","beyond","long-term bet","venture studio",
    "exponential","synthetic biology","space","climate tech","net zero 2040",
]
THEATER_SIGNALS= [
    "innovation lab","innovation hub","digital lab","center of excellence",
    "hackathon","ideation","prototype","proof of concept","poc",
    "we're exploring","looking into","vision for","roadmap for 2030",
    "innovation theater","announce","showcase","award",
    "innovation day","pitch competition","startup program","innovation challenge",
]

def score_signals(text: str, signals: list) -> int:
    return min(10, sum(1 for s in signals if s in text.lower()))

def theta_assess(research_text: str, company_name: str, industry: str,
                 override_archetype: str = None) -> dict:
    text   = research_text.lower()
    core   = score_signals(text, CORE_SIGNALS)
    edge   = score_signals(text, EDGE_SIGNALS)
    beyond = score_signals(text, BEYOND_SIGNALS)
    theater= score_signals(text, THEATER_SIGNALS)

    archetype_map = {
        "Innovation Theater":     (
            "Labs and pilots don't ship — strong on announcement, weak on scaling",
            "Move 4: Builders Over Storytellers",
            f"I noticed {company_name} has impressive innovation programs. How do you measure which pilots are actually on a path to market?",
        ),
        "Stuck in Core":          (
            "Over-indexed on Core — missing Edge bets, disruption risk growing",
            "Move 2: Rewire the System",
            f"As {industry} faces disruption, how is {company_name} building its next S-curve without destabilizing the core?",
        ),
        "Edge-Active, No Beyond": (
            "Strong Edge activity but no long-horizon vision — short-termism risk",
            "Move 3: Measure What Matters",
            f"{company_name}'s Edge work looks strong. How are you planting seeds for where the business needs to be in 2032+?",
        ),
        "Balanced Transformer":   (
            "Core/Edge tension — governance and metrics likely misaligned",
            "Move 1: Deep Audit",
            f"Curious how {company_name} governs the tension between optimizing today and investing in what comes next.",
        ),
        "Frontier Builder":       (
            "Beyond bets lack Core/Edge bridge — risk of stranded moonshots",
            "Move 2: Rewire",
            f"{company_name}'s frontier work is impressive. How are you building the bridge from R&D to scalable Edge business?",
        ),
        "Early Explorer":         (
            "Innovation portfolio not yet systematically managed",
            "Move 1: Deep Audit",
            f"How is {company_name} thinking about managing its innovation portfolio as the business scales?",
        ),
    }

    if override_archetype and override_archetype in archetype_map:
        archetype = override_archetype
    elif theater >= 3:
        archetype = "Innovation Theater"
    elif core >= 6 and edge < 3:
        archetype = "Stuck in Core"
    elif edge >= 5 and beyond < 2:
        archetype = "Edge-Active, No Beyond"
    elif edge >= 4 and core >= 5:
        archetype = "Balanced Transformer"
    elif beyond >= 4:
        archetype = "Frontier Builder"
    else:
        archetype = "Early Explorer"

    pain, move, angle = archetype_map[archetype]

    gaps = []
    if edge < 3: gaps.append("No visible Edge / next S-curve work")
    if beyond < 2: gaps.append("No long-horizon Beyond bets")
    if theater >= 3: gaps.append("Innovation theater risk: labs don't ship")
    if core >= 7 and edge < 3: gaps.append("Core-heavy: disruption vulnerability")
    if not gaps: gaps.append("Portfolio reasonably active — governance clarity is the opportunity")

    primary = max({"core":core,"edge":edge,"beyond":beyond}, key=lambda k:{"core":core,"edge":edge,"beyond":beyond}[k])
    zone_emoji = {"core":"🟩","edge":"🟨","beyond":"🟥"}

    return {
        "zone_distribution": {"core": core, "edge": edge, "beyond": beyond},
        "theater_risk": theater,
        "primary_zone": primary,
        "archetype": archetype,
        "pain_point": pain,
        "gaps": gaps,
        "recommended_move": move,
        "messaging_angle": angle,
        "zone_summary": f"{zone_emoji.get(primary,'🟨')} {primary.capitalize()} zone | {archetype}",
    }

VALID_ARCHETYPES = list({"Innovation Theater","Stuck in Core","Edge-Active, No Beyond",
                          "Balanced Transformer","Frontier Builder","Early Explorer"})

# ─────────────────────────────────────────────────────────────
# STEP 0 — STATUS CHECK (any day, no AI cost)
# ─────────────────────────────────────────────────────────────

def status_check(contacts: list, learnings: dict):
    banner(f"📋  DAILY STATUS CHECK  |  {datetime.now().strftime('%A %B %d %Y')}")
    print(f"  {day_greeting()}\n")

    now     = datetime.now(timezone.utc)
    today   = datetime.now().date()
    week_start = today - timedelta(days=today.weekday())  # Monday

    # ── Urgent: responded but not replied
    urgent = []
    for c in contacts:
        logs = c.get("communicationLog",[])
        real = lambda l: (l.get("response","") or "").strip() and \
                          (l.get("response","") or "").lower() not in ["no response","none"]
        if len(logs) == 1 and real(logs[0]):
            urgent.append(c)
        elif len(logs) >= 2:
            # Last message is from them (has response) but we haven't followed up
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

    # ── Due today / overdue
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
                days_over = (today - nd_date).days
                overdue.append({**c, "_days_over": days_over})
        except: pass

    print(f"\n  📅  DUE TODAY ({len(due_today)}):")
    if due_today:
        for c in due_today[:5]:
            print(f"     • {c['name']} @ {c['company']} [{c.get('priority','')}] — {c.get('funnelStage','')}")
    else:
        print("     Nothing due today.")

    print(f"\n  ⚠️   OVERDUE ({len(overdue)}):")
    if overdue:
        for c in sorted(overdue, key=lambda x: -x["_days_over"])[:5]:
            print(f"     • {c['name']} @ {c['company']} — {c['_days_over']}d overdue [{c.get('priority','')}]")
    else:
        print("     Nothing overdue.")

    # ── Pipeline snapshot
    funnel_counts = defaultdict(int)
    for c in contacts:
        funnel_counts[c.get("funnelStage","Unaware")] += 1

    print(f"\n  📊  PIPELINE:")
    funnel_order = ["Unaware","Awareness","Engaged","Consideration","Active Conversation","Conversion"]
    for stage in funnel_order:
        count = funnel_counts.get(stage, 0)
        bar   = "█" * count
        print(f"     {stage:22s} {bar} {count}")

    # ── This week's activity
    this_week_touches = 0
    this_week_responses = 0
    for c in contacts:
        for log in c.get("communicationLog",[]):
            try:
                log_date = datetime.fromisoformat(log["date"].replace("Z","")).date()
                if log_date >= week_start:
                    this_week_touches += 1
                    if (log.get("response","") or "").strip():
                        this_week_responses += 1
            except: pass

    print(f"\n  📬  THIS WEEK: {this_week_touches} touches sent, {this_week_responses} responses received")

    # ── Last strategy note
    strategy_notes = learnings.get("strategy_notes",[])
    if strategy_notes:
        print(f"\n  💡  LAST STRATEGY NOTE:")
        for line in strategy_notes[-1].splitlines()[:4]:
            if line.strip(): print(f"     {line}")

    print()

# ─────────────────────────────────────────────────────────────
# STEP 1 — AUDIT
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
                           "_our_message": logs[0].get("message",""),
                           "_channel": logs[0].get("channel","LinkedIn"),
                           "_date": logs[0].get("date","")})

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
            print(f"\n  🏭 Best responding industry: {top[0][0]} ({top[0][1]['rate_pct']:.0f}%)" if top else "")
        print(f"  🎯 Best angle: {patterns.get('top_angle','—')}  |  Best time: {patterns.get('best_send_window','—')}")

    print(f"\n  🔴  URGENT — Responded, awaiting your reply: {len(urgent)}")
    for i, c in enumerate(urgent, 1):
        resp = (c.get("_their_response","") or "")[:100]
        print(f"\n  {i}. {c['name']} @ {c['company']} [{c.get('priority','')}]")
        if c.get("jobTitle"): print(f"     {c['jobTitle']}")
        channel = c.get("_channel","LinkedIn")
        print(f"     Channel: {channel}")
        print(f"     Their reply: \"{resp}{'...' if len(resp)==100 else ''}\"")

    print(f"\n  🟡  WARM — Connected, never messaged: {len(warm)}")
    for c in warm:
        print(f"     • {c.get('name','')} @ {c.get('company','')} [{c.get('priority','')}]  {c.get('jobTitle','')}")

    print(f"\n  ⬜  STALE — {len(stale)} contacts messaged 10+ days ago, no response")
    for c in sorted(stale, key=lambda x: x.get("_days_ago",0), reverse=True)[:5]:
        print(f"     • {c.get('name','')} @ {c.get('company','')} ({c.get('_days_ago',0)}d ago, {c.get('_touches',1)} touch)")

# ─────────────────────────────────────────────────────────────
# STEP 2 — DRAFT FOLLOW-UP REPLIES
# ─────────────────────────────────────────────────────────────

def draft_reply(client, contact: dict, config: dict, patterns: dict) -> str:
    strategy  = get_messaging_strategy(contact, patterns)
    name      = (contact.get("name","") or "").split()[0] or "there"
    company   = contact.get("company","")
    title     = contact.get("jobTitle","")
    their_resp= contact.get("_their_response","")
    our_msg   = contact.get("_our_message","")
    channel   = contact.get("_channel","LinkedIn")
    user_name = config.get("user_short_name","Pam")

    prompt = f"""You are drafting a reply for {config.get('user_name','Christine Pamela')}, an innovation consultant (Theta Framework).

CONTACT: {name} — {title} at {company}
CHANNEL: {channel}
SENIORITY TYPE: {strategy['seniority']}

PAM'S ORIGINAL MESSAGE:
{our_msg}

THEIR RESPONSE:
{their_resp}

Draft 3 reply variants. Each should:
- Acknowledge their specific response genuinely (reference what they actually said)
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
        r = client.messages.create(model="claude-haiku-4-5-20251001", max_tokens=700,
                                    messages=[{"role":"user","content":prompt}])
        return r.content[0].text.strip()
    except Exception as e:
        return f"---REPLY 1---\nHi {name},\n\nThank you for responding — I'd love to set up a brief call to continue this conversation.\n\nWould 20 minutes work for you this week?\n\nBest,\n{user_name}"

def approval_loop_followup(contact: dict, draft_text: str):
    section(f"FOLLOW-UP: {contact.get('name','')} @ {contact.get('company','')}")
    print(f"  {contact.get('jobTitle','')} | {contact.get('priority','')} priority")
    resp = contact.get("_their_response","")
    print(f"\n  Their reply:")
    print(f"  \"{resp[:200]}{'...' if len(resp)>200 else ''}\"")
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
        print("  Paste reply (Enter twice to finish):")
        lines = []
        while True:
            line = input()
            if line == "" and lines and lines[-1] == "":
                break
            lines.append(line)
        return "\n".join(lines[:-1]).strip()
    return replies[0] if replies else None

# ─────────────────────────────────────────────────────────────
# STEP 3 — HUNT
# ─────────────────────────────────────────────────────────────

def hunt_new_companies(client, existing_companies: list, config: dict,
                       industries: list, country_focus: str, target_count: int) -> list:
    section("STEP 2 — HUNTING NEW COMPANIES")
    industry_str = ", ".join(industries) if industries else "any major industry"
    country_str  = f"Prefer companies in or with major operations in: {country_focus}." if country_focus else "Global — any geography."
    print(f"  Industries: {industry_str}")
    print(f"  Geography: {country_str}")
    print(f"  Count: {target_count}")
    print(f"  Searching...")

    existing_str = ", ".join(sorted(set(existing_companies)))

    prompt = f"""You are a business intelligence researcher for Christine Pamela, an innovation consultant (Theta Framework).

INDUSTRIES THIS WEEK: {industry_str}
GEOGRAPHY: {country_str}

DO NOT SUGGEST (already in tracker): {existing_str}

Identify exactly {target_count} large enterprises (2000+ employees) with visible tension between
optimizing their core business and building next-generation growth.

For each, find the most relevant contact for Theta conversations — ideally:
Chief Innovation Officer, Chief Strategy Officer, VP Digital Transformation,
Head of Corporate Venture, CTO, Head of Innovation Portfolio.

DO NOT invent specific names. Provide ranked LinkedIn search strings instead.
Also consider if Twitter/X, Substack, or email might be better first-touch channels for this person.

Output EXACTLY:

---COMPANY---
NAME: [Company]
INDUSTRY: [Industry]
COUNTRY: [HQ country]
SIZE: [Approx employees]
WHY_THETA_FIT: [2 sentences on innovation tension]
THETA_ARCHETYPE: [Stuck in Core / Innovation Theater / Edge-Active No Beyond / Balanced Transformer / Frontier Builder]
STRENGTHS: [1 sentence on what they do well]
PAIN_POINTS: [1-2 sentences on visible innovation gaps]
RECENT_SIGNAL: [One specific recent initiative or announcement]
TARGET_ROLES: ["Company" "Chief Innovation Officer" | "Company" "VP Digital Transformation" | etc — 3-5 ranked]
BEST_CHANNEL: [LinkedIn / Email / Twitter/X / Other — with brief reason]"""

    try:
        r = client.messages.create(model="claude-haiku-4-5-20251001", max_tokens=4500,
                                    messages=[{"role":"user","content":prompt}])
        raw = r.content[0].text.strip()
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
        print(f"  ✓ {len(companies)} companies identified")
        return companies[:target_count]
    except Exception as e:
        print(f"  ✗ Hunt failed: {e}")
        return []

# ─────────────────────────────────────────────────────────────
# STEP 4 — RESEARCH + THETA
# ─────────────────────────────────────────────────────────────

def research_company(client, company: dict, cost: CostTracker) -> dict:
    name     = company.get("name","")
    industry = company.get("industry","")
    cached   = load_cache(name)

    if "background" in cached:
        background = cached["background"]
        cost.charge("cached_research", note=f"{name} (cached)")
    else:
        prompt = f"""Research {name} ({industry}). Provide:

1. Core business and revenue model (2 sentences)
2. Named innovation programs, R&D efforts, digital transformation (be specific)
3. Innovation maturity: do they actually ship or mostly announce?
4. Key STRENGTHS in their innovation approach
5. Key PAIN POINTS or gaps — where is their innovation portfolio weak?
6. Strategic priorities 2024–2026

Under 300 words. Be honest about both strengths and weaknesses."""
        try:
            r = client.messages.create(model="claude-haiku-4-5-20251001", max_tokens=500,
                                        messages=[{"role":"user","content":prompt}])
            background = r.content[0].text.strip()
            save_cache(name, {"background": background})
            cost.charge("research", note=f"{name} (fresh)")
        except Exception as e:
            background = f"Research unavailable: {e}"
            cost.charge("cached_research", note=f"{name} (error)")

    combined = f"{company.get('why_theta_fit','')} {company.get('recent_signal','')} {company.get('strengths','')} {company.get('pain_points','')} {background}"
    theta    = theta_assess(combined, name, industry)
    cost.charge("theta", note=name)

    return {"background": background, "theta": theta, "was_cached": "background" in cached}

# ─────────────────────────────────────────────────────────────
# STEP 5 — CASE STUDY / ARTICLE CHECK
# ─────────────────────────────────────────────────────────────

def ask_case_study_context(company_name: str, industry: str) -> dict:
    print(f"\n  ── YOUR CONTENT FOR {company_name.upper()} ──")
    context = {"has_case_study": False, "building_case_study": False,
               "case_study_note": "", "articles": []}

    has_cs = ask(f"  Case study relevant to {company_name} or {industry}?", ["y","n"])
    if has_cs == "y":
        context["has_case_study"] = True
        context["case_study_note"] = input("  Brief description (company/topic): ").strip()
    else:
        building = ask(f"  Building one?", ["y","n"])
        if building == "y":
            context["building_case_study"] = True

    has_art = ask(f"  Articles you've written relevant to them?", ["y","n"])
    if has_art == "y":
        print("  Paste URLs one per line (Enter twice to finish):")
        articles = []
        while True:
            line = input("  > ").strip()
            if not line and (not articles or articles[-1] == ""):
                break
            if line:
                articles.append(line)
        context["articles"] = articles

    return context

# ─────────────────────────────────────────────────────────────
# STEP 6 — DRAFT FIRST-TOUCH MESSAGES
# ─────────────────────────────────────────────────────────────

def draft_first_touch(client, company: dict, theta: dict, strategy: dict,
                       config: dict, patterns: dict, cs_context: dict) -> list:
    user_name  = config.get("user_short_name","Pam")
    top_angle  = patterns.get("top_angle","research_observation")
    best_send  = patterns.get("best_send_window","Tuesday 09:00–12:00")
    seniority  = strategy.get("seniority","other")
    style_note = strategy.get("note","Lead with curiosity.")

    # Best channel from hunt
    best_channel = company.get("best_channel","LinkedIn")

    content_hook = ""
    if cs_context.get("has_case_study"):
        content_hook = f"Pam has a case study: {cs_context.get('case_study_note','yes')}. One variant should reference it naturally."
    elif cs_context.get("building_case_study"):
        content_hook = f"Pam is building a case study on {company.get('industry','')}. One variant can mention this research angle."
    if cs_context.get("articles"):
        content_hook += f" Pam has written: {', '.join(cs_context['articles'][:2])}. One variant shares the article as a value-add."

    target_roles_raw = company.get("target_roles","")
    search_strings   = [s.strip() for s in target_roles_raw.split("|") if s.strip()]
    first_search     = search_strings[0] if search_strings else f'"{company.get("name","")}" "Chief Innovation Officer"'

    prompt = f"""Draft outreach messages for {config.get('user_name','Christine Pamela')}, an innovation consultant (Theta Framework).

TARGET: {company.get('name','')} | {company.get('industry','')} | {company.get('country','')}
MOST LIKELY ROLE: {first_search}
SENIORITY: {seniority}
BEST CHANNEL THIS COMPANY: {best_channel}

COMPANY STRENGTHS: {company.get('strengths','')}
COMPANY PAIN POINTS: {company.get('pain_points','')}
RECENT SIGNAL: {company.get('recent_signal','')}

THETA:
- Archetype: {theta.get('archetype','')}
- Pain: {theta.get('pain_point','')}
- Move: {theta.get('recommended_move','')}
- Angle: {theta.get('messaging_angle','')}

STRATEGY: Best angle={top_angle} | Style={style_note} | Best send={best_send}

PAM'S CONTENT: {content_hook if content_hook else "No specific content this week."}

RULES:
1. First touch only — no framework pitch, no selling
2. Lead with ONE genuine observation or question
3. LinkedIn: max 4 sentences. Email: max 6 sentences + subject line
4. 5 variants, each meaningfully different in angle
5. Do NOT use the contact's name (not yet confirmed)
6. If article/case study exists, ONE variant references it as a natural share
7. Sign as "{user_name}"

Write EXACTLY 5 variants:

---VARIANT 1---
CHANNEL: LinkedIn
TONE: [label]
BODY:
[message]

---VARIANT 2---
CHANNEL: LinkedIn
TONE: [label]
BODY:
[message]

---VARIANT 3---
CHANNEL: LinkedIn
TONE: [label]
BODY:
[message]

---VARIANT 4---
CHANNEL: Email
SUBJECT: [subject]
TONE: [label]
BODY:
[message]

---VARIANT 5---
CHANNEL: Email
SUBJECT: [subject]
TONE: [label]
BODY:
[message]"""

    try:
        r = client.messages.create(model="claude-haiku-4-5-20251001", max_tokens=2000,
                                    messages=[{"role":"user","content":prompt}])
        return parse_variants(r.content[0].text.strip())
    except Exception as e:
        return [{"variant":1,"channel":"LinkedIn","tone":"fallback","subject":"",
                 "body":f"[Draft failed: {e}]"}]

def parse_variants(raw: str) -> list:
    variants = []
    for i, block in enumerate(raw.split("---VARIANT")[1:], 1):
        v = {"variant":i,"channel":"LinkedIn","tone":"","subject":"","body":""}
        body_lines, in_body = [], False
        for line in block.strip().splitlines():
            s = line.strip()
            if s.startswith("CHANNEL:"): v["channel"] = s[8:].strip()
            elif s.startswith("TONE:"):   v["tone"]    = s[5:].strip()
            elif s.startswith("SUBJECT:"): v["subject"] = s[8:].strip()
            elif s.startswith("BODY:"):   in_body = True
            elif s.startswith("---"):     in_body = False
            elif in_body:                 body_lines.append(line)
        v["body"] = "\n".join(body_lines).strip()
        if v["body"]: variants.append(v)
    return variants[:5]

# ─────────────────────────────────────────────────────────────
# STEP 7 — APPROVAL LOOP
# ─────────────────────────────────────────────────────────────

def approval_loop_new(company: dict, theta: dict, research: dict,
                      variants: list, cs_context: dict, learnings: dict) -> tuple:
    section(f"NEW COMPANY: {company.get('name','')}")
    cached_tag = " (cached)" if research.get("was_cached") else ""

    print(f"  {company.get('industry','')} | {company.get('country','')} | ~{company.get('size','?')} employees")
    print(f"  Recommended channel: {company.get('best_channel','LinkedIn')}")

    target_roles_raw = company.get("target_roles","")
    search_strings = [s.strip() for s in target_roles_raw.split("|") if s.strip()]
    print(f"\n  🔍  LinkedIn searches (ranked):")
    for i, s in enumerate(search_strings[:5], 1):
        print(f"     {i}. {s}")

    print(f"\n  💪  Strengths:    {company.get('strengths','—')}")
    print(f"  ⚠️   Pain points:  {company.get('pain_points','—')}")
    print(f"  📡   Recent:       {company.get('recent_signal','—')}")
    print(f"\n  Theta:  {theta.get('archetype','')}  |  {theta.get('zone_summary','')}")
    print(f"  Zones:  Core={theta['zone_distribution']['core']} Edge={theta['zone_distribution']['edge']} Beyond={theta['zone_distribution']['beyond']}")
    print(f"  Pain:   {theta.get('pain_point','')}")
    print(f"  Move:   {theta.get('recommended_move','')}")
    bg = research.get("background","")
    print(f"  Research{cached_tag}: {bg[:200]}{'...' if len(bg)>200 else ''}")

    if cs_context.get("has_case_study"):
        print(f"\n  📎  Case study: {cs_context.get('case_study_note','on file')}")
    if cs_context.get("articles"):
        print(f"  📝  Articles: {', '.join(cs_context['articles'][:2])}")

    print("\n  ── MESSAGE VARIANTS ──")
    for v in variants:
        print(f"\n  [{v['variant']}] {v['channel']} | {v['tone']}")
        if v.get("subject"): print(f"      Subject: {v['subject']}")
        print()
        for line in v["body"].splitlines():
            print(f"      {line}")

    print()
    print("  y=approve v1 / 1-5=pick variant / e=edit / t=correct archetype / n=skip")
    choice = input("  Decision? ").strip().lower()

    if choice == "n":
        return None, None

    if choice == "t":
        print(f"\n  Current: {theta.get('archetype','')}")
        for i, a in enumerate(VALID_ARCHETYPES, 1):
            print(f"    {i}. {a}")
        raw = input("  Correct archetype number: ").strip()
        if raw.isdigit() and 1 <= int(raw) <= len(VALID_ARCHETYPES):
            correct = VALID_ARCHETYPES[int(raw)-1]
            corrections = learnings.get("archetype_corrections", {})
            corrections[company.get("name","")] = {
                "was": theta.get("archetype"),
                "corrected_to": correct,
                "date": datetime.now().isoformat(),
            }
            learnings["archetype_corrections"] = corrections
            save_learnings(learnings)
            theta.update(theta_assess(research.get("background",""),
                                      company.get("name",""), company.get("industry",""),
                                      override_archetype=correct))
            print(f"  ✓ Corrected to: {correct}")
        choice = input("  Decision? [y/1-5/e/n]: ").strip().lower()

    if choice == "n":
        return None, None
    if choice in ["1","2","3","4","5"]:
        idx = int(choice) - 1
        return "approved", variants[idx] if idx < len(variants) else variants[0]
    if choice == "e":
        print("  Paste message (Enter twice to finish):")
        lines = []
        while True:
            line = input()
            if line == "" and lines and lines[-1] == "":
                break
            lines.append(line)
        body   = "\n".join(lines[:-1]).strip()
        ch_raw = input("  Channel? [LinkedIn / Email / Other]: ").strip() or "LinkedIn"
        subj   = ""
        if "email" in ch_raw.lower():
            subj = input("  Subject: ").strip()
        return "approved", {"variant":0,"channel":ch_raw,"tone":"custom","subject":subj,"body":body}

    return "approved", variants[0] if variants else None

# ─────────────────────────────────────────────────────────────
# STEP 8 — BUILD CONTACT RECORD (tracker-compatible format)
# ─────────────────────────────────────────────────────────────

def build_contact_record(company: dict, theta: dict, chosen_variant: dict,
                          cs_context: dict) -> dict:
    """
    Builds a contact record matching your tracker's exact JSON schema.
    Name/jobTitle are intentionally blank — fill after LinkedIn search.
    """
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
        # ── Fields matching your tracker schema ──
        "id":               str(uuid.uuid4()),
        "company":          company.get("name","").strip(),
        "name":             "",          # Fill after LinkedIn search
        "jobTitle":         "",          # Fill after LinkedIn search
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
        # ── Notes field carries all intelligence ──
        "notes": (
            f"Theta archetype: {theta.get('archetype','')}\n"
            f"Zone: Core={theta['zone_distribution']['core']} "
            f"Edge={theta['zone_distribution']['edge']} "
            f"Beyond={theta['zone_distribution']['beyond']}\n"
            f"Pain point: {theta.get('pain_point','')}\n"
            f"Strengths: {company.get('strengths','')}\n"
            f"Recommended move: {theta.get('recommended_move','')}\n"
            f"Recent signal: {company.get('recent_signal','')}\n"
            f"Best channel: {company.get('best_channel','LinkedIn')}\n"
            f"\n── FIND YOUR CONTACT ──\n{linkedin_block}"
            f"{content_note}"
        ),
        # ── Draft message (extra field for your reference) ──
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
    tasks      = []
    best_send  = patterns.get("best_send_window","Tuesday 09:00–12:00")

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
        draft  = c.get("draft_message",{})
        notes  = c.get("notes","")
        searches = [l.strip() for l in notes.splitlines() if "LinkedIn search" in l]
        channel  = draft.get("channel","LinkedIn")
        tasks.append({
            "priority": "2-NEW",
            "task":     f"First touch: [Find contact] @ {c['company']}",
            "channel":  channel,
            "action":   f"1. Find contact via searches below → 2. Connect/reach out → 3. Send message",
            "linkedin_searches": searches,
            "message":  draft.get("body",""),
            "subject":  draft.get("subject",""),
            "send_time": best_send,
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
        "generated_at":    datetime.now().isoformat(),
        "week_of":         datetime.now().strftime("%Y-%m-%d"),
        "best_send_window": best_send,
        "total_tasks":     len(tasks),
        "tasks":           tasks,
        "goal_hierarchy":  {
            "this_week":  "All urgent replies + new first touches",
            "this_month": "5 new conversations started",
            "quarterly":  "2 Theta consulting engagements opened",
        },
    }

def save_all(approved_new, followup_actions, warm, patterns, cost):
    OUTPUTS_DIR.mkdir(exist_ok=True)

    if approved_new:
        export = {
            "version":    "1.2",
            "exportDate": datetime.now(timezone.utc).isoformat().replace("+00:00","Z"),
            "source":     "semi-agentic-outreach-v3.2",
            "note":       (
                "Import into tracker via '↑ Import CSV' button.\n"
                "Name and jobTitle are blank — fill after you find the person on LinkedIn.\n"
                "draft_message contains your approved first-touch message."
            ),
            "contacts":   approved_new,
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
# FRIDAY REVIEW — CEO-STYLE WEEKLY SUMMARY
# ─────────────────────────────────────────────────────────────

def friday_review(client, config: dict, learnings: dict):
    banner("🪞  FRIDAY REVIEW")
    print("  Upload this week's tracker export for a CEO-level summary.\n")

    # Ask for file path
    print("  Step 1: Export your tracker → click '↓ Export CSV' → save the JSON file")
    print("  Step 2: Paste the full file path below\n")
    raw_path = input("  Path to this week's tracker JSON: ").strip().strip('"').strip("'")

    if not raw_path or not Path(raw_path).exists():
        print(f"  ❌  File not found: {raw_path}")
        print("  Tip: On Windows, right-click the file → Properties → copy the full path.")
        return

    tracker = load_tracker_json(raw_path)
    contacts = tracker["contacts"]
    print(f"\n  ✓ Loaded {len(contacts)} contacts from {Path(raw_path).name}")

    # Save as this week's tracker file for next Monday
    Path(TRACKER_PATH).parent.mkdir(exist_ok=True)
    Path(TRACKER_PATH).write_text(
        json.dumps(tracker, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"  ✓ Saved as data/outreach_import.json (Monday will pick this up automatically)")

    # Run pattern analysis
    patterns = analyze_patterns(contacts)

    # Compare to last week
    last_patterns = learnings.get("patterns", {})
    last_review   = learnings.get("history", [{}])
    last_week_cr  = last_review[-1].get("patterns",{}).get("channel_rates",{}) if last_review else {}

    # ── Display key metrics ──
    section("THIS WEEK'S NUMBERS")

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
                if cd >= week_start:
                    new_connections += 1
            except: pass
        funnel_moves[c.get("funnelStage","Unaware")] += 1

    response_rate = round(responses_this_week / max(touches_this_week,1) * 100)

    print(f"  Touches sent this week:   {touches_this_week}")
    print(f"  Responses received:       {responses_this_week}")
    print(f"  Response rate (week):     {response_rate}%")
    print(f"  New contacts added:       {new_connections}")
    print(f"\n  Pipeline snapshot:")
    for stage in ["Unaware","Awareness","Engaged","Consideration","Active Conversation","Conversion"]:
        count = funnel_moves.get(stage,0)
        print(f"    {stage:22s} {count}")

    print(f"\n  Channel rates (all time):")
    for ch, s in sorted(patterns.get("channel_rates",{}).items(), key=lambda x: -x[1]["rate_pct"]):
        bar = "█" * int(s["rate_pct"]/10) + "░" * (10-int(s["rate_pct"]/10))
        print(f"    {ch:14s} [{bar}] {s['rate_pct']:.0f}%")

    if patterns.get("industry_rates"):
        print(f"\n  Industry response rates:")
        for ind, s in sorted(patterns["industry_rates"].items(), key=lambda x: -x[1]["rate_pct"])[:5]:
            if s["sent"] > 0:
                print(f"    {ind:30s} {s['rate_pct']:.0f}% ({s['responded']}/{s['sent']})")

    # ── Danger signals ──
    section("DANGER SIGNALS")
    def _is_hot_cold(c):
        logs = c.get("communicationLog")
        if not logs: return False
        try:
            return (datetime.now() - datetime.fromisoformat(
                logs[0]["date"].replace("Z",""))).days > 14
        except: return False
    hot_cold = [c for c in contacts if c.get("priority") == "Hot" and _is_hot_cold(c)]

    stalled = [c for c in contacts
               if c.get("funnelStage") in ("Consideration","Active Conversation")
               and not c.get("nextActionDate")]

    overdue = [c for c in contacts if c.get("nextActionDate") and
               datetime.fromisoformat(c["nextActionDate"].replace("Z","") if "T" in (c.get("nextActionDate") or "") else c["nextActionDate"]+"T00:00:00").date() < today]

    if hot_cold:
        print(f"  🔴  {len(hot_cold)} Hot contacts silent 14+ days:")
        for c in hot_cold[:3]:
            print(f"       {c.get('name','')} @ {c.get('company','')}")
    if stalled:
        print(f"  🟡  {len(stalled)} contacts in Consideration/Active Conv with no follow-up date set")
    if overdue:
        print(f"  ⚠️   {len(overdue)} contacts with overdue action dates")
    if not hot_cold and not stalled and not overdue:
        print("  ✅  No critical danger signals this week.")

    # ── Reflection questions ──
    section("YOUR REFLECTION")
    print("  Answer these (Enter to skip):\n")

    notes = []
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
        if ans:
            notes.append({"q": q, "a": ans})
        print()

    # ── Generate CEO summary via Claude ──
    section("CEO SUMMARY — GENERATING...")

    funnel_str = "\n".join(f"  {k}: {v}" for k, v in funnel_moves.items())
    channel_str = "\n".join(
        f"  {ch}: {s['rate_pct']:.0f}% ({s['responded']}/{s['sent']})"
        for ch, s in patterns.get("channel_rates",{}).items())
    notes_str = "\n".join(f"Q: {n['q']}\nA: {n['a']}" for n in notes) if notes else "No reflection notes."

    danger_str = ""
    if hot_cold: danger_str += f"\n- {len(hot_cold)} Hot contacts silent 14+ days"
    if stalled:  danger_str += f"\n- {len(stalled)} conversations stalled with no follow-up date"
    if overdue:  danger_str += f"\n- {len(overdue)} overdue actions"

    prompt = f"""You are reviewing Christine Pamela's outreach week for her innovation consulting business (Theta Framework).

THIS WEEK'S METRICS:
- Touches sent: {touches_this_week}
- Responses received: {responses_this_week} ({response_rate}% rate)
- New contacts added: {new_connections}

PIPELINE:
{funnel_str}

CHANNEL PERFORMANCE:
{channel_str}

DANGER SIGNALS:{danger_str if danger_str else " None critical."}

PAM'S REFLECTION:
{notes_str}

Write a CEO-level weekly review in this structure:

HEADLINE (1 sentence on the week's performance)

WHAT WORKED
(2-3 bullets — specific, evidence-based)

WHAT DIDN'T
(1-2 bullets — honest)

PIPELINE HEALTH
(1 paragraph — is it moving, where is it bunching up, any conversion risk)

DANGER WATCH
(1-2 bullets — what needs attention before it becomes a problem)

RECOMMENDATION FOR MONDAY
(3 concrete, specific actions — numbered)

Keep it tight. Board-meeting level. No fluff."""

    try:
        r = client.messages.create(model="claude-haiku-4-5-20251001", max_tokens=700,
                                    messages=[{"role":"user","content":prompt}])
        review_text = r.content[0].text.strip()
    except Exception as e:
        review_text = f"[CEO review generation failed: {e}]"

    print()
    for line in review_text.splitlines():
        print(f"  {line}")

    # Save review to file
    OUTPUTS_DIR.mkdir(exist_ok=True)
    review_output = (
        f"WEEKLY REVIEW — {datetime.now().strftime('%A %B %d %Y')}\n"
        f"{'='*60}\n\n"
        f"METRICS:\n"
        f"  Touches: {touches_this_week} | Responses: {responses_this_week} ({response_rate}%)\n"
        f"  New contacts: {new_connections}\n\n"
        f"{review_text}\n\n"
        f"{'='*60}\n"
        f"REFLECTION NOTES:\n"
        + "\n".join(f"Q: {n['q']}\nA: {n['a']}\n" for n in notes)
    )
    with open(REVIEW_PATH, "w", encoding="utf-8") as f:
        f.write(review_output)
    print(f"\n  ✓ Review saved to {REVIEW_PATH}")

    # Save learnings
    learnings["history"].append({
        "week_of":  datetime.now().strftime("%Y-%m-%d"),
        "notes":    notes,
        "patterns": patterns,
        "review":   review_text,
        "metrics": {
            "touches": touches_this_week,
            "responses": responses_this_week,
            "response_rate_pct": response_rate,
            "new_contacts": new_connections,
        },
    })
    learnings["patterns"]      = patterns
    learnings["last_tracker_file"] = str(raw_path)
    if notes:
        learnings["strategy_notes"].append(review_text)
    save_learnings(learnings)
    print(f"  ✓ Learnings saved — Monday will build on this.")
    print(f"\n  📋  Next Monday: just run  python outreach_agent_v3_2.py")
    print(f"      It will read this week's data automatically.\n")

# ─────────────────────────────────────────────────────────────
# MAIN
# ─────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="Semi-Agentic Outreach System v3.2")
    parser.add_argument("--friday", action="store_true",
                        help="Friday review mode — upload updated JSON, get CEO summary")
    parser.add_argument("--status", action="store_true",
                        help="Quick daily status check — no AI calls, instant, free")
    args = parser.parse_args()

    config   = load_config()
    learnings= load_learnings()
    OUTPUTS_DIR.mkdir(exist_ok=True)
    CACHE_DIR.mkdir(parents=True, exist_ok=True)

    # ── STATUS MODE (any day, free, instant) ──────────────────
    if args.status:
        if not Path(TRACKER_PATH).exists():
            print(f"❌  No tracker file found. Export from your tracker and save as {TRACKER_PATH}")
            sys.exit(1)
        tracker  = load_tracker_json(TRACKER_PATH)
        contacts = tracker["contacts"]
        status_check(contacts, learnings)
        return

    # ── FRIDAY MODE ────────────────────────────────────────────
    if args.friday:
        client = anthropic.Anthropic(api_key=config["anthropic_api_key"])
        friday_review(client, config, learnings)
        return

    # ── MONDAY MODE ────────────────────────────────────────────
    banner("🧠  SEMI-AGENTIC OUTREACH v3.2  |  Theta Framework")
    print(f"  {datetime.now().strftime('%A, %B %d %Y  %H:%M')}")
    print(f"  {day_greeting()}")

    # Check if it's actually Monday — warn if not
    if datetime.now().weekday() != 0:
        print(f"\n  ℹ️   Note: Today is {datetime.now().strftime('%A')}. This is designed for Monday.")
        confirm = ask("  Continue anyway?", ["y","n"])
        if confirm == "n":
            print("  Tip: Use --status for a quick daily check.")
            return

    # Load tracker data
    if not Path(TRACKER_PATH).exists():
        print(f"\n  ❌  Tracker not found: {TRACKER_PATH}")
        print("  → Export from your tracker (↓ Export CSV button) → save as data/outreach_import.json")
        # Offer to specify a different path
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

    # Check if this is the Friday file (most recent export)
    export_date = tracker.get("exportDate","")
    if export_date:
        try:
            ed = datetime.fromisoformat(export_date.replace("Z","")).date()
            days_old = (datetime.now().date() - ed).days
            if days_old > 7:
                print(f"  ⚠️   This file is {days_old} days old. Consider re-exporting from your tracker.")
        except: pass

    # Pattern analysis
    print("\n  Analyzing patterns...")
    cost     = CostTracker(budget=config.get("weekly_budget", 2.00))
    client   = anthropic.Anthropic(api_key=config["anthropic_api_key"])
    patterns = analyze_patterns(contacts)
    cost.charge("analysis")
    learnings["patterns"] = patterns
    save_learnings(learnings)

    # Audit
    urgent, warm, stale = audit_tracker(contacts)
    display_audit(urgent, warm, stale, patterns)

    followup_actions = []
    approved_new     = []

    # Follow-ups
    if urgent:
        do_fu = ask(f"\n  Draft replies for {len(urgent)} people who responded?", ["y","n"])
        if do_fu == "y":
            for c in urgent:
                print(f"\n  Drafting reply for {c.get('name','')} @ {c.get('company','')}...")
                draft  = draft_reply(client, c, config, patterns)
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

    # Hunt
    do_hunt = ask("\n  Hunt for NEW target companies this week?", ["y","n"])

    if do_hunt == "y":
        industries    = select_industries_interactively(contacts, patterns)
        country_focus = select_country_focus()
        target_count  = select_target_count()

        existing      = list(set(c.get("company","").strip() for c in contacts if c.get("company")))
        new_companies = hunt_new_companies(client, existing, config,
                                           industries, country_focus, target_count)
        cost.charge("hunt")

        if new_companies:
            print(f"\n  Reviewing {len(new_companies)} companies. Commands: y / 1-5 / e / t / n")
            input("  Press Enter to start... ")

            for i, company in enumerate(new_companies, 1):
                if not cost.can_afford("draft"):
                    print(f"\n  ⚠️  Budget limit (${cost.spent:.2f}). Stopping.")
                    print("  Tip: Increase weekly_budget in config.yaml for more companies.")
                    break

                print(f"\n  [{i}/{len(new_companies)}] Researching {company.get('name','')}...")
                research   = research_company(client, company, cost)
                theta      = research["theta"]
                cs_context = ask_case_study_context(company.get("name",""), company.get("industry",""))

                strategy = get_messaging_strategy(
                    {"jobTitle": company.get("target_roles","").split("|")[0]}, patterns)
                variants = draft_first_touch(client, company, theta, strategy,
                                              config, patterns, cs_context)
                cost.charge("draft", note=company.get("name",""))

                status_val, chosen = approval_loop_new(
                    company, theta, research, variants, cs_context, learnings)

                if status_val == "approved" and chosen:
                    record = build_contact_record(company, theta, chosen, cs_context)
                    approved_new.append(record)
                    print(f"  ✓ Added: {company.get('name','')}")

                print(f"  💰 {cost.summary()}")

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

    banner("✅  DONE")
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
        print(f"  Tue  → Fill Name + Title + send when ready")
    print(f"  Tue–Thu → Log all activity in your tracker as you go")
    print(f"  Any day → python outreach_agent_v3_2.py --status  (quick check)")
    print(f"  Fri  → Export tracker → python outreach_agent_v3_2.py --friday")
    print()

if __name__ == "__main__":
    main()
