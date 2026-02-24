"""
learning_engine.py — Tracks response patterns and evolves messaging strategy.
Reads ALL historical touchpoints, not just last week.
"""

from __future__ import annotations
import json
from collections import defaultdict
from datetime import datetime
from pathlib import Path


class LearningEngine:
    """
    Analyzes all historical communication data to surface:
    - Response rates by channel
    - Which message angles got replies
    - Seniority-level preferences
    - Timezone / timing signals
    - Per-company insights
    """

    def __init__(self, learnings_path: str = "data/learnings.json"):
        self.learnings_path = Path(learnings_path)
        self.learnings: dict = self._load()

    # ------------------------------------------------------------------
    # Main analysis entry point
    # ------------------------------------------------------------------

    def analyze_patterns(self, outreach_data: dict) -> dict:
        """
        Run full pattern analysis on all contacts.
        Returns a patterns dict and stores it in learnings.json.
        """
        contacts = outreach_data.get("contacts", [])

        channel_stats   = defaultdict(lambda: {"sent": 0, "responded": 0, "positive": 0})
        angle_wins      = defaultdict(int)
        seniority_stats = defaultdict(lambda: {"sent": 0, "responded": 0, "angles": []})
        timing_signals  = []
        company_notes   = {}

        for contact in contacts:
            logs = contact.get("communicationLog", [])
            if not logs:
                continue

            role = contact.get("jobTitle", "").lower()
            seniority = self._classify_seniority(role)

            for i, comm in enumerate(logs):
                channel   = comm.get("channel", "LinkedIn")
                response  = comm.get("response", "")
                sentiment = comm.get("sentiment", "")
                message   = comm.get("message", "")
                date_str  = comm.get("date", "")

                # Channel stats
                channel_stats[channel]["sent"] += 1
                if response:
                    channel_stats[channel]["responded"] += 1
                if sentiment in ("Positive", "Very positive"):
                    channel_stats[channel]["positive"] += 1

                # Seniority stats
                seniority_stats[seniority]["sent"] += 1
                if response:
                    seniority_stats[seniority]["responded"] += 1

                # What angles worked
                msg_lower = message.lower()
                if response:
                    for angle, keywords in self._angle_keywords().items():
                        if any(kw in msg_lower for kw in keywords):
                            angle_wins[angle] += 1
                            if seniority not in seniority_stats[seniority]["angles"]:
                                seniority_stats[seniority]["angles"].append(angle)

                # Timing signals
                if date_str and response:
                    try:
                        dt = datetime.fromisoformat(date_str.replace("Z", "+00:00"))
                        timing_signals.append({
                            "day": dt.strftime("%A"),
                            "hour": dt.hour,
                            "channel": channel,
                        })
                    except ValueError:
                        pass

            # Per-company insights
            co = contact.get("company", "Unknown")
            if co not in company_notes:
                company_notes[co] = {"contacts": [], "responded": 0, "total": 0}
            company_notes[co]["contacts"].append(contact.get("name"))
            company_notes[co]["total"] += len(logs)
            company_notes[co]["responded"] += sum(1 for c in logs if c.get("response"))

        # Compute success rates
        channel_rates = {}
        for ch, stats in channel_stats.items():
            rate = (stats["responded"] / stats["sent"] * 100) if stats["sent"] else 0
            channel_rates[ch] = {**stats, "success_rate_pct": round(rate, 1)}

        seniority_rates = {}
        for level, stats in seniority_stats.items():
            rate = (stats["responded"] / stats["sent"] * 100) if stats["sent"] else 0
            seniority_rates[level] = {
                **stats,
                "success_rate_pct": round(rate, 1),
            }

        best_timing = self._summarize_timing(timing_signals)

        patterns = {
            "analyzed_at": datetime.now().isoformat(),
            "total_contacts_with_logs": len([c for c in contacts if c.get("communicationLog")]),
            "channel_rates": channel_rates,
            "angle_wins": dict(sorted(angle_wins.items(), key=lambda x: x[1], reverse=True)),
            "seniority_rates": seniority_rates,
            "best_timing": best_timing,
            "company_notes": company_notes,
            "top_channel": max(channel_rates, key=lambda k: channel_rates[k]["success_rate_pct"], default="LinkedIn"),
            "top_angle": max(angle_wins, key=angle_wins.get, default="research_observation"),
        }

        # Persist
        self.learnings["patterns"] = patterns
        self.learnings["last_updated"] = datetime.now().isoformat()
        self._save()

        return patterns

    # ------------------------------------------------------------------
    # Messaging strategy (used by message_generator)
    # ------------------------------------------------------------------

    def get_messaging_strategy(self, contact: dict) -> dict:
        """
        Return the best-fit messaging strategy for this contact
        based on accumulated learnings.
        """
        role     = contact.get("jobTitle", "").lower()
        industry = contact.get("industryFocus", "").lower()
        seniority = self._classify_seniority(role)

        # Pull from learnings if we have enough data
        patterns = self.learnings.get("patterns", {})
        top_angle = patterns.get("top_angle", "research_observation")
        top_channel = patterns.get("top_channel", "LinkedIn")

        # Role-based defaults (conservative first touch)
        if seniority == "c_suite_tech":
            return {
                "style": "technical_depth",
                "opening": "specific_tech_observation",
                "framework_intro": "second_touch",
                "proof_type": "technical_case_study",
                "preferred_angle": top_angle,
                "channel_rec": top_channel,
                "note": "CTOs respond to depth + concrete examples. No selling on first touch.",
            }
        elif seniority == "c_suite_business":
            return {
                "style": "strategic_vision",
                "opening": "market_insight",
                "framework_intro": "second_touch",
                "proof_type": "business_impact",
                "preferred_angle": top_angle,
                "channel_rec": top_channel,
                "note": "CEOs respond to portfolio-level questions and peer benchmarks.",
            }
        elif seniority == "innovation_lead":
            return {
                "style": "practitioner_peer",
                "opening": "pain_point_question",
                "framework_intro": "first_touch_light",
                "proof_type": "sector_case_study",
                "preferred_angle": top_angle,
                "channel_rec": top_channel,
                "note": "Innovation heads like 'builders over storytellers' angle — peer credibility.",
            }
        else:
            return {
                "style": "problem_focused",
                "opening": "curiosity_question",
                "framework_intro": "third_touch",
                "proof_type": "relevant_case",
                "preferred_angle": top_angle,
                "channel_rec": top_channel,
                "note": "Conservative default — ask a good question, listen first.",
            }

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _classify_seniority(self, role: str) -> str:
        if any(t in role for t in ["cto", "chief technology", "vp engineering", "chief digital"]):
            return "c_suite_tech"
        if any(t in role for t in ["ceo", "chief executive", "president", "managing director", "md"]):
            return "c_suite_business"
        if any(t in role for t in ["innovation", "r&d", "research", "transformation", "strategy"]):
            return "innovation_lead"
        if any(t in role for t in ["partner", "principal", "director"]):
            return "senior_leader"
        return "other"

    def _angle_keywords(self) -> dict:
        return {
            "case_study":           ["case study", "example", "dbs", "siemens", "telco"],
            "portfolio_governance": ["portfolio", "governance", "portfolio governance"],
            "theta_framework":      ["theta", "core/edge", "core, edge", "s-curve"],
            "research_observation": ["i noticed", "i've been studying", "i've been following"],
            "pain_point_question":  ["how are you thinking", "how do you", "curious how"],
            "market_insight":       ["market", "industry", "trend", "shift", "disruption"],
        }

    def _summarize_timing(self, signals: list) -> dict:
        if not signals:
            return {"note": "No timing data yet — will learn as responses come in."}
        day_counts = defaultdict(int)
        hour_buckets = defaultdict(int)
        for s in signals:
            day_counts[s["day"]] += 1
            bucket = "morning (6–10)" if 6 <= s["hour"] < 10 else \
                     "mid-morning (10–12)" if 10 <= s["hour"] < 12 else \
                     "afternoon (12–17)" if 12 <= s["hour"] < 17 else "other"
            hour_buckets[bucket] += 1
        best_day = max(day_counts, key=day_counts.get)
        best_time = max(hour_buckets, key=hour_buckets.get)
        return {
            "best_day": best_day,
            "best_time_window": best_time,
            "day_distribution": dict(day_counts),
        }

    def _load(self) -> dict:
        if self.learnings_path.exists():
            try:
                with open(self.learnings_path) as f:
                    return json.load(f)
            except json.JSONDecodeError:
                pass
        return {"created_at": datetime.now().isoformat(), "patterns": {}}

    def _save(self) -> None:
        self.learnings_path.parent.mkdir(parents=True, exist_ok=True)
        with open(self.learnings_path, "w") as f:
            json.dump(self.learnings, f, indent=2)
