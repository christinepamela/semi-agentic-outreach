"""
theta_framework.py — Maps companies to Core / Edge / Beyond zones.
Based on Christine Pamela's Theta Framework methodology.
"""

from __future__ import annotations
import re


# ---------------------------------------------------------------------------
# Keyword signals used for heuristic scoring
# ---------------------------------------------------------------------------

CORE_SIGNALS = [
    "operational excellence", "efficiency", "optimization", "cost reduction",
    "process improvement", "quality", "reliability", "scale", "margin",
    "profitability", "customer retention", "traditional", "incumbent",
    "legacy", "core business", "existing", "sustaining",
]

EDGE_SIGNALS = [
    "pilot", "experiment", "venture", "new business", "adjacent",
    "digital transformation", "platform", "ecosystem", "partnership",
    "spin-off", "incubator", "accelerator", "lab", "next generation",
    "growth initiative", "s-curve", "new market", "emerging",
    "startup", "agile", "product launch", "beta",
]

BEYOND_SIGNALS = [
    "moonshot", "quantum", "deep tech", "10x", "breakthrough",
    "research lab", "fundamental research", "2030", "2035", "2040",
    "future of", "reinvent", "disruption", "frontier",
    "autonomous", "fusion", "biotech", "nanotechnology", "ai research",
    "basic research", "horizon 3", "beyond",
]

INNOVATION_THEATER_SIGNALS = [
    "innovation lab", "innovation hub", "digital lab", "center of excellence",
    "hackathon", "ideation", "prototype", "proof of concept", "poc",
    "we're exploring", "looking into", "vision for", "roadmap for 2030",
]


# ---------------------------------------------------------------------------
# Main assessor class
# ---------------------------------------------------------------------------

