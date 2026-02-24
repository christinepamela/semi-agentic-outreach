"""
message_generator.py — Drafts 5 personalized outreach message variants.
Conservative first touch by default — curiosity-led, no hard selling.
"""

from __future__ import annotations
import anthropic


class MessageGenerator:
    """
    Generates 5 message variants per company using:
    - Theta assessment (zone, archetype, pain points)
    - Research data (recent news, background)
    - Learnings (what angles have worked historically)
    - Contact profile (role, seniority, industry)
    """

    def __init__(self, api_key: str, user_name: str = "Pam", framework_name: str = "Theta Framework"):
        self.client = anthropic.Anthropic(api_key=api_key)
        self.user_name = user_name
        self.framework_name = framework_name

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def create_variants(
        self,
        company: dict,
        research: dict,
        theta: dict,
        strategy: dict,
        learnings: dict,
    ) -> list[dict]:
        """
        Returns a list of 5 message dicts, each with:
        - variant_number
        - channel (LinkedIn / Email)
        - tone
        - subject (for email variants)
        - body
        - send_time_recommendation
        - rationale
        """
        prompt = self._build_prompt(company, research, theta, strategy, learnings)

        try:
            response = self.client.messages.create(
                model="claude-haiku-4-5-20251001",
                max_tokens=1800,
                messages=[{"role": "user", "content": prompt}],
            )
            raw = response.content[0].text.strip()
            return self._parse_variants(raw, company, theta, learnings)
        except Exception as e:
            return self._fallback_variants(company, research, theta, str(e))

    # ------------------------------------------------------------------
    # Prompt construction
    # ------------------------------------------------------------------

    def _build_prompt(self, company: dict, research: dict, theta: dict, strategy: dict, learnings: dict) -> str:
        contact_name   = company.get("name", "")
        contact_title  = company.get("jobTitle", "")
        company_name   = company.get("company", "")
        industry       = company.get("industryFocus", "")
        country        = company.get("country", "")

        archetype      = theta.get("archetype", "")
        messaging_angle = theta.get("messaging_angle", "")
        pain_points    = "; ".join(theta.get("pain_points", []))
        recommended_move = theta.get("recommended_move", "")
        gaps           = "; ".join(theta.get("gaps", []))

        background     = research.get("background", "")
        recent_news    = research.get("recent_news", "")

        top_angle      = learnings.get("top_angle", "research_observation")
        channel_rec    = strategy.get("channel_rec", "LinkedIn")
        best_timing    = learnings.get("best_timing", {})
        best_day       = best_timing.get("best_day", "Tuesday")
        best_window    = best_timing.get("best_time_window", "morning (6–10)")

        return f"""You are drafting outreach messages for {self.user_name}, an innovation consultant who uses the {self.framework_name}.

CONTACT:
- Name: {contact_name}
- Title: {contact_title}
- Company: {company_name}
- Industry: {industry}
- Country: {country}

THETA ASSESSMENT:
- Archetype: {archetype}
- Pain points: {pain_points}
- Gaps: {gaps}
- Recommended move: {recommended_move}
- Messaging angle: {messaging_angle}

COMPANY BACKGROUND:
{background}

RECENT NEWS:
{recent_news}

LEARNINGS (what has worked in the past):
- Top angle: {top_angle}
- Best channel: {channel_rec}
- Best send time: {best_day}, {best_window}

RULES:
1. Conservative first touch — NO selling, NO pitching the Theta Framework directly
2. Lead with curiosity, a genuine observation, or a thoughtful question
3. Keep messages short: LinkedIn ≤ 5 sentences, Email ≤ 8 sentences
4. Sign off as "{self.user_name}"
5. Personalize to the contact's specific role and industry
6. Each variant should be meaningfully different (not just tone changes)

Write EXACTLY 5 variants. Format each one like this:

---VARIANT 1---
CHANNEL: LinkedIn
TONE: Curiosity-led
SUBJECT: N/A
BODY:
[message text]
SEND TIME: {best_day} {best_window} ({country} timezone)
RATIONALE: [1 sentence on why this angle]

---VARIANT 2---
[continue same format]

... through VARIANT 5

Variant 5 should always be an email variant with a subject line."""

    # ------------------------------------------------------------------
    # Parsing
    # ------------------------------------------------------------------

    def _parse_variants(self, raw: str, company: dict, theta: dict, learnings: dict) -> list[dict]:
        """Parse the raw LLM output into structured variant dicts."""
        variants = []
        blocks = raw.split("---VARIANT")
        for i, block in enumerate(blocks[1:], start=1):
            lines = block.strip().splitlines()
            variant: dict = {
                "variant_number": i,
                "channel": "LinkedIn",
                "tone": "",
                "subject": "",
                "body": "",
                "send_time": "",
                "rationale": "",
            }
            body_lines = []
            in_body = False
            for line in lines:
                stripped = line.strip()
                if stripped.startswith("CHANNEL:"):
                    variant["channel"] = stripped.replace("CHANNEL:", "").strip()
                elif stripped.startswith("TONE:"):
                    variant["tone"] = stripped.replace("TONE:", "").strip()
                elif stripped.startswith("SUBJECT:"):
                    variant["subject"] = stripped.replace("SUBJECT:", "").strip()
                elif stripped.startswith("BODY:"):
                    in_body = True
                elif stripped.startswith("SEND TIME:"):
                    in_body = False
                    variant["send_time"] = stripped.replace("SEND TIME:", "").strip()
                elif stripped.startswith("RATIONALE:"):
                    in_body = False
                    variant["rationale"] = stripped.replace("RATIONALE:", "").strip()
                elif in_body:
                    body_lines.append(line)
            variant["body"] = "\n".join(body_lines).strip()
            if variant["body"]:
                variants.append(variant)
        return variants[:5] if variants else self._fallback_variants(company, {}, theta, "parse error")

    def _fallback_variants(self, company: dict, research: dict, theta: dict, error: str) -> list[dict]:
        """Return minimal fallback variants if API call fails."""
        name     = company.get("name", "")
        co       = company.get("company", "")
        angle    = theta.get("messaging_angle", "I'd love to learn more about your innovation approach.")
        first_name = name.split()[0] if name else "there"

        return [
            {
                "variant_number": 1,
                "channel": "LinkedIn",
                "tone": "Curiosity-led (fallback)",
                "subject": "",
                "body": f"Hi {first_name},\n\n{angle}\n\nWould a brief conversation make sense?\n\nBest,\n{self.user_name}",
                "send_time": "Tuesday morning",
                "rationale": f"Fallback template — API error: {error}",
            }
        ]
