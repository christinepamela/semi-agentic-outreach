"""
Semi-Agentic Outreach System v3.1 — Session 3 Upgrade
======================================================
Changes from v3:
- User-selectable target count (5 minimum, default 5)
- Industry-first selection with tracker gap analysis
- No hallucinated names — 3–5 ranked LinkedIn search strings instead
- Case study / article prompts per company (saved to record + used in drafts)
- Expanded Theta signal vocabulary
- "Teach" option (t) in approval loop to correct wrong archetypes
- Pain point / strength detection in research prompt

Monday:  python outreach_agent_v3_1.py
Friday:  python outreach_agent_v3_1.py --friday
"""

import json
import uuid
import sys
import os
import re
import hashlib
import argparse
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

# All industries the agent knows about — used for gap analysis
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

CACHE_TTL = {"background": 90, "news": 7}   # days

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
    channel_stats   = defaultdict(lambda: {"sent":0,"responded":0,"positive":0})
    angle_wins      = defaultdict(int)
    angle_attempts  = defaultdict(int)
    seniority_stats = defaultdict(lambda: {"sent":0,"responded":0,"positive":0,"angles":[]})
    timing          = []
    contact_insights = {}

    for c in contacts:
        logs     = c.get("communicationLog", [])
        title    = c.get("jobTitle", "")
        seniority = classify_seniority(title)
        name     = c.get("name","")

        for log in logs:
            ch        = log.get("channel","LinkedIn")
            response  = log.get("response","").strip()
            sentiment = log.get("sentiment","")
            msg       = log.get("message","").lower()
            date_str  = log.get("date","")
            responded = bool(response) and response.lower() not in ["no response","none"]
            positive  = sentiment in ("Positive","Very positive")

            channel_stats[ch]["sent"] += 1
            if responded: channel_stats[ch]["responded"] += 1
            if positive:  channel_stats[ch]["positive"]  += 1

            seniority_stats[seniority]["sent"] += 1
            if responded: seniority_stats[seniority]["responded"] += 1
            if positive:  seniority_stats[seniority]["positive"]  += 1

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

        if logs:
            all_responded = [l for l in logs if l.get("response","").strip() and
                             l.get("response","").lower() not in ["no response","none"]]
            contact_insights[name] = {
                "company":     c.get("company",""),
                "seniority":   seniority,
                "total_touches": len(logs),
                "total_responses": len(all_responded),
                "best_sentiment": max(
                    (l.get("sentiment","") for l in logs),
                    key=lambda s: {"Very positive":3,"Positive":2,"Neutral":1,"Needs follow-up":1,
                                   "Not interested":0}.get(s,0), default=""
                ),
            }

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

    day_counts  = defaultdict(int)
    hour_counts = defaultdict(int)
    for t in timing:
        day_counts[t["day"]] += 1
        hour_counts[t["hour"] // 3 * 3] += 1
    best_day  = max(day_counts,  key=day_counts.get)  if day_counts  else "Tuesday"
    best_hour = max(hour_counts, key=hour_counts.get) if hour_counts else 9

    top_channel = max(channel_rates, key=lambda k: channel_rates[k]["rate_pct"], default="Email")
    top_angle   = max(angle_rates,   key=lambda k: angle_rates[k]["rate_pct"],   default="research_observation")

    return {
        "analyzed_at":     datetime.now().isoformat(),
        "channel_rates":   channel_rates,
        "angle_rates":     angle_rates,
        "seniority_rates": seniority_rates,
        "top_channel":     top_channel,
        "top_angle":       top_angle,
        "best_day":        best_day,
        "best_hour":       best_hour,
        "best_send_window": f"{best_day} {best_hour:02d}:00–{best_hour+3:02d}:00",
        "contact_insights": contact_insights,
    }

def load_learnings() -> dict:
    path = Path(LEARNINGS_PATH)
    if path.exists():
        try: return json.loads(path.read_text())
        except: pass
    return {"history": [], "patterns": {}, "strategy_notes": [], "archetype_corrections": {}}

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
            "note": "Innovation leads respond to 'builders over storytellers' framing — peer credibility.",
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

def analyze_industry_gaps(contacts: list) -> dict:
    """
    Count contacts per industry in tracker.
    Returns gap analysis + suggestions.
    """
    industry_counts = defaultdict(int)
    for c in contacts:
        ind = c.get("industryFocus", "").strip()
        if ind:
            industry_counts[ind] += 1

    # Bucket tracker industries to standard names
    covered = {k: v for k, v in industry_counts.items() if v > 0}
    total   = sum(covered.values())

    # Find which of our standard industries are underrepresented
    gaps = []
    for ind in ALL_INDUSTRIES:
        # fuzzy match: check if any tracker industry contains this keyword
        covered_count = sum(v for k, v in covered.items()
                            if any(w.lower() in k.lower() for w in ind.split(" / ")[0].split()))
        gaps.append({"industry": ind, "count": covered_count})

    gaps_sorted = sorted(gaps, key=lambda x: x["count"])
    underrepresented = [g["industry"] for g in gaps_sorted if g["count"] == 0][:8]
    present          = [g["industry"] for g in gaps_sorted if g["count"] > 0]

    return {
        "covered":           covered,
        "total_contacts":    total,
        "underrepresented":  underrepresented,
        "present_industries": present,
    }


def select_industries_interactively(contacts: list) -> list:
    """
    Show industry gap analysis and let Pam choose industries for this week's hunt.
    Returns list of selected industry strings.
    """
    section("INDUSTRY SELECTION")
    gaps = analyze_industry_gaps(contacts)

    print("\n  Your tracker by industry (existing contacts):")
    for ind, cnt in sorted(gaps["covered"].items(), key=lambda x: -x[1])[:10]:
        bar = "█" * min(cnt, 20)
        print(f"    {ind:40s} {bar} {cnt}")

    print(f"\n  Industries with NO contacts yet (fresh territory):")
    for i, ind in enumerate(gaps["underrepresented"], 1):
        print(f"    {i:2}. {ind}")

    print()
    choice = ask("  Industry selection mode?",
                 ["recommended", "pick", "random"])

    if choice == "recommended":
        # Suggest 3 underrepresented + 1 from existing (deepen)
        suggested = gaps["underrepresented"][:3]
        if gaps["present_industries"]:
            suggested.append(gaps["present_industries"][0])  # deepen one existing
        print(f"\n  Suggested mix: {', '.join(suggested)}")
        confirm = ask("  Use this selection?", ["y", "n"])
        if confirm == "y":
            return suggested

    if choice == "pick" or (choice == "recommended" and confirm == "n"):
        print(f"\n  All available industries:")
        for i, ind in enumerate(ALL_INDUSTRIES, 1):
            print(f"    {i:2}. {ind}")
        print()
        raw = input("  Enter numbers separated by commas (e.g. 3,7,12): ").strip()
        try:
            indices = [int(x.strip()) - 1 for x in raw.split(",") if x.strip().isdigit()]
            selected = [ALL_INDUSTRIES[i] for i in indices if 0 <= i < len(ALL_INDUSTRIES)]
            if selected:
                print(f"  Selected: {', '.join(selected)}")
                return selected
        except Exception:
            pass
        print("  Invalid selection, using random.")

    # Random
    import random
    selected = random.sample(ALL_INDUSTRIES, min(3, len(ALL_INDUSTRIES)))
    print(f"  Random selection: {', '.join(selected)}")
    return selected


def select_target_count() -> int:
    """Ask how many new companies to hunt this week."""
    print()
    raw = input("  How many new target companies this week? [5 minimum, default 5]: ").strip()
    if raw.isdigit():
        n = int(raw)
        if n >= 5:
            print(f"  Hunting {n} companies.")
            return n
        else:
            print(f"  Minimum is 5. Using 5.")
            return 5
    print("  Using default: 5.")
    return 5


def select_country_focus() -> str:
    """Ask for optional country/region filter."""
    print()
    raw = input("  Focus on specific country/region? (e.g. 'Germany', 'Southeast Asia') or press Enter to skip: ").strip()
    return raw if raw else ""

# ─────────────────────────────────────────────────────────────
# THETA FRAMEWORK — EXPANDED SCORING
# ─────────────────────────────────────────────────────────────

CORE_SIGNALS   = [
    "operational excellence","efficiency","optimization","cost reduction",
    "process improvement","quality","reliability","scale","margin","profitability",
    "customer retention","traditional","incumbent","legacy","core business",
    "existing products","sustaining","agile","lean","continuous improvement",
    "cost discipline","productivity","operational kpi","workforce efficiency",
    "supply chain optimization","erp","sap","six sigma","kaizen",
]
EDGE_SIGNALS   = [
    "pilot","experiment","venture","new business","adjacent","digital transformation",
    "platform","ecosystem","partnership","spin-off","incubator","accelerator",
    "next generation","growth initiative","s-curve","new market","emerging","startup",
    "product launch","beta","new revenue","digital twin","xcelerator","open innovation",
    "business model innovation","new venture","corporate venture","edge","scale-up",
    "technology partnership","innovation unit","innovation hub","breakthrough program",
    "digital business","data platform","ai transformation","cloud transformation",
]
BEYOND_SIGNALS = [
    "moonshot","quantum","deep tech","10x","breakthrough","research lab",
    "fundamental research","2030","2035","2040","future of","reinvent","disruption",
    "frontier","autonomous","fusion","biotech","nanotechnology","ai research",
    "basic research","horizon 3","beyond","long-term bet","venture studio",
    "exponential","synthetic biology","space","climate tech","net zero 2040",
    "advanced materials","next-generation","horizon scanning",
]
THEATER_SIGNALS= [
    "innovation lab","innovation hub","digital lab","center of excellence",
    "hackathon","ideation","prototype","proof of concept","poc",
    "we're exploring","looking into","vision for","roadmap for 2030",
    "innovation theater","announce","showcase","award",
    "innovation day","pitch competition","startup program","innovation challenge",
    "lab without output","innovation report","innovation index","recognition",
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

    if override_archetype:
        archetype = override_archetype
        # Re-derive pain + move from override
        archetype_map = {
            "Innovation Theater": (
                "Labs and pilots don't ship to market — strong on announcement, weak on scaling",
                "Move 4: Builders Over Storytellers — measure what ships, not what's launched",
                f"I noticed {company_name} has impressive innovation programs. How do you measure which pilots are actually on a path to market?"
            ),
            "Stuck in Core": (
                "Over-indexed on Core optimization, missing Edge bets — disruption risk growing",
                "Move 2: Rewire the System — create protected innovation lanes",
                f"As {industry} faces increasing disruption, how is {company_name} building its next S-curve without destabilizing the core?"
            ),
            "Edge-Active, No Beyond": (
                "Strong Edge activity but no long-horizon vision — risk of short-termism",
                "Move 3: Measure What Matters — stage-appropriate KPIs per zone",
                f"{company_name}'s Edge work looks strong. How are you planting seeds for where the business needs to be in 2032 and beyond?"
            ),
            "Balanced Transformer": (
                "Core/Edge tension — governance and metrics likely misaligned across zones",
                "Move 1: Deep Audit — map complexity, trace where initiatives stall",
                f"Curious how {company_name} governs the tension between optimizing today and investing in what comes next."
            ),
            "Frontier Builder": (
                "Beyond bets lack Core/Edge bridge — risk of stranded moonshots",
                "Move 2: Rewire — connect Beyond bets to Edge commercialization path",
                f"{company_name}'s frontier work is impressive. How are you building the bridge from R&D to scalable Edge business?"
            ),
        }
        pain, move, angle = archetype_map.get(override_archetype, (
            "Innovation portfolio not yet systematically managed",
            "Move 1: Deep Audit — start by mapping where initiatives live",
            f"How is {company_name} thinking about managing its innovation portfolio as the business scales?"
        ))
    else:
        if theater >= 3:
            archetype = "Innovation Theater"
            pain      = "Labs and pilots don't ship to market — strong on announcement, weak on scaling"
            move      = "Move 4: Builders Over Storytellers — measure what ships, not what's launched"
            angle     = f"I noticed {company_name} has impressive innovation programs. How do you measure which pilots are actually on a path to market?"
        elif core >= 6 and edge < 3:
            archetype = "Stuck in Core"
            pain      = "Over-indexed on Core optimization, missing Edge bets — disruption risk growing"
            move      = "Move 2: Rewire the System — create protected innovation lanes"
            angle     = f"As {industry} faces increasing disruption, how is {company_name} building its next S-curve without destabilizing the core?"
        elif edge >= 5 and beyond < 2:
            archetype = "Edge-Active, No Beyond"
            pain      = "Strong Edge activity but no long-horizon vision — risk of short-termism"
            move      = "Move 3: Measure What Matters — stage-appropriate KPIs per zone"
            angle     = f"{company_name}'s Edge work looks strong. How are you planting seeds for where the business needs to be in 2032 and beyond?"
        elif edge >= 4 and core >= 5:
            archetype = "Balanced Transformer"
            pain      = "Core/Edge tension — governance and metrics likely misaligned across zones"
            move      = "Move 1: Deep Audit — map complexity, trace where initiatives stall"
            angle     = f"Curious how {company_name} governs the tension between optimizing today and investing in what comes next."
        elif beyond >= 4:
            archetype = "Frontier Builder"
            pain      = "Beyond bets lack Core/Edge bridge — risk of stranded moonshots"
            move      = "Move 2: Rewire — connect Beyond bets to Edge commercialization path"
            angle     = f"{company_name}'s frontier work is impressive. How are you building the bridge from R&D to scalable Edge business?"
        else:
            archetype = "Early Explorer"
            pain      = "Innovation portfolio not yet systematically managed"
            move      = "Move 1: Deep Audit — start by mapping where initiatives live"
            angle     = f"How is {company_name} thinking about managing its innovation portfolio as the business scales?"

    gaps = []
    if edge < 3: gaps.append("No visible Edge / next S-curve work")
    if beyond < 2: gaps.append("No long-horizon Beyond bets")
    if theater >= 3: gaps.append("Innovation theater risk: labs don't ship")
    if core >= 7 and edge < 3: gaps.append("Core-heavy: disruption vulnerability")
    if not gaps: gaps.append("Portfolio reasonably active — governance clarity is the opportunity")

    zone_emoji = {"core":"🟩","edge":"🟨","beyond":"🟥"}
    primary = max({"core":core,"edge":edge,"beyond":beyond}, key=lambda k:{"core":core,"edge":edge,"beyond":beyond}[k])

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

# ─────────────────────────────────────────────────────────────
# STEP 1 — AUDIT
# ─────────────────────────────────────────────────────────────

def audit_tracker(contacts: list) -> tuple:
    urgent, warm, stale = [], [], []
    now = datetime.now(timezone.utc)

    for c in contacts:
        logs     = c.get("communicationLog",[])
        status   = c.get("connectionStatus","")

        real_response = lambda l: (l.get("response","") or "").strip() and \
                                   (l.get("response","") or "").lower() not in ["no response","none"]

        if len(logs) == 1 and real_response(logs[0]):
            urgent.append({**c, "_their_response": logs[0]["response"],
                           "_our_message": logs[0].get("message",""),
                           "_channel": logs[0].get("channel","LinkedIn"),
                           "_date": logs[0].get("date","")})

        elif status == "Connected" and not logs:
            warm.append(c)

        elif logs and not any(real_response(l) for l in logs):
            last_date = logs[-1].get("date","")
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

    cr = patterns.get("channel_rates",{})
    if cr:
        print("\n  📊 Your historical response rates:")
        for ch, s in sorted(cr.items(), key=lambda x: -x[1]["rate_pct"]):
            bar = "█" * int(s["rate_pct"]/10) + "░" * (10 - int(s["rate_pct"]/10))
            print(f"     {ch:12s} [{bar}] {s['rate_pct']:.0f}%  ({s['responded']}/{s['sent']})")
        print(f"  🎯 Best angle: {patterns.get('top_angle','—')}  |  Best time: {patterns.get('best_send_window','—')}")

    print(f"\n  🔴  URGENT — Responded, no reply yet: {len(urgent)}")
    for i, c in enumerate(urgent, 1):
        resp = (c.get("_their_response","") or "")[:100]
        print(f"\n  {i}. {c['name']} @ {c['company']} [{c['priority']}]")
        if c.get("jobTitle"): print(f"     {c['jobTitle']}")
        print(f"     Their reply: \"{resp}{'...' if len(resp)==100 else ''}\"")

    print(f"\n  🟡  WARM — Connected, never messaged: {len(warm)}")
    for c in warm:
        print(f"     • {c['name']} @ {c['company']} [{c['priority']}]  {c.get('jobTitle','')}")

    print(f"\n  ⬜  STALE — {len(stale)} contacts messaged with no response (10+ days)")
    for c in sorted(stale, key=lambda x: x.get("_days_ago",0), reverse=True)[:5]:
        print(f"     • {c['name']} @ {c['company']} [{c['priority']}]  ({c.get('_days_ago',0)}d ago, {c.get('_touches',1)} touch)")

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
    user_name = config.get("user_short_name","Pam")

    prompt = f"""You are drafting a reply for {config.get('user_name','Christine Pamela')}, an innovation consultant (Theta Framework).

CONTACT: {name} — {title} at {company}
SENIORITY TYPE: {strategy['seniority']}
MESSAGING STYLE: {strategy['style']} (based on what has worked with this seniority level)

PAM'S ORIGINAL MESSAGE:
{our_msg}

THEIR RESPONSE:
{their_resp}

Draft 3 reply variants. Each should:
- Acknowledge their specific response genuinely
- Move toward a 20-minute conversation
- Be warm, not salesy — {name} already responded positively
- Max 4 sentences
- Sign off as "{user_name}"
- Variant 3 should suggest a specific time slot or Calendly-style soft ask

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

# ─────────────────────────────────────────────────────────────
# STEP 3 — HUNT NEW COMPANIES
# ─────────────────────────────────────────────────────────────

def hunt_new_companies(client, existing_companies: list, config: dict,
                       industries: list, country_focus: str, target_count: int) -> list:
    section("STEP 2 — HUNTING NEW COMPANIES")
    industry_str = ", ".join(industries) if industries else "any major industry"
    country_str  = f"Prefer companies headquartered in or with major operations in: {country_focus}." if country_focus else "Global — any geography."
    print(f"  Industries: {industry_str}")
    print(f"  Geography: {country_str if country_focus else 'Global'}")
    print(f"  Target count: {target_count}")
    print(f"  Asking Claude to identify {target_count} new enterprise targets...")

    existing_str = ", ".join(sorted(set(existing_companies)))

    prompt = f"""You are a business intelligence researcher for Christine Pamela, an innovation consultant (Theta Framework — Core/Edge/Beyond zone methodology for large enterprises).

Ideal clients: large global enterprises (2000+ employees) where there is visible tension between:
- Optimizing the core business (efficiency, margins)
- Building next-generation growth (new business models, platforms, innovation units)

INDUSTRIES TO FOCUS ON THIS WEEK: {industry_str}
GEOGRAPHY: {country_str if country_focus else "Global — any geography."}

COMPANIES ALREADY IN PAM'S TRACKER (do NOT suggest):
{existing_str}

TASK: Identify exactly {target_count} companies from the specified industries.

For each company, identify the MOST RELEVANT contact for Theta Framework conversations.
This should be a senior person responsible for breakthrough innovation, digital transformation,
portfolio governance, or corporate strategy — NOT general IT or operational roles.
Ideal titles: Chief Innovation Officer, Chief Strategy Officer, VP Digital Transformation,
Head of Corporate Venture, Chief Technology Officer, Head of Innovation Portfolio,
VP New Business Development.

IMPORTANT: Do NOT invent specific person names. Instead, provide ranked LinkedIn search strings
that Pam can use to find the right person in 2 minutes. Be realistic about who exists.

For each, output EXACTLY:

---COMPANY---
NAME: [Company]
INDUSTRY: [Industry]
COUNTRY: [HQ country]
SIZE: [Approx employees]
WHY_THETA_FIT: [2 sentences: what is their specific innovation tension]
THETA_ARCHETYPE: [Stuck in Core / Innovation Theater / Edge-Active No Beyond / Balanced Transformer / Frontier Builder]
TARGET_ROLES: [3-5 ranked LinkedIn search strings, most relevant first, format: "CompanyName" "Role Title 1" | "CompanyName" "Role Title 2" | etc]
RECENT_SIGNAL: [One specific recent initiative, announcement, or challenge — be concrete]
STRENGTHS: [1 sentence: what they are genuinely doing well on innovation]
PAIN_POINTS: [1-2 sentences: where their innovation approach has visible cracks]"""

    try:
        r = client.messages.create(model="claude-haiku-4-5-20251001", max_tokens=4000,
                                    messages=[{"role":"user","content":prompt}])
        raw = r.content[0].text.strip()
        companies = []
        for block in raw.split("---COMPANY---")[1:]:
            c = {}
            for line in block.strip().splitlines():
                for field in ["NAME","INDUSTRY","COUNTRY","SIZE","WHY_THETA_FIT",
                              "THETA_ARCHETYPE","TARGET_ROLES","RECENT_SIGNAL",
                              "STRENGTHS","PAIN_POINTS"]:
                    if line.startswith(f"{field}:"):
                        c[field.lower()] = line[len(field)+1:].strip()
            if c.get("name"):
                companies.append(c)
        print(f"  ✓ {len(companies)} target companies identified")
        return companies[:target_count]
    except Exception as e:
        print(f"  ✗ Hunt failed: {e}")
        return []

# ─────────────────────────────────────────────────────────────
# STEP 4 — RESEARCH + THETA PER NEW COMPANY
# ─────────────────────────────────────────────────────────────

def research_company(client, company: dict, cost: CostTracker) -> dict:
    name     = company.get("name","")
    industry = company.get("industry","")
    cached   = load_cache(name)

    if "background" in cached:
        background = cached["background"]
        cost.charge("cached_research", note=f"{name} (bg cached)")
    else:
        prompt = f"""Research {name} ({industry}). Provide:

1. Core business and revenue model (2-3 sentences)
2. Known breakthrough innovation programs, R&D, digital transformation efforts (be specific — named programs, investments, ventures)
3. Innovation maturity signals — do they actually ship or mostly announce? Any evidence of labs-to-market success?
4. Key STRENGTHS in their innovation approach
5. Key PAIN POINTS or gaps — where is their innovation portfolio weak, slow, or misaligned?
6. Strategic priorities 2024–2026

Keep under 300 words. Be specific and honest — include both strengths and weaknesses."""
        try:
            r = client.messages.create(model="claude-haiku-4-5-20251001", max_tokens=500,
                                        messages=[{"role":"user","content":prompt}])
            background = r.content[0].text.strip()
            save_cache(name, {"background": background})
            cost.charge("research", note=f"{name} (fresh)")
        except Exception as e:
            background = f"Research unavailable: {e}"
            cost.charge("cached_research", note=f"{name} (error fallback)")

    combined = f"{company.get('why_theta_fit','')} {company.get('recent_signal','')} {company.get('strengths','')} {company.get('pain_points','')} {background}"
    theta    = theta_assess(combined, name, industry)
    cost.charge("theta", note=name)

    return {"background": background, "theta": theta, "was_cached": "background" in cached}

# ─────────────────────────────────────────────────────────────
# STEP 5 — CASE STUDY / ARTICLE CHECK
# ─────────────────────────────────────────────────────────────

def ask_case_study_context(company_name: str, industry: str) -> dict:
    """
    Ask Pam if she has a case study or article relevant to this company.
    Returns context dict that enriches the message drafts.
    """
    print(f"\n  ── YOUR CONTENT FOR {company_name.upper()} ──\n")

    context = {
        "has_case_study": False,
        "building_case_study": False,
        "case_study_note": "",
        "articles": [],
    }

    # Case study
    has_cs = ask(f"  Do you have a case study relevant to {company_name} or {industry}?", ["y", "n"])
    if has_cs == "y":
        context["has_case_study"] = True
        cs_note = input("  Brief description (company/topic of the case study): ").strip()
        context["case_study_note"] = cs_note
    else:
        building = ask(f"  Are you building a case study on {company_name} or {industry}?", ["y", "n"])
        if building == "y":
            context["building_case_study"] = True

    # Articles
    has_articles = ask(f"  Any articles you've written relevant to {company_name} or {industry}?", ["y", "n"])
    if has_articles == "y":
        print("  Paste article URLs one per line (press Enter twice to finish):")
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

    # Build content hook
    content_hook = ""
    if cs_context.get("has_case_study") and cs_context.get("case_study_note"):
        content_hook = f"Pam has a relevant case study on: {cs_context['case_study_note']}. One variant should naturally reference this."
    elif cs_context.get("building_case_study"):
        content_hook = f"Pam is building a case study on {company.get('industry','')}. One variant can hint at this research angle."
    if cs_context.get("articles"):
        content_hook += f" Pam has written articles: {', '.join(cs_context['articles'][:2])}. One variant should reference sharing the article as a value-add."

    # Build LinkedIn search hint for personalisation
    target_roles_raw = company.get("target_roles", "")
    search_strings = [s.strip() for s in target_roles_raw.split("|") if s.strip()]
    first_search = search_strings[0] if search_strings else f'"{company.get("name","")}" "Chief Innovation Officer"'

    prompt = f"""Draft outreach messages for {config.get('user_name','Christine Pamela')}, an innovation consultant (Theta Framework).

TARGET COMPANY: {company.get('name','')} | {company.get('industry','')} | {company.get('country','')}
TARGET ROLE (most likely): {first_search}
SENIORITY TYPE: {seniority}

COMPANY STRENGTHS: {company.get('strengths','')}
COMPANY PAIN POINTS: {company.get('pain_points','')}

THETA ASSESSMENT:
- Archetype: {theta.get('archetype','')}
- Pain point: {theta.get('pain_point','')}
- Recommended move: {theta.get('recommended_move','')}
- Suggested angle: {theta.get('messaging_angle','')}

RECENT SIGNAL: {company.get('recent_signal','')}
WHY PAM: {company.get('why_theta_fit','')}

STRATEGY NOTE:
- Best performing angle: {top_angle}
- Style for {seniority}: {style_note}
- Best send time: {best_send}

PAM'S CONTENT: {content_hook if content_hook else "No specific case study or article to reference this week."}

RULES:
1. First touch ONLY — no framework pitching, no selling
2. Lead with ONE genuine observation, strength acknowledgment, OR pain point question
3. LinkedIn variants: max 4 sentences. Email: max 6 sentences + subject line
4. Each variant meaningfully different in angle (observation / pain point / peer / content share)
5. If Pam has an article or case study, one variant should mention it naturally as a share, not a pitch
6. Sign off as "{user_name}"
7. Do NOT use the target person's name (we don't have a confirmed name)

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

VALID_ARCHETYPES = [
    "Stuck in Core", "Innovation Theater", "Edge-Active, No Beyond",
    "Balanced Transformer", "Frontier Builder", "Early Explorer"
]

def approval_loop_new(company: dict, theta: dict, research: dict,
                      variants: list, cs_context: dict, learnings: dict) -> tuple:
    section(f"NEW COMPANY: {company['name']}")
    cached_tag = " (cached)" if research.get("was_cached") else ""

    print(f"  {company.get('industry','')} | {company.get('country','')} | ~{company.get('size','?')} employees")

    # LinkedIn search strings
    target_roles_raw = company.get("target_roles", "")
    search_strings = [s.strip() for s in target_roles_raw.split("|") if s.strip()]
    print(f"\n  🔍  LinkedIn searches (ranked, most relevant first):")
    for i, s in enumerate(search_strings[:5], 1):
        print(f"     {i}. {s}")

    # Company intelligence
    print(f"\n  💪  Strengths:    {company.get('strengths','—')}")
    print(f"  ⚠️   Pain points:  {company.get('pain_points','—')}")
    print(f"\n  Theta Archetype: {theta.get('archetype','')}  |  {theta.get('zone_summary','')}")
    print(f"  Zone scores: Core={theta['zone_distribution']['core']} Edge={theta['zone_distribution']['edge']} Beyond={theta['zone_distribution']['beyond']}")
    print(f"  Pain point: {theta.get('pain_point','')}")
    print(f"  Move: {theta.get('recommended_move','')}")
    print(f"  Research{cached_tag}: {research.get('background','')[:250]}...")
    print(f"  Recent signal: {company.get('recent_signal','')}")

    # Case study context
    if cs_context.get("has_case_study"):
        print(f"\n  📎  Case study on file: {cs_context.get('case_study_note','yes')}")
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
    print("  Commands: y=approve v1 / 1-5=pick variant / e=edit / t=teach correct archetype / n=skip")
    choice = input("  Decision? ").strip().lower()

    if choice == "n":
        return None, None

    # Teach mode — Pam corrects the archetype
    if choice == "t":
        print(f"\n  Current archetype: {theta.get('archetype','')}")
        print(f"  Options:")
        for i, a in enumerate(VALID_ARCHETYPES, 1):
            print(f"    {i}. {a}")
        raw = input("  Enter number of correct archetype: ").strip()
        if raw.isdigit() and 1 <= int(raw) <= len(VALID_ARCHETYPES):
            correct = VALID_ARCHETYPES[int(raw)-1]
            # Save correction to learnings
            corrections = learnings.get("archetype_corrections", {})
            corrections[company["name"]] = {
                "was": theta.get("archetype"),
                "corrected_to": correct,
                "date": datetime.now().isoformat(),
            }
            learnings["archetype_corrections"] = corrections
            save_learnings(learnings)
            # Re-score with override
            from copy import deepcopy
            theta_corrected = theta_assess(
                research.get("background",""), company["name"],
                company.get("industry",""), override_archetype=correct
            )
            print(f"  ✓ Archetype corrected to: {correct}")
            print(f"  Updated pain point: {theta_corrected.get('pain_point','')}")
            # Re-enter approval with corrected theta — for simplicity, just re-show key fields
            theta.update(theta_corrected)
            print("  Re-enter decision with corrected archetype:")
            print("  Commands: y=approve v1 / 1-5=pick variant / e=edit / n=skip")
            choice = input("  Decision? ").strip().lower()
        else:
            print("  Invalid. Keeping original archetype.")
            choice = input("  Decision? [y/1-5/e/n]: ").strip().lower()

    if choice == "n":
        return None, None

    if choice in ["1","2","3","4","5"]:
        idx = int(choice) - 1
        chosen = variants[idx] if idx < len(variants) else variants[0]
        return "approved", chosen

    if choice == "e":
        print("  Paste your message (Enter twice to finish):")
        lines = []
        while True:
            line = input()
            if line == "" and lines and lines[-1] == "":
                break
            lines.append(line)
        body = "\n".join(lines[:-1]).strip()
        ch_raw = input("  Channel? [LinkedIn / Email]: ").strip() or "LinkedIn"
        subj = ""
        if ch_raw.lower() == "email":
            subj = input("  Subject line: ").strip()
        return "approved", {"variant":0,"channel":ch_raw,"tone":"custom","subject":subj,"body":body}

    # y or anything else = approve v1
    return "approved", variants[0] if variants else None


def approval_loop_followup(contact: dict, draft_text: str):
    section(f"FOLLOW-UP: {contact['name']} @ {contact['company']}")
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
# STEP 8 — BUILD OUTPUTS
# ─────────────────────────────────────────────────────────────

def build_contact_record(company: dict, theta: dict, chosen_variant: dict,
                          cs_context: dict) -> dict:
    now = datetime.now(timezone.utc).isoformat().replace("+00:00","Z")

    # Build LinkedIn search block for notes
    target_roles_raw = company.get("target_roles", "")
    search_strings = [s.strip() for s in target_roles_raw.split("|") if s.strip()]
    linkedin_block = "\n".join(f"  LinkedIn search {i}: {s}"
                                for i, s in enumerate(search_strings[:5], 1))

    # Content notes
    content_note = ""
    if cs_context.get("has_case_study"):
        content_note = f"\nCase study on file: {cs_context.get('case_study_note','yes')}"
    if cs_context.get("building_case_study"):
        content_note += f"\nBuilding case study for: {company.get('industry','')}"
    if cs_context.get("articles"):
        content_note += f"\nArticles: {', '.join(cs_context['articles'])}"

    return {
        "company":          company.get("name","").strip(),
        "name":             "",  # No hallucinated name — to be filled after LinkedIn search
        "jobTitle":         "",  # To be filled after LinkedIn search
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
        "notes": (
            f"Theta archetype: {theta.get('archetype','')}\n"
            f"Zone: Core={theta['zone_distribution']['core']} Edge={theta['zone_distribution']['edge']} Beyond={theta['zone_distribution']['beyond']}\n"
            f"Pain point: {theta.get('pain_point','')}\n"
            f"Strengths: {company.get('strengths','')}\n"
            f"Recommended move: {theta.get('recommended_move','')}\n"
            f"Recent signal: {company.get('recent_signal','')}\n"
            f"\n── FIND YOUR CONTACT ──\n"
            f"{linkedin_block}"
            f"{content_note}"
        ),
        "id":               str(uuid.uuid4()),
        "communicationLog": [],
        "lastMessage":      "",
        "createdAt":        now,
        "draft_message": {
            "channel": chosen_variant.get("channel","LinkedIn"),
            "subject": chosen_variant.get("subject",""),
            "body":    chosen_variant.get("body",""),
            "tone":    chosen_variant.get("tone",""),
            "note":    "Name/title blank — fill in after LinkedIn search. Message drafted without name.",
        }
    }

def generate_tuesday_tasks(approved_new: list, followup_actions: list,
                            warm: list, patterns: dict) -> dict:
    tasks = []
    best_send = patterns.get("best_send_window","Tuesday 09:00–12:00")

    for f in followup_actions:
        tasks.append({
            "priority": "1-URGENT",
            "task": f"Reply to {f['name']} @ {f['company']}",
            "channel": f.get("channel","LinkedIn"),
            "action": "Send reply",
            "message": f.get("message",""),
            "send_time": "First thing — they're waiting",
            "goal_link": "Weekly goal: convert warm conversations to meetings",
        })

    for c in approved_new:
        draft = c.get("draft_message",{})
        notes = c.get("notes","")
        linkedin_searches = [l.strip() for l in notes.splitlines() if "LinkedIn search" in l]
        tasks.append({
            "priority": "2-NEW",
            "task": f"First touch: [Find contact] @ {c['company']}",
            "channel": draft.get("channel","LinkedIn"),
            "action": "1. Find contact on LinkedIn using searches below → 2. Connect → 3. Send message",
            "linkedin_searches": linkedin_searches,
            "message": draft.get("body",""),
            "subject": draft.get("subject",""),
            "send_time": best_send,
            "goal_link": "Weekly goal: add new conversations",
        })

    for c in warm[:3]:
        tasks.append({
            "priority": "3-WARM",
            "task": f"First message: {c['name']} @ {c['company']} (already connected)",
            "channel": c.get("connectionMethod","LinkedIn"),
            "action": "Send first message — they accepted your connection",
            "message": "[Draft using Theta angle from their notes]",
            "send_time": best_send,
            "goal_link": "Weekly goal: activate warm connections",
        })

    return {
        "generated_at": datetime.now().isoformat(),
        "week_of":      datetime.now().strftime("%Y-%m-%d"),
        "best_send_window": best_send,
        "total_tasks":  len(tasks),
        "tasks":        tasks,
        "goal_hierarchy": {
            "this_week":  "All urgent replies + new first touches",
            "this_month": "5 new conversations started",
            "quarterly":  "2 Theta consulting engagements opened",
        }
    }

def save_all(approved_new, followup_actions, warm, patterns, cost):
    OUTPUTS_DIR.mkdir(exist_ok=True)

    if approved_new:
        export = {
            "version":    "1.1",
            "exportDate": datetime.now(timezone.utc).isoformat().replace("+00:00","Z"),
            "source":     "semi-agentic-outreach-v3.1",
            "note":       "Import into tracker. Name/jobTitle fields blank — fill after LinkedIn search. draft_message = approved first-touch message.",
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
# FRIDAY REFLECTION MODE
# ─────────────────────────────────────────────────────────────

def friday_reflection(client, contacts: list, learnings: dict, config: dict):
    banner("🪞  FRIDAY REFLECTION")
    print("  Let's capture what worked this week so the agent improves.\n")

    patterns = analyze_patterns(contacts)

    print("  ── THIS WEEK'S PATTERNS ──\n")
    cr = patterns.get("channel_rates",{})
    for ch, s in sorted(cr.items(), key=lambda x:-x[1]["rate_pct"]):
        bar = "█" * int(s["rate_pct"]/10) + "░"*(10-int(s["rate_pct"]/10))
        print(f"  {ch:12s} [{bar}] {s['rate_pct']:.0f}%")

    ar = patterns.get("angle_rates",{})
    print("\n  ── ANGLE PERFORMANCE ──\n")
    for angle, s in sorted(ar.items(), key=lambda x:-x[1]["rate_pct"]):
        if s["attempts"] > 0:
            print(f"  {angle:28s}  {s['wins']}/{s['attempts']} responded  ({s['rate_pct']:.0f}%)")

    # Show any archetype corrections made this week
    corrections = learnings.get("archetype_corrections", {})
    if corrections:
        print(f"\n  ── ARCHETYPE CORRECTIONS YOU MADE ──\n")
        for company, corr in corrections.items():
            print(f"  {company}: {corr['was']} → {corr['corrected_to']}")
        print("\n  These corrections are improving Theta's accuracy over time.")

    print("\n  ── YOUR REFLECTION ──\n")
    print("  Answer these (or press Enter to skip each):\n")

    notes = []
    questions = [
        "What message angle got the best reaction this week?",
        "Who surprised you with a response? What did you say to them?",
        "What industry or role type felt most receptive?",
        "Did your case study or article references land well?",
        "Anything you want to try differently next Monday?",
    ]
    for q in questions:
        print(f"  Q: {q}")
        ans = input("  A: ").strip()
        if ans:
            notes.append({"question": q, "answer": ans, "date": datetime.now().isoformat()})
        print()

    if notes:
        notes_text = "\n".join(f"Q: {n['question']}\nA: {n['answer']}" for n in notes)
        prompt = f"""Pam is an innovation consultant running weekly outreach using the Theta Framework.

Her reflection notes from this week:
{notes_text}

Her channel response rates: {json.dumps(patterns.get('channel_rates',{}), indent=2)}
Top performing angle: {patterns.get('top_angle','')}

Synthesize into 3 concrete strategy recommendations for next Monday's outreach run.
Be specific, actionable, and brief. Label them RECOMMENDATION 1, 2, 3."""

        try:
            r = client.messages.create(model="claude-haiku-4-5-20251001", max_tokens=500,
                                        messages=[{"role":"user","content":prompt}])
            strategy_update = r.content[0].text.strip()
        except:
            strategy_update = "Continue with top-performing angles."

        print("\n  ── STRATEGY RECOMMENDATIONS FOR NEXT MONDAY ──\n")
        for line in strategy_update.splitlines():
            print(f"  {line}")

        learnings["history"].append({
            "week_of":  datetime.now().strftime("%Y-%m-%d"),
            "notes":    notes,
            "patterns": patterns,
            "strategy": strategy_update,
        })
        learnings["patterns"] = patterns
        learnings["strategy_notes"].append(strategy_update)
        save_learnings(learnings)
        print("\n  ✓ Learnings saved to data/learnings.json")
    else:
        print("  No notes captured. Saving pattern data only.")
        learnings["patterns"] = patterns
        save_learnings(learnings)

# ─────────────────────────────────────────────────────────────
# MAIN
# ─────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--friday", action="store_true", help="Run Friday reflection mode")
    args = parser.parse_args()

    config = load_config()
    client = anthropic.Anthropic(api_key=config["anthropic_api_key"])
    cost   = CostTracker(budget=config.get("weekly_budget", 2.00))
    OUTPUTS_DIR.mkdir(exist_ok=True)
    CACHE_DIR.mkdir(parents=True, exist_ok=True)

    if not Path(TRACKER_PATH).exists():
        print(f"❌  Tracker not found: {TRACKER_PATH}")
        sys.exit(1)
    with open(TRACKER_PATH, encoding="utf-8") as f:
        tracker = json.load(f)
    contacts = tracker.get("contacts", [])

    learnings = load_learnings()

    # ── FRIDAY MODE ─────────────────────────────────
    if args.friday:
        friday_reflection(client, contacts, learnings, config)
        return

    # ── MONDAY MODE ─────────────────────────────────
    banner("🧠  SEMI-AGENTIC OUTREACH v3.1  |  Theta Framework")
    print(f"  {datetime.now().strftime('%A, %B %d %Y %H:%M')}")
    print(f"  {len(contacts)} contacts loaded")

    print("\n  Analyzing all historical touchpoints...")
    patterns = analyze_patterns(contacts)
    cost.charge("analysis")
    learnings["patterns"] = patterns
    save_learnings(learnings)

    urgent, warm, stale = audit_tracker(contacts)
    display_audit(urgent, warm, stale, patterns)

    followup_actions = []
    approved_new     = []

    # Follow-ups
    if urgent:
        do_fu = ask(f"\n  Draft replies for {len(urgent)} people who responded to you?", ["y","n"])
        if do_fu == "y":
            for c in urgent:
                print(f"\n  Drafting reply for {c['name']} @ {c['company']}...")
                draft = draft_reply(client, c, config, patterns)
                cost.charge("followup_draft", note=c["name"])
                chosen = approval_loop_followup(c, draft)
                if chosen:
                    followup_actions.append({
                        "name":    c["name"],
                        "company": c["company"],
                        "channel": c.get("_channel","LinkedIn"),
                        "action":  "Send reply",
                        "message": chosen,
                        "priority": c.get("priority",""),
                    })
                    print(f"  ✓ Approved")

    # Hunt new companies
    do_hunt = ask("\n  Hunt for NEW target companies?", ["y","n"])

    if do_hunt == "y":
        # Step 1: Industry selection
        industries = select_industries_interactively(contacts)

        # Step 2: Country focus
        country_focus = select_country_focus()

        # Step 3: How many
        target_count = select_target_count()

        existing = list(set(c.get("company","").strip() for c in contacts if c.get("company")))
        new_companies = hunt_new_companies(client, existing, config,
                                           industries, country_focus, target_count)
        cost.charge("hunt")

        if new_companies:
            print(f"\n  Approval loop: review each company + message drafts.")
            print(f"  Commands: y=approve v1 / 1-5=pick variant / e=edit / t=teach / n=skip\n")
            input("  Press Enter to start... ")

            for i, company in enumerate(new_companies, 1):
                if not cost.can_afford("draft"):
                    print(f"\n  ⚠️  Budget limit reached (${cost.spent:.2f}). Stopping.")
                    print(f"  Tip: Increase weekly_budget in config.yaml if you need more companies.")
                    break

                print(f"\n  [{i}/{len(new_companies)}] Researching {company.get('name','')}...")
                research = research_company(client, company, cost)
                theta    = research["theta"]

                # Case study / article check
                cs_context = ask_case_study_context(
                    company.get("name",""), company.get("industry",""))

                strategy = get_messaging_strategy(
                    {"jobTitle": company.get("target_roles","").split("|")[0]}, patterns)
                variants = draft_first_touch(client, company, theta, strategy,
                                              config, patterns, cs_context)
                cost.charge("draft", note=company.get("name",""))

                status, chosen = approval_loop_new(
                    company, theta, research, variants, cs_context, learnings)

                if status == "approved" and chosen:
                    record = build_contact_record(company, theta, chosen, cs_context)
                    approved_new.append(record)
                    print(f"  ✓ Added: {company['name']}")

                print(f"  💰 {cost.summary()}")

    # Save everything
    banner("💾  SAVING OUTPUTS")
    save_all(approved_new, followup_actions, warm, patterns, cost)

    if approved_new:
        print(f"  ✓ {len(approved_new)} new contacts → {EXPORT_PATH}")
    if followup_actions:
        print(f"  ✓ {len(followup_actions)} reply drafts → {FOLLOWUP_PATH}")
    print(f"  ✓ Task list → {TASKS_PATH}")
    print(f"  ✓ Cost report → {COST_PATH}")

    banner("✅  DONE")
    print(f"  New companies approved:  {len(approved_new)}")
    print(f"  Follow-ups drafted:      {len(followup_actions)}")
    print(f"  Warm contacts waiting:   {len(warm)}")
    print(f"  💰 {cost.summary()}")
    print()
    print("  Next steps:")
    if followup_actions:
        print(f"  1. Send follow-up replies (see {FOLLOWUP_PATH})")
    if approved_new:
        print(f"  2. Import {EXPORT_PATH} into your tracker")
        print(f"  3. Use LinkedIn searches in each contact's notes to find the person")
        print(f"  4. Fill in Name + Title in tracker after finding them")
    print(f"  5. Use {TASKS_PATH} as your Tuesday task list")
    print(f"  6. Friday: run  python outreach_agent_v3_1.py --friday")
    print()

if __name__ == "__main__":
    main()
