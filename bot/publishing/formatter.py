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
        """Truncate text to fit within the limit, preserving complete sentences.
        
        Uses smart boundary-based truncation — never cuts mid-word or mid-sentence.
        Always preserves the footer.
        """
        if len(text) <= limit:
            return text

        footer = "Автор @asmasha_bot\n@bmw_mpower_club\n#bmw_mpower_club"
        
        # Separate content from footer
        content = text
        if footer in text:
            content = text[:text.index(footer)].rstrip()
        
        max_content = limit - len(footer) - 4  # -4 for \n\n separator
        if max_content < 100:
            return footer
        
        if len(content) > max_content:
            content = self._smart_truncate_content(content, max_content)
        
        return f"{content}\n\n{footer}"
    
    def _smart_truncate_content(self, text: str, max_len: int) -> str:
        """Smart truncation at natural sentence/paragraph boundary."""
        if len(text) <= max_len:
            return text
        
        target = max_len - 3
        if target < 50:
            return text[:target] + "..."
        
        search_zone = text[:target + 1]
        
        # 1. Paragraph break
        last_para = search_zone.rfind("\n\n")
        if last_para > target * 0.5:
            return text[:last_para].rstrip() + "..."
        
        # 2. Sentence end
        sentence_end_chars = ['. ', '! ', '? ', '… ', '.\n', '!\n', '?\n', '…\n']
        best_sentence_end = -1
        for end_char in sentence_end_chars:
            pos = search_zone.rfind(end_char)
            if pos > best_sentence_end and pos > target * 0.5:
                best_sentence_end = pos + len(end_char) - 1
        
        if best_sentence_end > target * 0.5:
            return text[:best_sentence_end + 1].rstrip() + "..."
        
        # 3. Newline
        last_newline = search_zone.rfind("\n")
        if last_newline > target * 0.5:
            return text[:last_newline].rstrip() + "..."
        
        # 4. Space (avoid mid-word)
        last_space = search_zone.rfind(" ")
        if last_space > target * 0.5:
            return text[:last_space].rstrip() + "..."
        
        # 5. Hard cut — very last resort
        return text[:target].rstrip() + "..."

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
