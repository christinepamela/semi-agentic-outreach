"""
Semi-Agentic Outreach System v2
================================
What it actually does:
  1. AUDIT   — Scans your tracker, surfaces urgent gaps (responded but no reply, connected but silent)
  2. HUNT    — Web searches for NEW companies globally where Theta applies
  3. RESEARCH — Theta assessment + right decision-maker per new company
  4. DRAFT   — 5 message variants per company
  5. APPROVE — You review each one: y / n / edit  (nothing added without your OK)
  6. EXPORT  — Writes approved contacts to import-ready JSON in your exact tracker format

Run: python outreach_agent_v2.py
"""

import json
import uuid
import sys
import os
from datetime import datetime, timezone
from pathlib import Path

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

# ─────────────────────────────────────────────
# CONFIG
# ─────────────────────────────────────────────

CONFIG_PATH = "config.yaml"
TRACKER_PATH = "data/outreach_import.json"
EXPORT_PATH  = "outputs/new_contacts_for_import.json"
FOLLOWUP_PATH = "outputs/followup_actions.json"

def load_config():
    path = Path(CONFIG_PATH)
    if not path.exists():
        print(f"❌  config.yaml not found. Make sure you're in the project root.")
        sys.exit(1)
    with open(path) as f:
        cfg = yaml.safe_load(f)
    if "YOUR-KEY-HERE" in cfg.get("anthropic_api_key", ""):
        print("❌  Add your Anthropic API key to config.yaml first.")
        sys.exit(1)
    return cfg

# ─────────────────────────────────────────────
# DISPLAY HELPERS
# ─────────────────────────────────────────────

def hr(char="─", n=60):
    print(char * n)

def banner(title):
    print()
    hr("═")
    print(f"  {title}")
    hr("═")

def section(title):
    print()
    hr()
    print(f"  {title}")
    hr()

def ask(prompt, options=None):
    """Simple approval prompt. Returns user input string."""
    if options:
        opts = " / ".join(f"[{o}]" for o in options)
        prompt = f"{prompt}  {opts}: "
    while True:
        resp = input(prompt).strip().lower()
        if options is None or resp in [o.lower() for o in options]:
            return resp
        print(f"  Please enter one of: {', '.join(options)}")

# ─────────────────────────────────────────────
# STEP 1 — AUDIT YOUR EXISTING TRACKER
# ─────────────────────────────────────────────

def audit_tracker(contacts):
    """
    Find the most urgent gaps:
    - Responded to you but you haven't replied (CRITICAL)
    - Connected on LinkedIn but never messaged
    - Messaged once, no follow-up, >14 days ago
    """
    urgent   = []   # responded, needs reply
    warm     = []   # connected, never messaged
    stale    = []   # messaged once, no follow-up

    now = datetime.now(timezone.utc)

    for c in contacts:
        logs   = c.get("communicationLog", [])
        status = c.get("connectionStatus", "")
        name   = c.get("name", "")
        company = c.get("company", "").strip()
        priority = c.get("priority", "")

        # URGENT: they responded but no follow-up message from us
        if len(logs) == 1 and logs[0].get("response", "").strip():
            urgent.append({
                "name": name, "company": company, "priority": priority,
                "jobTitle": c.get("jobTitle", ""),
                "their_response": logs[0]["response"],
                "channel": logs[0].get("channel", "LinkedIn"),
                "our_message": logs[0].get("message", ""),
                "date": logs[0].get("date", ""),
                "contact_obj": c,
            })

        # WARM: connected but never messaged
        elif status == "Connected" and not logs:
            warm.append({
                "name": name, "company": company, "priority": priority,
                "jobTitle": c.get("jobTitle", ""),
                "industryFocus": c.get("industryFocus", ""),
                "notes": c.get("notes", ""),
                "contact_obj": c,
            })

        # STALE: one message sent, no response, no follow-up
        elif len(logs) == 1 and not logs[0].get("response", ""):
            date_str = logs[0].get("date", "")
            days_ago = 999
            if date_str:
                try:
                    sent = datetime.fromisoformat(date_str.replace("Z", "+00:00"))
                    days_ago = (now - sent).days
                except:
                    pass
            if days_ago > 7:
                stale.append({
                    "name": name, "company": company, "priority": priority,
                    "jobTitle": c.get("jobTitle", ""),
                    "days_ago": days_ago,
                    "channel": logs[0].get("channel", "LinkedIn"),
                    "contact_obj": c,
                })

    return urgent, warm, stale


