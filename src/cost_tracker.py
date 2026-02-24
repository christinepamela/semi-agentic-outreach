"""
cost_tracker.py — Budget management for the outreach agent.
Keeps weekly spend under $2 using operation tracking and caching.
"""

import json
from datetime import datetime
from pathlib import Path


class CostTracker:
    """Tracks API spend and enforces the weekly budget."""

    # Cost estimates per operation type (USD)
    COSTS = {
        "research":        0.15,   # Full company research (web search + analysis)
        "draft":           0.05,   # Message drafting (5 variants)
        "assessment":      0.08,   # Theta zone assessment
        "pattern_analysis": 0.10,  # Learning engine analysis
        "cached_research":  0.01,  # Using cached company data (almost free)
    }

    def __init__(self, budget_limit: float = 2.00, report_path: str = "outputs/cost_report.json"):
        self.budget_limit = budget_limit
        self.report_path = Path(report_path)
        self.total_spent: float = 0.0
        self.operations: list = []

    # ------------------------------------------------------------------
    # Core methods
    # ------------------------------------------------------------------

    def log_operation(self, op_type: str, note: str = "") -> float:
        """Record an operation and add its cost to the running total."""
        cost = self.COSTS.get(op_type, 0.0)
        self.total_spent += cost
        self.operations.append({
            "type": op_type,
            "cost": cost,
            "note": note,
            "timestamp": datetime.now().isoformat(),
            "running_total": round(self.total_spent, 4),
        })
        return cost

    def can_continue(self) -> bool:
        """Return True if budget allows at least one more research cycle."""
        min_cycle_cost = self.COSTS["research"] + self.COSTS["draft"] + self.COSTS["assessment"]
        return (self.total_spent + min_cycle_cost) <= self.budget_limit

    def remaining(self) -> float:
        return max(0.0, self.budget_limit - self.total_spent)

    # ------------------------------------------------------------------
    # Reporting
    # ------------------------------------------------------------------

    def get_report(self) -> dict:
        """Generate the full cost report."""
        return {
            "week_ending": datetime.now().strftime("%Y-%m-%d"),
            "total_spent": round(self.total_spent, 4),
            "budget_limit": self.budget_limit,
            "remaining": round(self.remaining(), 4),
            "budget_used_pct": round((self.total_spent / self.budget_limit) * 100, 1),
            "operations": self.operations,
            "optimization_tips": self._get_optimization_tips(),
        }

    def save_report(self) -> None:
        """Write cost report to outputs directory."""
        self.report_path.parent.mkdir(parents=True, exist_ok=True)
        with open(self.report_path, "w") as f:
            json.dump(self.get_report(), f, indent=2)

    def print_summary(self) -> None:
        """Print a compact summary to the console."""
        pct = (self.total_spent / self.budget_limit) * 100
        bar_filled = int(pct / 5)
        bar = "█" * bar_filled + "░" * (20 - bar_filled)
        print(f"  💰 Budget: [{bar}] ${self.total_spent:.2f} / ${self.budget_limit:.2f} ({pct:.0f}%)")

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    def _get_optimization_tips(self) -> list:
        tips = []

        cached = sum(1 for op in self.operations if "cached" in op["type"])
        total_research = sum(1 for op in self.operations if op["type"] == "research")

        if total_research > 0 and cached == 0:
            tips.append("Enable caching — researching the same companies again wastes ~$0.14 each.")
        if total_research > 10:
            tips.append("Consider reducing companies_per_week to 8 to stay comfortably under budget.")
        if self.total_spent > self.budget_limit * 0.9:
            tips.append("Close to budget limit — consider using more cached research next week.")
        if not tips:
            tips.append("Budget usage looks healthy. Keep going!")

        return tips
