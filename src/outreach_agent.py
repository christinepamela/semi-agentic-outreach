"""
outreach_agent.py — Main entry point for the Semi-Agentic Outreach System.

Run every Monday morning:
    python src/outreach_agent.py

Outputs to:
    outputs/monday_strategy.json   — Full research + Theta + messages
    outputs/tuesday_tasks.json     — Task list for your todo tracker
    outputs/cost_report.json       — Weekly spend report
"""

from __future__ import annotations
import json
import sys
import os
from datetime import datetime
from pathlib import Path

# Add src directory to path so imports work when run from project root
sys.path.insert(0, str(Path(__file__).parent))

try:
    import yaml
except ImportError:
    print("❌ PyYAML not installed. Run: pip install pyyaml")
    sys.exit(1)

try:
    from colorama import init, Fore, Style
    init(autoreset=True)
    HAS_COLOR = True
except ImportError:
    HAS_COLOR = False

from theta_framework import ThetaAssessor
from learning_engine import LearningEngine
from cost_tracker import CostTracker
from research_module import CompanyResearcher
from message_generator import MessageGenerator


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def c(text: str, color: str = "") -> str:
    """Colorize console output if colorama is available."""
    if not HAS_COLOR:
        return text
    colors = {
        "green":  Fore.GREEN,
        "yellow": Fore.YELLOW,
        "red":    Fore.RED,
        "cyan":   Fore.CYAN,
        "bold":   Style.BRIGHT,
    }
    return f"{colors.get(color, '')}{text}{Style.RESET_ALL}"


def banner(msg: str) -> None:
    print(f"\n{c('━' * 60, 'cyan')}")
    print(f"  {c(msg, 'bold')}")
    print(f"{c('━' * 60, 'cyan')}")


# ---------------------------------------------------------------------------
# Main Agent
# ---------------------------------------------------------------------------

