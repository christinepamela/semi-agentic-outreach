"""
Semi-Agentic Outreach System v3 — Complete
===========================================
Merges v2's audit/hunt/approval loop with full learning engine,
Theta scoring, todo integration, cost caching, and Friday reflection.

Monday:  python outreach_agent_v3.py
Friday:  python outreach_agent_v3.py --friday
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
    """Full historical analysis across all touchpoints."""
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

            # Channel stats
            channel_stats[ch]["sent"] += 1
            if responded: channel_stats[ch]["responded"] += 1
            if positive:  channel_stats[ch]["positive"]  += 1

            # Seniority stats
            seniority_stats[seniority]["sent"] += 1
            if responded: seniority_stats[seniority]["responded"] += 1
            if positive:  seniority_stats[seniority]["positive"]  += 1

            # Angle tracking
            for angle, kws in ANGLE_KEYWORDS.items():
                if any(kw in msg for kw in kws):
                    angle_attempts[angle] += 1
                    if responded:
                        angle_wins[angle] += 1
                        if angle not in seniority_stats[seniority]["angles"]:
                            seniority_stats[seniority]["angles"].append(angle)

            # Timing
            if date_str and responded:
                try:
                    dt = datetime.fromisoformat(date_str.replace("Z","+00:00"))
                    timing.append({"day": dt.strftime("%A"), "hour": dt.hour, "channel": ch})
                except: pass

        # Per-contact insights
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

    # Compute rates
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

    # Best timing
    day_counts  = defaultdict(int)
    hour_counts = defaultdict(int)
    for t in timing:
        day_counts[t["day"]] += 1
        hour_counts[t["hour"] // 3 * 3] += 1  # bucket to 3-hr windows
    best_day  = max(day_counts,  key=day_counts.get)  if day_counts  else "Tuesday"
    best_hour = max(hour_counts, key=hour_counts.get) if hour_counts else 9

    # Top performers
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
    return {"history": [], "patterns": {}, "strategy_notes": []}

def save_learnings(data: dict):
    Path(LEARNINGS_PATH).parent.mkdir(exist_ok=True)
    with open(LEARNINGS_PATH, "w") as f:
        json.dump(data, f, indent=2)

def get_messaging_strategy(contact: dict, patterns: dict) -> dict:
    """Return best-fit messaging approach based on learnings."""
    title     = contact.get("jobTitle","")
    seniority = classify_seniority(title)
    sr        = patterns.get("seniority_rates",{})
    top_angle = patterns.get("top_angle","research_observation")
    top_ch    = patterns.get("top_channel","LinkedIn")

    # Seniority-specific overrides based on actual data
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
# THETA FRAMEWORK — FULL SCORING
# ─────────────────────────────────────────────────────────────

CORE_SIGNALS   = ["operational excellence","efficiency","optimization","cost reduction",
                  "process improvement","quality","reliability","scale","margin","profitability",
                  "customer retention","traditional","incumbent","legacy","core business",
                  "existing products","sustaining","agile","lean","continuous improvement"]
EDGE_SIGNALS   = ["pilot","experiment","venture","new business","adjacent","digital transformation",
                  "platform","ecosystem","partnership","spin-off","incubator","accelerator",
                  "next generation","growth initiative","s-curve","new market","emerging","startup",
                  "product launch","beta","new revenue","digital twin","xcelerator","open innovation"]
BEYOND_SIGNALS = ["moonshot","quantum","deep tech","10x","breakthrough","research lab",
                  "fundamental research","2030","2035","2040","future of","reinvent","disruption",
                  "frontier","autonomous","fusion","biotech","nanotechnology","ai research",
                  "basic research","horizon 3","beyond","long-term bet","venture studio"]
THEATER_SIGNALS= ["innovation lab","innovation hub","digital lab","center of excellence",
                  "hackathon","ideation","prototype","proof of concept","poc",
                  "we're exploring","looking into","vision for","roadmap for 2030",
                  "innovation theater","announce","showcase","award"]

def score_signals(text: str, signals: list) -> int:
    return min(10, sum(1 for s in signals if s in text.lower()))

def theta_assess(research_text: str, company_name: str, industry: str) -> dict:
    text   = research_text.lower()
    core   = score_signals(text, CORE_SIGNALS)
    edge   = score_signals(text, EDGE_SIGNALS)
    beyond = score_signals(text, BEYOND_SIGNALS)
    theater= score_signals(text, THEATER_SIGNALS)

    # Determine archetype
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

    # Gaps
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
        name     = c.get("name","")
        company  = c.get("company","").strip()
        priority = c.get("priority","")
        title    = c.get("jobTitle","")

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

    # Show learnings summary first
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

def hunt_new_companies(client, existing_companies: list, config: dict) -> list:
    section("STEP 2 — HUNTING NEW COMPANIES")
    print("  Asking Claude to identify 15 new global enterprise targets...")

    existing_str = ", ".join(sorted(set(existing_companies)))

    prompt = f"""You are a business intelligence researcher for Christine Pamela, an innovation consultant (Theta Framework — Core/Edge/Beyond zone methodology for large enterprises).