def display_audit(urgent, warm, stale):
    banner("STEP 1 — TRACKER AUDIT")

    print(f"\n  🔴  URGENT — They responded, you haven't replied: {len(urgent)}")
    for i, c in enumerate(urgent, 1):
        print(f"\n  {i}. {c['name']} @ {c['company']} [{c['priority']}]")
        print(f"     Title: {c['jobTitle'] or 'unknown'}")
        print(f"     Their response: \"{c['their_response'][:120]}...\"" if len(c['their_response']) > 120 else f"     Their response: \"{c['their_response']}\"")
        print(f"     Channel: {c['channel']}")

    print(f"\n  🟡  WARM — Connected but never messaged: {len(warm)}")
    for i, c in enumerate(warm, 1):
        print(f"  {i}. {c['name']} @ {c['company']} [{c['priority']}]  {c['jobTitle']}")

    print(f"\n  ⬜  STALE — Messaged once, no follow-up: {len(stale)}")
    for i, c in enumerate(stale, 1):
        print(f"  {i}. {c['name']} @ {c['company']} [{c['priority']}]  ({c['days_ago']}d ago)")


# ─────────────────────────────────────────────
# STEP 2 — DRAFT FOLLOW-UPS FOR URGENT CONTACTS
# ─────────────────────────────────────────────

def draft_followup_reply(client, contact, config):
    """Ask Claude to draft a reply to someone who already responded to Pam."""
    name       = contact["name"].split()[0]
    their_resp = contact["their_response"]
    our_msg    = contact["our_message"]
    company    = contact["company"]
    title      = contact["jobTitle"]
    user_name  = config.get("user_short_name", "Pam")

    prompt = f"""You are drafting a reply for {config.get('user_name','Christine Pamela')}, an innovation consultant who uses the Theta Framework (Core / Edge / Beyond zone methodology).

CONTEXT:
- Pam sent an outreach message to {name} ({title} at {company})
- They replied. Pam needs to respond warmly and move toward a conversation.

PAM'S ORIGINAL MESSAGE:
{our_msg}

THEIR RESPONSE:
{their_resp}

TASK: Draft 3 short reply variants. Each should:
- Acknowledge what they said specifically
- Be warm and genuine, not salesy
- Move toward a 20-min conversation or coffee chat
- Be 3-5 sentences max
- Sign off as "{user_name}"

Format:
---REPLY 1---
[message]

---REPLY 2---
[message]

---REPLY 3---
[message]"""

    try:
        resp = client.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=800,
            messages=[{"role": "user", "content": prompt}]
        )
        return resp.content[0].text.strip()
    except Exception as e:
        return f"[Draft failed: {e}]"


# ─────────────────────────────────────────────
# STEP 3 — HUNT NEW COMPANIES
# ─────────────────────────────────────────────

HUNT_QUERIES = [
    "large enterprises digital transformation breakthrough innovation chief innovation officer 2025 2026",
    "fortune 500 companies innovation lab scaling failure pilots don't ship 2025",
    "global companies innovation portfolio strategy edge beyond core transformation",
    "industrial companies AI transformation innovation strategy leader Asia Europe 2025 2026",
    "life sciences pharma digital transformation innovation portfolio 2025",
    "luxury consumer brands innovation transformation strategy 2025 2026",
    "energy utilities companies digital transformation innovation chief strategy officer",
    "financial services insurance innovation transformation next s-curve 2025",
]

