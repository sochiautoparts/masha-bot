"""Post formatter for masha-bot — 1024/4096 character limits."""

from __future__ import annotations

import re
from typing import Any, Optional


class PostFormatter:
    """Formats posts for Telegram channel with proper limits."""

    # Telegram limits
    PHOTO_CAPTION_LIMIT = 1024
    TEXT_POST_LIMIT = 4096

    def format_post(
        self,
        text: str,
        content_type: str = "news+reaction",
        has_image: bool = False,
    ) -> str:
        """Format a post for the channel, respecting character limits."""
        # Clean up text
        text = self._clean_text(text)

        # Apply character limit
        limit = self.PHOTO_CAPTION_LIMIT if has_image else self.TEXT_POST_LIMIT
        text = self._truncate(text, limit)

        # Ensure proper formatting
        text = self._format_paragraphs(text)
        text = self._ensure_hashtags(text, content_type)
        text = self._ensure_footer(text)

        return text

    def _clean_text(self, text: str) -> str:
        """Clean up the generated text."""
        # Remove markdown code blocks
        text = re.sub(r"```\w*\n?", "", text)
        text = text.replace("```", "")

        # Remove leading "Вот пост:" type prefixes
        text = re.sub(r"^(Вот\s+(ваш\s+)?пост:?|Пост:?|Вот:?)\s*", "", text, flags=re.IGNORECASE)

        # Normalize whitespace
        text = re.sub(r"\n{3,}", "\n\n", text)
        text = text.strip()

        return text

    def _truncate(self, text: str, limit: int) -> str:
        """Truncate text to fit within the limit, preserving complete sentences."""
        if len(text) <= limit:
            return text

        # Try to cut at last sentence boundary before limit
        truncated = text[:limit]

        # Find last sentence boundary
        last_boundary = max(
            truncated.rfind("."),
            truncated.rfind("!"),
            truncated.rfind("?"),
            truncated.rfind("\n"),
        )

        if last_boundary > limit * 0.5:  # At least 50% preserved
            text = text[: last_boundary + 1].strip()
        else:
            text = truncated.rstrip() + "..."

        # Ensure footer fits
        footer = "Автор @asmasha_bot\n@bmw_mpower_club\n#bmw_mpower_club"
        if footer not in text:
            needed = len(footer) + 2  # +2 for \n\n
            if len(text) + needed > limit:
                text = text[: limit - needed].rstrip()
                if not text.endswith((".", "!", "?")):
                    text += "..."
            text = f"{text}\n\n{footer}"

        return text

    def _format_paragraphs(self, text: str) -> str:
        """Ensure proper paragraph formatting."""
        # Split into paragraphs
        paragraphs = text.split("\n\n")

        formatted = []
        for para in paragraphs:
            para = para.strip()
            if para:
                formatted.append(para)

        return "\n\n".join(formatted)

    def _ensure_hashtags(self, text: str, content_type: str) -> str:
        """Ensure relevant hashtags are present."""
        # Default BMW hashtags
        default_tags = ["#bmw", "#bmwm", "#mpower"]

        # Content-type specific tags
        type_tags = {
            "news+reaction": ["#bmwnews", "#bmwnovosti"],
            "DIY/how-to": ["#bmwdiy", "#bmwservice", "#bimmercode"],
            "polls/debates": ["#bmwpoll", "#bmwdebate"],
            "lore/history": ["#bmwhistory", "#bmwclassic", "#bmwlegacy"],
            "garage stories": ["#bmwgarage", "#bmwmechanic"],
            "partner": ["#bmwparts", "#bmwaccessories"],
        }

        extra_tags = type_tags.get(content_type, [])

        # Check which tags are already in the text
        existing_tags = set(re.findall(r"#\w+", text.lower()))

        # Add missing tags
        all_needed = default_tags + extra_tags
        missing = [t for t in all_needed if t.lower() not in existing_tags]

        if missing:
            # Find the footer and add tags before it
            footer = "Автор @asmasha_bot"
            if footer in text:
                parts = text.split(footer)
                tag_line = " ".join(missing[:5])  # Max 5 additional tags
                text = f"{parts[0].rstrip()}\n\n{tag_line}\n{footer}{parts[1] if len(parts) > 1 else ''}"
            else:
                tag_line = " ".join(missing[:5])
                text = f"{text}\n{tag_line}"

        return text

    def _ensure_footer(self, text: str) -> str:
        """Ensure the channel footer is present."""
        footer = "Автор @asmasha_bot\n@bmw_mpower_club\n#bmw_mpower_club"
        if footer not in text:
            text = f"{text}\n\n{footer}"
        return text

    def format_poll(
        self,
        question: str,
        options: list[str],
        context: str = "",
    ) -> tuple[str, list[str]]:
        """Format a poll post. Returns (text, options)."""
        text = f"📊 {question}"
        if context:
            text = f"{context}\n\n{text}"

        # Ensure footer
        text = self._ensure_footer(text)

        # Truncate if needed
        text = self._truncate(text, self.TEXT_POST_LIMIT)

        # Truncate options to 100 chars each (Telegram limit)
        formatted_options = [opt[:100] for opt in options]

        return text, formatted_options