class OutreachAgent:
    """
    Orchestrates the full Monday morning workflow:
    Load → Analyze → Select Targets → Research → Theta → Draft → Output
    """

    def __init__(self, config_path: str = "config.yaml"):
        self.config = self._load_config(config_path)
        self._validate_config()

        # Initialize subsystems
        self.theta      = ThetaAssessor()
        self.learner    = LearningEngine(learnings_path=self.config["learnings_path"])
        self.cost       = CostTracker(budget_limit=self.config["weekly_budget"])
        self.researcher = CompanyResearcher(
            api_key=self.config["anthropic_api_key"],
            cache_dir=self.config["cache_dir"],
        )
        self.msg_gen    = MessageGenerator(
            api_key=self.config["anthropic_api_key"],
            user_name=self.config.get("user_short_name", "Pam"),
            framework_name=self.config.get("framework_name", "Theta Framework"),
        )

        # Ensure output directory exists
        Path(self.config["output_dir"]).mkdir(parents=True, exist_ok=True)

    # ------------------------------------------------------------------
    # Public: main run
    # ------------------------------------------------------------------

    def run_weekly_cycle(self) -> None:
        """Execute the full Monday morning workflow."""
        banner("🚀  SEMI-AGENTIC OUTREACH — MONDAY CYCLE")
        print(f"  Date: {datetime.now().strftime('%A, %B %d %Y %H:%M')}")
        print(f"  Budget: ${self.config['weekly_budget']:.2f}/week\n")

        # === STEP 1: Load data ===
        print(c("📂  Loading outreach data...", "cyan"))
        outreach_data = self._load_outreach_data()
        total_contacts = len(outreach_data.get("contacts", []))
        logs_count = sum(
            len(c.get("communicationLog", []))
            for c in outreach_data.get("contacts", [])
        )
        print(f"  ✓ {total_contacts} contacts loaded, {logs_count} historical touchpoints")

        # === STEP 2: Analyze patterns ===
        print(c("\n📊  Analyzing historical patterns...", "cyan"))
        self.cost.log_operation("pattern_analysis")
        patterns = self.learner.analyze_patterns(outreach_data)
        self._print_pattern_summary(patterns)

        # === STEP 3: Select targets ===
        print(c("\n🎯  Selecting this week's targets...", "cyan"))
        targets = self._select_targets(outreach_data)
        print(f"  ✓ {len(targets)} companies selected")

        # === STEP 4–6: Research → Theta → Draft ===
        results = []
        for i, contact in enumerate(targets, start=1):
            company_name = contact.get("company", "Unknown").strip()
            print(c(f"\n[{i}/{len(targets)}] {company_name}", "yellow"))

            if not self.cost.can_continue():
                print(c("  ⚠️  Budget limit reached — stopping early", "red"))
                break

            # Research
            print("  🔍 Researching...")
            is_cached = self._is_cached(company_name)
            research = self.researcher.research_company(company_name, contact)
            op_type = "cached_research" if is_cached else "research"
            self.cost.log_operation(op_type, note=company_name)
            print(f"  ✓ Research complete {'(cached)' if is_cached else ''}")

            # Theta assessment
            print("  🧩 Running Theta assessment...")
            theta = self.theta.assess_position(research)
            self.cost.log_operation("assessment", note=company_name)
            print(f"  ✓ {theta.get('zone_summary', '')}")

            # Message drafting
            print("  ✍️  Drafting messages...")
            strategy = self.learner.get_messaging_strategy(contact)
            messages = self.msg_gen.create_variants(
                company=contact,
                research=research,
                theta=theta,
                strategy=strategy,
                learnings=patterns,
            )
            self.cost.log_operation("draft", note=company_name)
            print(f"  ✓ {len(messages)} message variants drafted")

            # Build result record
            results.append({
                "rank":         i,
                "company":      company_name,
                "contact": {
                    "name":    contact.get("name", ""),
                    "title":   contact.get("jobTitle", ""),
                    "country": contact.get("country", ""),
                    "channel": contact.get("connectionMethod", "LinkedIn"),
                    "status":  contact.get("connectionStatus", ""),
                    "priority": contact.get("priority", ""),
                    "funnel_stage": contact.get("funnelStage", ""),
                },
                "theta":        theta,
                "research_summary": {
                    "background":   research.get("background", "")[:500],
                    "recent_news":  research.get("recent_news", ""),
                },
                "messaging_strategy": strategy,
                "messages":     messages,
                "cost":         self.cost.operations[-1] if self.cost.operations else {},
            })

        # === STEP 7: Save outputs ===
        banner("💾  Saving outputs")
        self._save_strategy_report(results, patterns)
        self._save_tuesday_tasks(results)
        self.cost.save_report()

        # === Final summary ===
        banner("✅  Complete!")
        print(f"  Processed: {len(results)} companies")
        self.cost.print_summary()
        print(f"\n  📁 Outputs:")
        print(f"     {self.config['output_dir']}/monday_strategy.json")
        print(f"     {self.config['output_dir']}/tuesday_tasks.json")
        print(f"     {self.config['output_dir']}/cost_report.json")
        print()

    # ------------------------------------------------------------------
    # Target selection
    # ------------------------------------------------------------------

    def _select_targets(self, outreach_data: dict) -> list:
        """
        Pick the best N companies to research this week.
        Priority order:
        1. Hot priority + Connected (ready to message now)
        2. Hot priority + due for follow-up
        3. Medium priority + Connected
        4. Medium priority, not yet contacted
        5. Low priority as filler
        """
        contacts = outreach_data.get("contacts", [])
        n = self.config.get("companies_per_week", 10)

        def score(c: dict) -> tuple:
            priority_score = {"Hot": 3, "Medium": 2, "Low": 1}.get(c.get("priority", ""), 0)
            status_score   = {"Connected": 3, "Responded": 4, "Invited": 1, "Ignored": 0}.get(
                c.get("connectionStatus", ""), 0)
            funnel_score   = {"Engaged": 4, "Consideration": 3, "Unaware": 1}.get(
                c.get("funnelStage", ""), 0)
            has_next_action = 1 if c.get("nextActionDate") else 0
            return (priority_score, status_score, funnel_score, has_next_action)

        sorted_contacts = sorted(contacts, key=score, reverse=True)

        # De-duplicate by company (one contact per company)
        seen_companies = set()
        selected = []
        for contact in sorted_contacts:
            company = contact.get("company", "").strip()
            if company and company not in seen_companies:
                seen_companies.add(company)
                selected.append(contact)
            if len(selected) >= n:
                break

        return selected

    # ------------------------------------------------------------------
    # Output writers
    # ------------------------------------------------------------------

    def _save_strategy_report(self, results: list, patterns: dict) -> None:
        report = {
            "generated_at": datetime.now().isoformat(),
            "week_of": datetime.now().strftime("%Y-%m-%d"),
            "total_companies": len(results),
            "patterns_summary": {
                "top_channel": patterns.get("top_channel", "LinkedIn"),
                "top_angle":   patterns.get("top_angle", "research_observation"),
                "best_timing": patterns.get("best_timing", {}),
            },
            "companies": results,
        }
        path = Path(self.config["output_dir"]) / "monday_strategy.json"
        with open(path, "w", encoding="utf-8") as f:
            json.dump(report, f, indent=2, ensure_ascii=False)
        print(f"  ✓ monday_strategy.json saved ({len(results)} companies)")

    def _save_tuesday_tasks(self, results: list) -> None:
        """Save a clean task list for your todo tracker."""
        tasks = []
        for r in results:
            contact = r["contact"]
            theta   = r["theta"]
            msgs    = r["messages"]

            # Pick recommended variant (first LinkedIn or first overall)
            recommended = next((m for m in msgs if "linkedin" in m.get("channel", "").lower()), None)
            if not recommended and msgs:
                recommended = msgs[0]

            tasks.append({
                "task": f"Reach out to {contact['name']} @ {r['company']}",
                "priority": contact.get("priority", "Medium"),
                "channel": contact.get("channel", "LinkedIn"),
                "send_time": recommended.get("send_time", "Tuesday morning") if recommended else "Tuesday",
                "theta_archetype": theta.get("archetype", ""),
                "pain_point": theta.get("pain_points", [""])[0],
                "recommended_message": recommended.get("body", "") if recommended else "",
                "all_variants_count": len(msgs),
                "funnel_stage": contact.get("funnel_stage", "Unaware"),
                "country": contact.get("country", ""),
            })

        output = {
            "generated_at": datetime.now().isoformat(),
            "for_date": datetime.now().strftime("%Y-%m-%d"),  # next Tuesday
            "total_tasks": len(tasks),
            "tasks": tasks,
        }
        path = Path(self.config["output_dir"]) / "tuesday_tasks.json"
        with open(path, "w", encoding="utf-8") as f:
            json.dump(output, f, indent=2, ensure_ascii=False)
        print(f"  ✓ tuesday_tasks.json saved ({len(tasks)} tasks)")

    # ------------------------------------------------------------------
    # Config & helpers
    # ------------------------------------------------------------------

    def _load_config(self, config_path: str) -> dict:
        path = Path(config_path)
        if not path.exists():
            # Try from parent directory (when running from src/)
            alt = Path(__file__).parent.parent / config_path
            if alt.exists():
                path = alt
            else:
                print(f"❌ config.yaml not found at {config_path} or {alt}")
                print("   Make sure you run this from the project root:")
                print("   cd C:\\Users\\chris\\semi-agentic-outreach")
                print("   python src/outreach_agent.py")
                sys.exit(1)
        with open(path) as f:
            config = yaml.safe_load(f)
        # Resolve relative paths relative to config file location
        base = path.parent
        for key in ("outreach_data_path", "learnings_path", "cache_dir", "output_dir"):
            if key in config:
                config[key] = str(base / config[key])
        return config

    def _validate_config(self) -> None:
        key = self.config.get("anthropic_api_key", "")
        if "YOUR-KEY-HERE" in key or not key.startswith("sk-"):
            print(c("❌  API key not configured!", "red"))
            print("   Open config.yaml and replace YOUR-KEY-HERE with your real key.")
            print("   Get a key at: https://console.anthropic.com")
            sys.exit(1)

        data_path = Path(self.config["outreach_data_path"])
        if not data_path.exists():
            print(c(f"❌  Outreach data not found: {data_path}", "red"))
            print("   Export your tracker as JSON and save it to data/outreach_import.json")
            sys.exit(1)

    def _load_outreach_data(self) -> dict:
        with open(self.config["outreach_data_path"], encoding="utf-8") as f:
            return json.load(f)

    def _is_cached(self, company_name: str) -> bool:
        import re
        safe = company_name.lower().strip().replace(" ", "_")
        key = re.sub(r"[^a-z0-9_]", "", safe)
        cache_path = Path(self.config["cache_dir"]) / f"{key}.json"
        return cache_path.exists()

    def _print_pattern_summary(self, patterns: dict) -> None:
        channel_rates = patterns.get("channel_rates", {})
        if channel_rates:
            print("  Channel response rates:")
            for ch, stats in channel_rates.items():
                rate = stats.get("success_rate_pct", 0)
                bar = "█" * int(rate / 10) + "░" * (10 - int(rate / 10))
                print(f"    {ch:12s} [{bar}] {rate:.0f}%  ({stats['responded']}/{stats['sent']} replied)")
        top = patterns.get("top_angle", "—")
        timing = patterns.get("best_timing", {})
        print(f"  Top angle: {top}")
        if timing.get("best_day"):
            print(f"  Best time: {timing['best_day']} {timing.get('best_time_window', '')}")


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    agent = OutreachAgent(config_path="config.yaml")
    agent.run_weekly_cycle()