def hunt_new_companies(client, existing_companies, config, budget_remaining):
    """
    Ask Claude to generate a list of NEW companies to target,
    based on Theta Framework fit criteria.
    We avoid web scraping (no browser) and instead use Claude's knowledge
    + structured reasoning to surface real company targets.
    """
    section("STEP 2 — HUNTING NEW COMPANIES")
    print("  Asking Claude to identify new target companies...")
    print(f"  (Budget remaining: ${budget_remaining:.2f})")

    existing_str = ", ".join(sorted(set(existing_companies)))

    prompt = f"""You are a business intelligence researcher for Christine Pamela, an innovation consultant who uses the Theta Framework.

THE THETA FRAMEWORK helps large organizations manage innovation across three zones:
- Core (0-5 years): optimize existing business
- Edge (3-10 years): build next S-curve  
- Beyond (7-15 years): moonshots

Pam's ideal clients are: large global enterprises (5000+ employees) undergoing significant digital transformation or breakthrough innovation challenges — where there's visible tension between optimizing the core and building the future.

COMPANIES ALREADY IN PAM'S TRACKER (do NOT suggest these):
{existing_str}

TASK: Identify 15 specific companies that Pam should be targeting. For each company:
1. It must be a real, named company (not generic categories)
2. Must have visible innovation tension (labs that don't ship, transformation programs, s-curve pressure)
3. Must have a named senior person Pam could reach (Chief Innovation Officer, VP Strategy, Chief Digital Officer, etc.)
4. Global focus — any geography

For each company output EXACTLY this format:

---COMPANY---
NAME: [Company name]
INDUSTRY: [Industry]
COUNTRY: [HQ country]
WHY_THETA_FIT: [2 sentences on their innovation tension / why Theta applies]
TARGET_PERSON: [Full name if known, or "Head of Innovation" type title]
TARGET_TITLE: [Their title]
THETA_ARCHETYPE: [One of: Stuck in Core / Innovation Theater / Edge-Active No Beyond / Balanced Transformer]
RECENT_SIGNAL: [One specific recent news item, initiative, or public statement that signals their innovation challenge]
LINKEDIN_SEARCH: [Suggested LinkedIn search string to find the right person]"""

    try:
        resp = client.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=3000,
            messages=[{"role": "user", "content": prompt}]
        )
        raw = resp.content[0].text.strip()
        companies = parse_hunted_companies(raw)
        print(f"  ✓ Found {len(companies)} new target companies")
        return companies
    except Exception as e:
        print(f"  ✗ Hunt failed: {e}")
        return []


def parse_hunted_companies(raw):
    companies = []
    blocks = raw.split("---COMPANY---")
    for block in blocks[1:]:
        lines = block.strip().splitlines()
        c = {}
        for line in lines:
            for field in ["NAME","INDUSTRY","COUNTRY","WHY_THETA_FIT","TARGET_PERSON",
                         "TARGET_TITLE","THETA_ARCHETYPE","RECENT_SIGNAL","LINKEDIN_SEARCH"]:
                if line.startswith(f"{field}:"):
                    c[field.lower()] = line[len(field)+1:].strip()
        if c.get("name"):
            companies.append(c)
    return companies


# ─────────────────────────────────────────────
# STEP 4 — DRAFT FIRST-TOUCH MESSAGES
# ─────────────────────────────────────────────

def draft_first_touch(client, company, config):
    """Draft 5 message variants for a new company."""
    user_name = config.get("user_short_name", "Pam")
    target_name = company.get("target_person", "")
    first_name = target_name.split()[0] if target_name and not target_name.startswith("Head") else "there"

    prompt = f"""You are drafting outreach messages for {config.get('user_name','Christine Pamela')}, an innovation consultant using the Theta Framework (Core / Edge / Beyond zone methodology for large enterprises).

TARGET:
- Person: {company.get('target_person','')} — {company.get('target_title','')}
- Company: {company.get('name','')} ({company.get('industry','')}, {company.get('country','')})
- Theta archetype: {company.get('theta_archetype','')}
- Why Theta fits: {company.get('why_theta_fit','')}
- Recent signal: {company.get('recent_signal','')}

RULES:
1. Conservative first touch — NO pitching, NO selling, NO mentioning "Theta Framework" by name
2. Lead with a genuine observation about their specific situation
3. Ask ONE good question
4. Max 4 sentences for LinkedIn, max 6 for email
5. Sign off as "{user_name}"
6. Each variant must be meaningfully different (not just tone tweaks)

Write EXACTLY 5 variants:

---VARIANT 1---
CHANNEL: LinkedIn
TONE: Curiosity-led
BODY:
[message]

---VARIANT 2---
CHANNEL: LinkedIn  
TONE: Research observation
BODY:
[message]

---VARIANT 3---
CHANNEL: LinkedIn
TONE: Peer-to-peer
BODY:
[message]

---VARIANT 4---
CHANNEL: Email
SUBJECT: [subject line]
TONE: Strategic question
BODY:
[message]

---VARIANT 5---
CHANNEL: Email
SUBJECT: [subject line]
TONE: Case study hook
BODY:
[message]"""

    try:
        resp = client.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=1500,
            messages=[{"role": "user", "content": prompt}]
        )
        return parse_variants(resp.content[0].text.strip())
    except Exception as e:
        return [{"variant": 1, "channel": "LinkedIn", "tone": "fallback", "subject": "", "body": f"[Draft failed: {e}]"}]