Ideal clients: large global enterprises (2000+ employees) where there is visible tension between:
- Optimizing the core business (efficiency, margins)
- Building next-generation growth (new business models, platforms, innovation units)

They should have a named senior leader Pam can reach — Chief Innovation Officer, VP Strategy, Chief Digital Officer, Chief Technology Officer, or equivalent.

COMPANIES ALREADY IN PAM'S TRACKER (do NOT suggest):
{existing_str}

TASK: Identify exactly 15 new companies across diverse industries and geographies.
Strong fits: industrial conglomerates, pharma/life sciences, luxury, financial services, energy, automotive, telco, consumer goods — any large enterprise with visible innovation tension.

For each, output EXACTLY:

---COMPANY---
NAME: [Company]
INDUSTRY: [Industry]
COUNTRY: [HQ country]
SIZE: [Approx employees]
WHY_THETA_FIT: [2 sentences: what is their specific innovation tension]
THETA_ARCHETYPE: [Stuck in Core / Innovation Theater / Edge-Active No Beyond / Balanced Transformer / Frontier Builder]
TARGET_PERSON: [Full name if known, else role title e.g. "Chief Innovation Officer"]
TARGET_TITLE: [Their exact title]
RECENT_SIGNAL: [One specific recent initiative, announcement, or challenge — be concrete]
LINKEDIN_SEARCH: [Exact search string to find this person on LinkedIn]"""

    try:
        r = client.messages.create(model="claude-haiku-4-5-20251001", max_tokens=3500,
                                    messages=[{"role":"user","content":prompt}])
        raw = r.content[0].text.strip()
        companies = []
        for block in raw.split("---COMPANY---")[1:]:
            c = {}
            for line in block.strip().splitlines():
                for field in ["NAME","INDUSTRY","COUNTRY","SIZE","WHY_THETA_FIT",
                              "THETA_ARCHETYPE","TARGET_PERSON","TARGET_TITLE",
                              "RECENT_SIGNAL","LINKEDIN_SEARCH"]:
                    if line.startswith(f"{field}:"):
                        c[field.lower()] = line[len(field)+1:].strip()
            if c.get("name"):
                companies.append(c)
        print(f"  ✓ {len(companies)} target companies identified")
        return companies
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

    # Background (cached up to 90 days)
    if "background" in cached:
        background = cached["background"]
        cost.charge("cached_research", note=f"{name} (bg cached)")
    else:
        prompt = f"""Research {name} ({industry}). Provide:
1. Core business and revenue model
2. Known innovation programs, R&D, digital transformation efforts
3. Innovation maturity signals — do they ship or just announce?
4. Strategic priorities 2024–2026
Keep under 250 words. Be specific."""
        try:
            r = client.messages.create(model="claude-haiku-4-5-20251001", max_tokens=400,
                                        messages=[{"role":"user","content":prompt}])
            background = r.content[0].text.strip()
            save_cache(name, {"background": background})
            cost.charge("research", note=f"{name} (fresh)")
        except Exception as e:
            background = f"Research unavailable: {e}"
            cost.charge("cached_research", note=f"{name} (error fallback)")

    # Run Theta on combined text
    combined = f"{company.get('why_theta_fit','')} {company.get('recent_signal','')} {background}"
    theta    = theta_assess(combined, name, industry)
    cost.charge("theta", note=name)

    return {"background": background, "theta": theta, "was_cached": "background" in cached}

# ─────────────────────────────────────────────────────────────
# STEP 5 — DRAFT FIRST-TOUCH MESSAGES
# ─────────────────────────────────────────────────────────────

def draft_first_touch(client, company: dict, theta: dict, strategy: dict,
                       config: dict, patterns: dict) -> list:
    user_name  = config.get("user_short_name","Pam")
    target     = company.get("target_person","")
    first_name = target.split()[0] if target and not target[0].isupper() == False else \
                 (target.split()[0] if " " in target else "there")
    first_name = first_name if first_name and len(first_name) > 2 else "there"

    top_angle  = patterns.get("top_angle","research_observation")
    best_send  = patterns.get("best_send_window","Tuesday 09:00–12:00")
    seniority  = strategy.get("seniority","other")
    style_note = strategy.get("note","Lead with curiosity.")

    prompt = f"""Draft outreach messages for {config.get('user_name','Christine Pamela')}, an innovation consultant (Theta Framework).

