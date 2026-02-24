"""
research_module.py — Company research using the Anthropic Claude API.
Uses caching to minimize cost: company background is cached 90 days,
news is always fresh.
"""

from __future__ import annotations
import json
import hashlib
import time
from datetime import datetime, timedelta
from pathlib import Path

import anthropic


class CompanyResearcher:
    """
    Researches companies using Claude (claude-haiku for cost efficiency).
    Caches results to stay under the $2/week budget.
    """

    CACHE_TTL = {
        "background":       90,   # days — stable company info
        "decision_makers":  30,   # days — changes occasionally
        "recent_news":       7,   # days — fetch fresh every week
    }

    def __init__(self, api_key: str, cache_dir: str = "data/cache"):
        self.client = anthropic.Anthropic(api_key=api_key)
        self.cache_dir = Path(cache_dir)
        self.cache_dir.mkdir(parents=True, exist_ok=True)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def research_company(
        self,
        company_name: str,
        contact: dict,
        from_cache: bool = True,
    ) -> dict:
        """
        Research a company and return a structured dict.
        Uses cache where possible to reduce API costs.
        """
        cache_key = self._make_key(company_name)
        cached = self._load_cache(cache_key) if from_cache else {}

        background     = cached.get("background")    or self._fetch_background(company_name)
        decision_makers = cached.get("decision_makers") or self._infer_decision_makers(contact)
        recent_news    = self._fetch_recent_news(company_name)   # always fresh

        result = {
            "company_name": company_name.strip(),
            "industry":     contact.get("industryFocus", ""),
            "country":      contact.get("country", ""),
            "background":   background,
            "decision_makers": decision_makers,
            "recent_news":  recent_news,
            "researched_at": datetime.now().isoformat(),
        }

        # Save to cache
        self._save_cache(cache_key, {
            "background":      background,
            "decision_makers": decision_makers,
        })

        return result

    # ------------------------------------------------------------------
    # Private: fetchers
    # ------------------------------------------------------------------

    def _fetch_background(self, company_name: str) -> str:
        """Ask Claude for a company overview focused on innovation signals."""
        prompt = f"""You are a business research assistant.
Provide a concise, factual overview of {company_name} covering:
1. What the company does and its core business model
2. Key products/services and revenue drivers
3. Innovation signals: known R&D programs, new ventures, digital transformation efforts
4. Recent strategic priorities or announcements (2023–2026)
5. Innovation maturity: does it feel like a Core-optimizer, Edge-builder, or Beyond-bettor?

Keep it under 300 words. Be specific, not generic. Focus on signals useful for a Theta Framework assessment."""

        try:
            response = self.client.messages.create(
                model="claude-haiku-4-5-20251001",
                max_tokens=400,
                messages=[{"role": "user", "content": prompt}],
            )
            return response.content[0].text.strip()
        except Exception as e:
            return f"Research unavailable: {e}"

    def _fetch_recent_news(self, company_name: str) -> str:
        """Ask Claude for recent news signals (last ~6 months)."""
        prompt = f"""List 3–5 notable recent developments about {company_name} from roughly the past 6 months.
Focus on: funding rounds, product launches, partnerships, leadership changes, or strategic pivots.
If you don't have current information, note that and describe what you know from your training data.
Be concise — one sentence per item."""

        try:
            response = self.client.messages.create(
                model="claude-haiku-4-5-20251001",
                max_tokens=300,
                messages=[{"role": "user", "content": prompt}],
            )
            return response.content[0].text.strip()
        except Exception as e:
            return f"News unavailable: {e}"

    def _infer_decision_makers(self, contact: dict) -> list:
        """Build a structured decision-maker profile from the contact record."""
        return [
            {
                "name":     contact.get("name", ""),
                "title":    contact.get("jobTitle", ""),
                "tier":     contact.get("tier", ""),
                "country":  contact.get("country", ""),
                "channel":  contact.get("connectionMethod", "LinkedIn"),
                "status":   contact.get("connectionStatus", ""),
                "priority": contact.get("priority", ""),
                "notes":    contact.get("notes", ""),
            }
        ]

    # ------------------------------------------------------------------
    # Caching
    # ------------------------------------------------------------------

    def _make_key(self, company_name: str) -> str:
        safe = company_name.lower().strip().replace(" ", "_")
        return re.sub(r"[^a-z0-9_]", "", safe) if safe else hashlib.md5(company_name.encode()).hexdigest()[:8]

    def _load_cache(self, key: str) -> dict:
        path = self.cache_dir / f"{key}.json"
        if not path.exists():
            return {}
        try:
            with open(path) as f:
                data = json.load(f)
            # Check TTL per field
            result = {}
            now = datetime.now()
            for field, ttl_days in self.CACHE_TTL.items():
                if field in data:
                    cached_at = datetime.fromisoformat(data.get(f"{field}_cached_at", "2000-01-01"))
                    if (now - cached_at).days < ttl_days:
                        result[field] = data[field]
            return result
        except Exception:
            return {}

    def _save_cache(self, key: str, fields: dict) -> None:
        path = self.cache_dir / f"{key}.json"
        existing = {}
        if path.exists():
            try:
                with open(path) as f:
                    existing = json.load(f)
            except Exception:
                pass
        now = datetime.now().isoformat()
        for field, value in fields.items():
            existing[field] = value
            existing[f"{field}_cached_at"] = now
        with open(path, "w") as f:
            json.dump(existing, f, indent=2)


# Need re for _make_key
import re