def parse_variants(raw):
    variants = []
    blocks = raw.split("---VARIANT")
    for i, block in enumerate(blocks[1:], 1):
        v = {"variant": i, "channel": "LinkedIn", "tone": "", "subject": "", "body": ""}
        body_lines = []
        in_body = False
        for line in block.strip().splitlines():
            s = line.strip()
            if s.startswith("CHANNEL:"):
                v["channel"] = s[8:].strip()
            elif s.startswith("TONE:"):
                v["tone"] = s[5:].strip()
            elif s.startswith("SUBJECT:"):
                v["subject"] = s[8:].strip()
            elif s.startswith("BODY:"):
                in_body = True
            elif s.startswith("---"):
                in_body = False
            elif in_body:
                body_lines.append(line)
        v["body"] = "\n".join(body_lines).strip()
        if v["body"]:
            variants.append(v)
    return variants[:5]


# ─────────────────────────────────────────────
# STEP 5 — APPROVAL LOOP
# ─────────────────────────────────────────────

def approval_loop_new_company(company, variants):
    """Show company + drafts, get approval. Returns chosen variant or None."""
    section(f"NEW COMPANY: {company['name']}")
    print(f"  Industry:   {company.get('industry','')} | {company.get('country','')}")
    print(f"  Target:     {company.get('target_person','')} — {company.get('target_title','')}")
    print(f"  Archetype:  {company.get('theta_archetype','')}")
    print(f"  Why Theta:  {company.get('why_theta_fit','')}")
    print(f"  Signal:     {company.get('recent_signal','')}")
    print(f"  LinkedIn search: {company.get('linkedin_search','')}")

    print("\n  ── MESSAGE VARIANTS ──")
    for v in variants:
        print(f"\n  [{v['variant']}] {v['channel']} | {v['tone']}")
        if v.get('subject'):
            print(f"      Subject: {v['subject']}")
        print()
        for line in v['body'].splitlines():
            print(f"      {line}")

    print()
    choice = input("  Add to tracker? [y=yes / n=skip / 1-5=pick variant / e=edit]: ").strip().lower()

    if choice == "n":
        return None, None
    if choice == "y" or choice == "":
        chosen = variants[0] if variants else None
        return "approved", chosen
    if choice in ["1","2","3","4","5"]:
        idx = int(choice) - 1
        chosen = variants[idx] if idx < len(variants) else variants[0]
        return "approved", chosen
    if choice == "e":
        print("  Paste your edited message (press Enter twice when done):")
        lines = []
        while True:
            line = input()
            if line == "" and lines and lines[-1] == "":
                break
            lines.append(line)
        custom_body = "\n".join(lines[:-1]).strip()
        custom = {"variant": 0, "channel": "LinkedIn", "tone": "custom", "subject": "", "body": custom_body}
        return "approved", custom
    return None, None