TARGET:
- {target} — {company.get('target_title','')}
- {company.get('name','')} | {company.get('industry','')} | {company.get('country','')}
- Seniority: {seniority}

THETA ASSESSMENT:
- Archetype: {theta.get('archetype','')}
- Pain point: {theta.get('pain_point','')}
- Recommended move: {theta.get('recommended_move','')}
- Suggested angle: {theta.get('messaging_angle','')}

COMPANY CONTEXT:
{company.get('recent_signal','')}
{company.get('why_theta_fit','')}

STRATEGY NOTE (based on what has worked in Pam's history):
- Best performing angle: {top_angle}
- Style for {seniority}: {style_note}
- Best send time: {best_send}

RULES:
1. First touch ONLY — no framework pitching, no selling
2. Lead with ONE genuine observation or question
3. LinkedIn variants: max 4 sentences. Email: max 6 sentences + subject line
4. Each variant meaningfully different in angle (not just tone)
5. Sign off as "{user_name}"

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
        r = client.messages.create(model="claude-haiku-4-5-20251001", max_tokens=1800,
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
# STEP 6 — APPROVAL LOOP
# ─────────────────────────────────────────────────────────────

def approval_loop_new(company: dict, theta: dict, research: dict, variants: list) -> tuple:
    section(f"NEW COMPANY: {company['name']}")
    cached_tag = " (cached)" if research.get("was_cached") else ""
    print(f"  {company.get('industry','')} | {company.get('country','')} | ~{company.get('size','?')} employees")
    print(f"  Target:    {company.get('target_person','')} — {company.get('target_title','')}")
    print(f"  Archetype: {theta.get('archetype','')}  |  {theta.get('zone_summary','')}")
    print(f"  Zone scores: Core={theta['zone_distribution']['core']} Edge={theta['zone_distribution']['edge']} Beyond={theta['zone_distribution']['beyond']}")
    print(f"  Pain point: {theta.get('pain_point','')}")
    print(f"  Move: {theta.get('recommended_move','')}")
    print(f"  Research{cached_tag}: {research.get('background','')[:200]}...")
    print(f"  LinkedIn search: {company.get('linkedin_search','')}")

    print("\n  ── MESSAGE VARIANTS ──")
    for v in variants:
        print(f"\n  [{v['variant']}] {v['channel']} | {v['tone']}")
        if v.get("subject"): print(f"      Subject: {v['subject']}")
        print()
        for line in v["body"].splitlines():
            print(f"      {line}")

    print()
    choice = input("  Decision? [y=approve v1 / 1-5=pick variant / e=edit / n=skip]: ").strip().lower()

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
        return "approved", {"variant":0,"channel":"LinkedIn","tone":"custom","subject":"","body":body}
    # y or anything else = approve v1
    return "approved", variants[0] if variants else None

def approval_loop_followup(contact: dict, draft_text: str) -> str | None:
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
# STEP 7 — BUILD OUTPUTS
# ─────────────────────────────────────────────────────────────

def build_contact_record(company: dict, theta: dict, chosen_variant: dict) -> dict:
    now = datetime.now(timezone.utc).isoformat().replace("+00:00","Z")
    return {
        "company":          company.get("name","").strip(),
        "name":             company.get("target_person",""),
        "jobTitle":         company.get("target_title",""),
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
            f"Recommended move: {theta.get('recommended_move','')}\n"
            f"Recent signal: {company.get('recent_signal','')}\n"
            f"LinkedIn search: {company.get('linkedin_search','')}"
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
        }
    }

def generate_tuesday_tasks(approved_new: list, followup_actions: list,
                            warm: list, patterns: dict) -> dict:
    tasks = []
    best_send = patterns.get("best_send_window","Tuesday 09:00–12:00")

    # Priority 1: Follow-ups (already have a response)
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

    # Priority 2: New approved contacts
    for i, c in enumerate(approved_new):
        draft = c.get("draft_message",{})
        tasks.append({
            "priority": "2-NEW",
            "task": f"First touch: {c['name']} @ {c['company']}",
            "channel": draft.get("channel","LinkedIn"),
            "action": "Find on LinkedIn → Connect → Send message",
            "message": draft.get("body",""),
            "subject": draft.get("subject",""),
            "send_time": best_send,
            "linkedin_tip": f"Search: {next((l for l in (c.get('notes','') or '').splitlines() if 'LinkedIn search:' in l), '')}",
            "goal_link": "Weekly goal: add 10 new conversations",
        })

    # Priority 3: Warm (connected, never messaged)
    for c in warm[:3]:  # top 3 by priority
        tasks.append({
            "priority": "3-WARM",
            "task": f"First message: {c['name']} @ {c['company']} (already connected)",
            "channel": c.get("connectionMethod","LinkedIn"),
            "action": "Send first message — they accepted your connection",
            "message": "[Draft a message using the Theta angle in their notes]",
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
            "this_week":  "10 thoughtful first touches + reply to all who responded",
            "this_month": "5 new conversations started",
            "quarterly":  "2 Theta consulting engagements opened",
        }
    }

def save_all(approved_new, followup_actions, warm, patterns, cost):
    OUTPUTS_DIR.mkdir(exist_ok=True)

    # New contacts export
    if approved_new:
        export = {
            "version":    "1.0",
            "exportDate": datetime.now(timezone.utc).isoformat().replace("+00:00","Z"),
            "source":     "semi-agentic-outreach-v3",
            "note":       "Import into tracker. draft_message = approved first-touch message.",
            "contacts":   approved_new,
        }
        with open(EXPORT_PATH,"w",encoding="utf-8") as f:
            json.dump(export, f, indent=2, ensure_ascii=False)

    # Follow-ups
    if followup_actions:
        with open(FOLLOWUP_PATH,"w",encoding="utf-8") as f:
            json.dump({"followups": followup_actions}, f, indent=2, ensure_ascii=False)

    # Tuesday tasks
    tasks = generate_tuesday_tasks(approved_new, followup_actions, warm, patterns)
    with open(TASKS_PATH,"w",encoding="utf-8") as f:
        json.dump(tasks, f, indent=2, ensure_ascii=False)

    # Cost
    cost.save()

# ─────────────────────────────────────────────────────────────
# FRIDAY REFLECTION MODE
# ─────────────────────────────────────────────────────────────

def friday_reflection(client, contacts: list, learnings: dict, config: dict):
    banner("🪞  FRIDAY REFLECTION")
    print("  Let's capture what worked this week so the agent improves.\n")

    # Run fresh pattern analysis
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

    print("\n  ── YOUR REFLECTION ──\n")
    print("  Answer these (or press Enter to skip each):\n")

    notes = []
    questions = [
        "What message angle got the best reaction this week?",
        "Who surprised you with a response? What did you say to them?",
        "What industry or role type felt most receptive?",
        "Anything you want to try differently next Monday?",
    ]
    for q in questions:
        print(f"  Q: {q}")
        ans = input("  A: ").strip()
        if ans:
            notes.append({"question": q, "answer": ans, "date": datetime.now().isoformat()})
        print()

    if notes:
        # Ask Claude to synthesize into strategy update
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

        # Save to learnings
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

    # Load data
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
    banner("🧠  SEMI-AGENTIC OUTREACH v3  |  Theta Framework")
    print(f"  {datetime.now().strftime('%A, %B %d %Y %H:%M')}")
    print(f"  {len(contacts)} contacts loaded")

    # Pattern analysis
    print("\n  Analyzing all historical touchpoints...")
    patterns = analyze_patterns(contacts)
    cost.charge("analysis")
    learnings["patterns"] = patterns
    save_learnings(learnings)

    # Audit
    urgent, warm, stale = audit_tracker(contacts)
    display_audit(urgent, warm, stale, patterns)

    followup_actions = []
    approved_new     = []

    # Follow-ups (highest priority)
    if urgent:
        print()
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
    print()
    do_hunt = ask("\n  Hunt for NEW target companies?", ["y","n"])

    if do_hunt == "y":
        existing = list(set(c.get("company","").strip() for c in contacts if c.get("company")))
        new_companies = hunt_new_companies(client, existing, config)
        cost.charge("hunt")

        if new_companies:
            print(f"\n  Approval loop: review each company + message drafts.")
            print(f"  Commands: y=approve / 1-5=pick variant / e=edit / n=skip\n")
            input("  Press Enter to start... ")

            for i, company in enumerate(new_companies, 1):
                if not cost.can_afford("draft"):
                    print(f"\n  ⚠️  Budget limit reached (${cost.spent:.2f}). Stopping.")
                    break

                print(f"\n  [{i}/{len(new_companies)}] Researching {company.get('name','')}...")
                research = research_company(client, company, cost)
                theta    = research["theta"]

                strategy = get_messaging_strategy(
                    {"jobTitle": company.get("target_title","")}, patterns)
                variants = draft_first_touch(client, company, theta, strategy, config, patterns)
                cost.charge("draft", note=company.get("name",""))

                status, chosen = approval_loop_new(company, theta, research, variants)
                if status == "approved" and chosen:
                    record = build_contact_record(company, theta, chosen)
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
        print(f"  3. Use LinkedIn search strings in notes to find each person")
    print(f"  4. Use {TASKS_PATH} as your Tuesday task list")
    print(f"  5. Friday: run  python outreach_agent_v3.py --friday")
    print()

if __name__ == "__main__":
    main()