class ThetaAssessor:
    """
    Assesses a company's Theta positioning from research text.

    Usage:
        assessor = ThetaAssessor()
        result = assessor.assess_position(research_data)
    """

    def __init__(self):
        self.zones = {
            "core":   {"timeline": "0–5 years",   "color": "🟩", "focus": "Optimize existing"},
            "edge":   {"timeline": "3–10 years",   "color": "🟨", "focus": "Build next S-curve"},
            "beyond": {"timeline": "7–15+ years",  "color": "🟥", "focus": "Moonshots & frontier"},
        }
        self.four_moves = [
            "Move 1: Deep Audit — Map complexity, trace blockers",
            "Move 2: Rewire the System — Create protected innovation lanes",
            "Move 3: Measure What Matters — Stage-appropriate KPIs",
            "Move 4: Builders Over Storytellers — Ship, test, grow",
        ]

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def assess_position(self, research_data: dict) -> dict:
        """
        Main entry point. Takes a research_data dict (from research_module)
        and returns a full Theta assessment.
        """
        # Build a single text blob to scan for signals
        text = self._flatten_to_text(research_data)

        core_score  = self._score_signals(text, CORE_SIGNALS)
        edge_score  = self._score_signals(text, EDGE_SIGNALS)
        beyond_score = self._score_signals(text, BEYOND_SIGNALS)
        theater_risk = self._score_signals(text, INNOVATION_THEATER_SIGNALS)

        # Normalize scores to 0–10
        core_score   = min(10, core_score)
        edge_score   = min(10, edge_score)
        beyond_score = min(10, beyond_score)

        primary_focus = self._determine_primary_focus(core_score, edge_score, beyond_score)
        gaps          = self._identify_gaps(core_score, edge_score, beyond_score)
        pain_points   = self._map_pain_points(core_score, edge_score, beyond_score, theater_risk)
        recommended_move = self._select_move(gaps, pain_points, theater_risk)
        messaging_angle  = self._craft_messaging_angle(pain_points, recommended_move, research_data)
        archetype        = self._classify_archetype(core_score, edge_score, beyond_score, theater_risk)

        return {
            "zone_distribution": {
                "core":   core_score,
                "edge":   edge_score,
                "beyond": beyond_score,
            },
            "primary_focus": primary_focus,
            "archetype": archetype,
            "gaps": gaps,
            "pain_points": pain_points,
            "theater_risk": theater_risk > 2,
            "recommended_move": recommended_move,
            "messaging_angle": messaging_angle,
            "zone_summary": self._zone_summary(primary_focus, archetype),
        }

    # ------------------------------------------------------------------
    # Scoring helpers
    # ------------------------------------------------------------------

    def _flatten_to_text(self, data: dict) -> str:
        """Recursively flatten dict values into a single lowercase string."""
        parts = []
        for v in data.values():
            if isinstance(v, str):
                parts.append(v.lower())
            elif isinstance(v, list):
                parts.extend(str(i).lower() for i in v)
            elif isinstance(v, dict):
                parts.append(self._flatten_to_text(v))
        return " ".join(parts)

    def _score_signals(self, text: str, signals: list) -> int:
        """Count how many distinct signal words appear in the text."""
        score = 0
        for signal in signals:
            if signal in text:
                score += 1
        return score

    # ------------------------------------------------------------------
    # Assessment logic
    # ------------------------------------------------------------------

    def _determine_primary_focus(self, core: int, edge: int, beyond: int) -> str:
        scores = {"core": core, "edge": edge, "beyond": beyond}
        return max(scores, key=scores.get)

    def _identify_gaps(self, core: int, edge: int, beyond: int) -> list:
        gaps = []
        if edge < 3:
            gaps.append("Missing Edge: No visible next S-curve work detected")
        if beyond < 2:
            gaps.append("Missing Beyond: No long-term moonshot signals")
        if core > 7 and edge < 3:
            gaps.append("Core-heavy: Risk of disruption from below if Edge remains underfunded")
        if edge > 5 and beyond < 2:
            gaps.append("Edge-active but no Beyond anchor: mid-term vision without long horizon")
        if not gaps:
            gaps.append("Reasonably balanced portfolio — opportunity to sharpen zone governance")
        return gaps

    def _map_pain_points(self, core: int, edge: int, beyond: int, theater: int) -> list:
        points = []
        if theater > 2:
            points.append("Innovation theater risk: labs and pilots that don't ship to market")
        if core > 6 and edge < 3:
            points.append("Stuck in Core: optimizing existing business while Edge bets are absent")
        if edge > 4 and beyond < 2:
            points.append("Short-horizon trap: Edge activity without 10+ year vision")
        if edge > 3 and core > 6:
            points.append("Core/Edge tension: governance and metrics likely misaligned across zones")
        if not points:
            points.append("Primary opportunity: tightening stage-appropriate KPIs across zones")
        return points

    def _select_move(self, gaps: list, pain_points: list, theater: int) -> str:
        if theater > 2:
            return self.four_moves[3]  # Move 4: Builders over Storytellers
        if any("Missing Edge" in g for g in gaps):
            return self.four_moves[1]  # Move 2: Rewire the System
        if any("Edge" in p and "beyond" in p.lower() for p in pain_points):
            return self.four_moves[2]  # Move 3: Measure What Matters
        if any("Core-heavy" in g for g in gaps):
            return self.four_moves[0]  # Move 1: Deep Audit
        return self.four_moves[2]  # Default: Measure What Matters

    def _classify_archetype(self, core: int, edge: int, beyond: int, theater: int) -> str:
        if theater > 2:
            return "Innovation Theater"
        if core > 6 and edge < 3:
            return "Stuck in Core"
        if edge > 5 and beyond < 2:
            return "Edge-Active, No Beyond"
        if beyond > 4:
            return "Frontier Builder"
        if core > 5 and edge > 4:
            return "Balanced Transformer"
        return "Early Stage Explorer"

    def _craft_messaging_angle(self, pain_points: list, move: str, research: dict) -> str:
        company = research.get("company_name", "the company")
        industry = research.get("industry", "your sector")

        if "innovation theater" in " ".join(pain_points).lower():
            return (
                f"I've noticed {company} has impressive innovation programs. "
                f"How are you measuring which pilots are actually on a path to market?"
            )
        if "stuck in core" in " ".join(pain_points).lower():
            return (
                f"As {industry} faces increasing disruption, how is {company} "
                f"building its next S-curve without destabilizing the core?"
            )
        if "short-horizon" in " ".join(pain_points).lower():
            return (
                f"{company}'s Edge work looks strong. How are you planting the seeds "
                f"for where the business needs to be in 2032 and beyond?"
            )
        return (
            f"Curious how {company} governs the tension between optimizing today "
            f"and investing in what comes next."
        )

    def _zone_summary(self, primary_focus: str, archetype: str) -> str:
        zone = self.zones[primary_focus]
        return (
            f"{zone['color']} Primary zone: {primary_focus.capitalize()} "
            f"({zone['timeline']}) | Archetype: {archetype}"
        )