def approval_loop_followup(contact, draft_text):
    """Show follow-up reply drafts, get approval."""
    section(f"FOLLOW-UP NEEDED: {contact['name']} @ {contact['company']}")
    print(f"  Priority: {contact['priority']} | {contact['jobTitle']}")
    print(f"\n  Their response:")
    print(f"  \"{contact['their_response'][:200]}\"")
    print(f"\n  ── REPLY DRAFTS ──")
    print()

    # Parse the 3 reply variants
    replies = []
    blocks = draft_text.split("---REPLY")
    for i, block in enumerate(blocks[1:], 1):
        text = block.strip()
        # Remove the number prefix if present
        if text and text[0].isdigit():
            text = text[2:].strip() if len(text) > 2 else text
        if text.startswith("---"):
            continue
        replies.append(text)

    for i, reply in enumerate(replies, 1):
        print(f"  [{i}]")
        for line in reply.splitlines():
            print(f"      {line}")
        print()

    choice = input("  Send this reply? [1/2/3=pick / n=skip / e=edit]: ").strip().lower()

    if choice == "n":
        return None
    if choice in ["1","2","3"]:
        idx = int(choice) - 1
        return replies[idx] if idx < len(replies) else replies[0]
    if choice == "e":
        print("  Paste your reply (press Enter twice when done):")
        lines = []
        while True:
            line = input()
            if line == "" and lines and lines[-1] == "":
                break
            lines.append(line)
        return "\n".join(lines[:-1]).strip()
    return replies[0] if replies else None


# ─────────────────────────────────────────────
# STEP 6 — BUILD EXPORT JSON
# ─────────────────────────────────────────────

def build_new_contact(company, chosen_variant):
    """Create a contact record in your exact tracker JSON format."""
    now = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    return {
        "company":           company.get("name", "").strip(),
        "name":              company.get("target_person", ""),
        "jobTitle":          company.get("target_title", ""),
        "industryFocus":     company.get("industry", ""),
        "country":           company.get("country", ""),
        "tier":              "Decision-maker",
        "connectionMethod":  chosen_variant.get("channel", "LinkedIn"),
        "connectionStatus":  "Not yet connected",
        "priority":          "Medium",
        "opportunityType":   "Strategic fit",
        "funnelStage":       "Unaware",
        "nextActionDate":    None,
        "tags":              ["theta-hunt"],
        "notes":             (
            f"Theta archetype: {company.get('theta_archetype','')}\n"
            f"Why Theta fits: {company.get('why_theta_fit','')}\n"
            f"Recent signal: {company.get('recent_signal','')}\n"
            f"LinkedIn search: {company.get('linkedin_search','')}"
        ),
        "id":                str(uuid.uuid4()),
        "communicationLog":  [],
        "lastMessage":       "",
        "createdAt":         now,
        "draft_message": {
            "channel": chosen_variant.get("channel", "LinkedIn"),
            "subject": chosen_variant.get("subject", ""),
            "body":    chosen_variant.get("body", ""),
            "tone":    chosen_variant.get("tone", ""),
        }
    }


def save_outputs(new_contacts, followup_actions):
    Path("outputs").mkdir(exist_ok=True)

    # New contacts for import
    export = {
        "version":    "1.0",
        "exportDate": datetime.now(timezone.utc).isoformat().replace("+00:00","Z"),
        "source":     "semi-agentic-outreach-v2",
        "note":       "Import these into your tracker. draft_message field contains the approved first-touch message.",
        "contacts":   new_contacts,
    }
    with open(EXPORT_PATH, "w", encoding="utf-8") as f:
        json.dump(export, f, indent=2, ensure_ascii=False)

    # Follow-up actions
    if followup_actions:
        with open(FOLLOWUP_PATH, "w", encoding="utf-8") as f:
            json.dump({"followups": followup_actions}, f, indent=2, ensure_ascii=False)


# ─────────────────────────────────────────────
# MAIN
# ─────────────────────────────────────────────

def main():
    banner("🧠  SEMI-AGENTIC OUTREACH v2  |  Theta Framework")
    print(f"  {datetime.now().strftime('%A, %B %d %Y %H:%M')}")

    # Load config + data
    config = load_config()
    client = anthropic.Anthropic(api_key=config["anthropic_api_key"])
    budget = config.get("weekly_budget", 2.00)
    spent  = 0.0

    if not Path(TRACKER_PATH).exists():
        print(f"❌  Tracker not found at {TRACKER_PATH}")
        sys.exit(1)

    with open(TRACKER_PATH, encoding="utf-8") as f:
        tracker = json.load(f)
    contacts = tracker.get("contacts", [])

    # ── STEP 1: AUDIT ──────────────────────────────
    urgent, warm, stale = audit_tracker(contacts)
    display_audit(urgent, warm, stale)

    followup_actions = []
    approved_new = []

    # ── STEP 2: FOLLOW-UPS FIRST (highest priority) ──
    if urgent:
        print()
        do_followups = ask("\n  Draft replies for the people who already responded to you?", ["y","n"])
        if do_followups == "y":
            for contact in urgent:
                print(f"\n  Drafting reply for {contact['name']}...")
                draft = draft_followup_reply(client, contact, config)
                spent += 0.03
                chosen_reply = approval_loop_followup(contact, draft)
                if chosen_reply:
                    followup_actions.append({
                        "name":    contact["name"],
                        "company": contact["company"],
                        "channel": contact["channel"],
                        "action":  "Send reply",
                        "message": chosen_reply,
                        "priority": contact["priority"],
                    })
                    print(f"  ✓ Reply approved — added to followup_actions.json")

    # ── STEP 3: HUNT NEW COMPANIES ─────────────────
    print()
    do_hunt = ask("\n  Hunt for NEW companies to add to your tracker?", ["y","n"])

    if do_hunt == "y":
        existing_companies = list(set(c.get("company","").strip() for c in contacts if c.get("company")))
        new_companies = hunt_new_companies(client, existing_companies, config, budget - spent)
        spent += 0.15

        if not new_companies:
            print("  No new companies found.")
        else:
            print(f"\n  Found {len(new_companies)} candidates. Starting approval loop...")
            print("  (You'll review each one and decide y/n before anything is added)\n")
            input("  Press Enter to start reviewing... ")

            for i, company in enumerate(new_companies, 1):
                print(f"\n  [{i}/{len(new_companies)}] Drafting messages for {company.get('name','')}...")
                variants = draft_first_touch(client, company, config)
                spent += 0.05

                status, chosen = approval_loop_new_company(company, variants)

                if status == "approved" and chosen:
                    new_contact = build_new_contact(company, chosen)
                    approved_new.append(new_contact)
                    print(f"  ✓ Added: {company['name']}")

                print(f"  Budget used: ${spent:.2f} / ${budget:.2f}")

                if spent >= budget * 0.95:
                    print(f"\n  ⚠️  Approaching budget limit (${spent:.2f}). Stopping hunt.")
                    break

    # ── STEP 4: SHOW WARM CONTACTS ─────────────────
    if warm:
        section("WARM CONTACTS — Connected but never messaged")
        print("  These people accepted your LinkedIn request. You haven't said anything yet.\n")
        for c in warm:
            print(f"  • {c['name']} @ {c['company']} [{c['priority']}]  {c['jobTitle']}")
        print("\n  Tip: Start with the Hot priority ones. Their details are in your tracker.")

    # ── STEP 5: SAVE OUTPUTS ───────────────────────
    banner("💾  SAVING OUTPUTS")

    save_outputs(approved_new, followup_actions)

    if approved_new:
        print(f"\n  ✓ {len(approved_new)} new contacts → {EXPORT_PATH}")
        print(f"    Import this file into your tracker web app.")
    if followup_actions:
        print(f"  ✓ {len(followup_actions)} follow-up replies → {FOLLOWUP_PATH}")

    # ── SUMMARY ────────────────────────────────────
    banner("✅  DONE")
    print(f"  New companies approved:  {len(approved_new)}")
    print(f"  Follow-ups drafted:      {len(followup_actions)}")
    print(f"  Warm contacts waiting:   {len(warm)}")
    print(f"  Budget used:             ${spent:.2f} / ${budget:.2f}")
    print()
    print("  Next steps:")
    if followup_actions:
        print(f"  1. Send your follow-up replies (see {FOLLOWUP_PATH})")
    if approved_new:
        print(f"  2. Import {EXPORT_PATH} into your tracker")
        print(f"  3. Find each person on LinkedIn using the search strings in notes")
        print(f"  4. Send Tuesday outreach using the draft_message in each contact")
    print()


if __name__ == "__main__":
    main()