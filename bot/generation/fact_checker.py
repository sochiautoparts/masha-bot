"""BMW fact validation for masha-bot.

Checks:
- Model names are real (no "M7" unless Alpina B7 or M760i)
- Engine codes match models (S63→M5 F90, S58→M3 G80/M4 G82, etc.)
- HP figures are realistic for the model
- Flags common AI hallucinations about BMW
"""

from __future__ import annotations

import logging
import re
from typing import Any, Optional

from ...ai.router import get_ai_router
from ...bot.knowledge.bmw_base import (
    BMW_M_MODELS,
    BMW_ENGINES,
    BMW_SERIES,
    BMW_HALLUCINATIONS,
    is_valid_bmw_model,
    is_valid_bmw_engine,
    get_engine_for_model,
    validate_hp_for_model,
)

logger = logging.getLogger(__name__)


class BMWFactChecker:
    """Validates BMW-related claims in generated content."""

    def _get_router(self):
        """Get the global AI router singleton — shares failover chain with main bot."""
        return get_ai_router()

    async def check_post(self, text: str) -> list[dict[str, Any]]:
        """Check a post for BMW-related factual errors.

        Returns a list of check results, each with:
        - claim: the claim found
        - verdict: correct|incorrect|partially_correct|unverifiable
        - explanation: why
        - correction: corrected version if needed
        """
        checks: list[dict[str, Any]] = []

        # 1. Check model names
        model_issues = self._check_model_names(text)
        checks.extend(model_issues)

        # 2. Check engine-model combinations
        engine_issues = self._check_engine_model_combos(text)
        checks.extend(engine_issues)

        # 3. Check HP figures
        hp_issues = self._check_hp_figures(text)
        checks.extend(hp_issues)

        # 4. Check against known hallucinations
        hallucination_issues = self._check_hallucinations(text)
        checks.extend(hallucination_issues)

        # 5. If suspicious claims found, do AI-powered fact check
        if self._has_suspicious_claims(text):
            ai_checks = await self._ai_fact_check(text)
            checks.extend(ai_checks)

        return checks

    def _check_model_names(self, text: str) -> list[dict[str, Any]]:
        """Check for invalid BMW model names in text."""
        issues: list[dict[str, Any]] = []

        # Look for patterns like "M7", "M1" (modern), "M9"
        invalid_models = [
            ("M7", "Не существует BMW M7. Есть Alpina B7 или M760i (не M-division)"),
            ("M1 modern", "Современного BMW M1 не существует. Оригинальный M1 выпускался 1978-1981"),
        ]

        text_lower = text.lower()

        for model, explanation in invalid_models:
            if model.lower() in text_lower:
                # Special case: M760i is valid, so "M7" alone might be about M760i
                if model == "M7" and "m760i" in text_lower:
                    continue
                # M1 might refer to the classic
                if model == "M1 modern" and ("1978" in text or "классический" in text_lower or "оригинальный" in text_lower):
                    continue

                issues.append({
                    "claim": model,
                    "verdict": "incorrect",
                    "explanation": explanation,
                    "correction": None,
                    "source": "knowledge_base",
                })

        return issues

    def _check_engine_model_combos(self, text: str) -> list[dict[str, Any]]:
        """Check for incorrect engine-model combinations."""
        issues: list[dict[str, Any]] = []

        # Known wrong combinations
        wrong_combos: list[dict[str, str]] = [
            {
                "pattern": r"M5\s*F90.*V10|V10.*M5\s*F90",
                "claim": "V10 in M5 F90",
                "verdict": "incorrect",
                "explanation": "M5 F90 использует S63 V8 twin-turbo, а не V10. V10 S85 был в E60 M5.",
                "correction": "M5 F90 использует S63 4.4L V8 twin-turbo (600-625 л.с.)",
            },
            {
                "pattern": r"M3\s*E92.*N54|N54.*M3\s*E92",
                "claim": "N54 in M3 E92",
                "verdict": "incorrect",
                "explanation": "E92 M3 использует S65 V8, а не N54. N54 устанавливался в 335i/135i.",
                "correction": "E92 M3 использует S65 4.0L V8 (420 л.с.)",
            },
            {
                "pattern": r"M3\s*G80.*S63|S63.*M3\s*G80",
                "claim": "S63 in M3 G80",
                "verdict": "incorrect",
                "explanation": "M3 G80 использует S58 I6 twin-turbo, а не S63 V8. S63 в M5/M8/XM.",
                "correction": "M3 G80 использует S58 3.0L I6 twin-turbo (473-503 л.с.)",
            },
            {
                "pattern": r"M4\s*G82.*S63|S63.*M4\s*G82",
                "claim": "S63 in M4 G82",
                "verdict": "incorrect",
                "explanation": "M4 G82 использует S58 I6 twin-turbo, а не S63 V8.",
                "correction": "M4 G82 использует S58 3.0L I6 twin-turbo (473-503 л.с.)",
            },
        ]

        for combo in wrong_combos:
            if re.search(combo["pattern"], text, re.IGNORECASE):
                issues.append({
                    "claim": combo["claim"],
                    "verdict": combo["verdict"],
                    "explanation": combo["explanation"],
                    "correction": combo.get("correction"),
                    "source": "knowledge_base",
                })

        return issues

    def _check_hp_figures(self, text: str) -> list[dict[str, Any]]:
        """Check for unrealistic HP figures in text."""
        issues: list[dict[str, Any]] = []

        # Pattern: model + HP number
        hp_patterns = [
            (r"M5\s*F90.*?(\d{3,4})\s*(?:л\.с\.|hp|HP|лс)", "M5 F90"),
            (r"M3\s*G80.*?(\d{3,4})\s*(?:л\.с\.|hp|HP|лс)", "M3 G80"),
            (r"M4\s*G82.*?(\d{3,4})\s*(?:л\.с\.|hp|HP|лс)", "M4 G82"),
            (r"M2\s*G87.*?(\d{3,4})\s*(?:л\.с\.|hp|HP|лс)", "M2 G87"),
            (r"XM.*?(\d{3,4})\s*(?:л\.с\.|hp|HP|лс)", "XM"),
        ]

        for pattern, model in hp_patterns:
            match = re.search(pattern, text, re.IGNORECASE)
            if match:
                claimed_hp = int(match.group(1))
                validation = validate_hp_for_model(model, claimed_hp)
                if not validation.get("valid", True):
                    issues.append({
                        "claim": f"{model} {claimed_hp} л.с.",
                        "verdict": "incorrect",
                        "explanation": validation.get("reason", "HP figure seems incorrect"),
                        "correction": f"Actual range: {validation.get('actual_range', 'unknown')}",
                        "source": "knowledge_base",
                    })

        return issues

    def _check_hallucinations(self, text: str) -> list[dict[str, Any]]:
        """Check against known AI hallucinations about BMW."""
        issues: list[dict[str, Any]] = []
        text_lower = text.lower()

        for hall in BMW_HALLUCINATIONS:
            myth = hall["myth"].lower()
            # Check if the myth appears in text
            if myth in text_lower:
                issues.append({
                    "claim": hall["myth"],
                    "verdict": "incorrect",
                    "explanation": hall["truth"],
                    "correction": hall["truth"],
                    "source": "hallucination_database",
                })

        return issues

    def _has_suspicious_claims(self, text: str) -> bool:
        """Detect if text contains claims that need AI fact-checking."""
        suspicious_patterns = [
            r'по\s*данным\s*BMW',
            r'BMW\s*объявила',
            r'BMW\s*подтвердила',
            r'официально\s*объявлено',
            r'рекорд\s*Нюрбургринга',
            r'\d{1,2}:\d{2}\.\d{2}',  # Lap times
        ]
        return any(re.search(p, text, re.IGNORECASE) for p in suspicious_patterns)

    async def _ai_fact_check(self, text: str) -> list[dict[str, Any]]:
        """Use AI to fact-check suspicious claims."""
        try:
            router = self._get_router()
            result = await router.fact_check(text)
            if result and result.get("verdict") == "incorrect":
                return [{
                    "claim": "AI-detected factual error",
                    "verdict": result["verdict"],
                    "explanation": result.get("explanation", ""),
                    "correction": result.get("correction"),
                    "source": "ai_fact_check",
                }]
        except Exception as exc:
            logger.error("AI fact-check failed: %s", exc)

        return []

    # No close() needed — we use the global router singleton,
    # which is cleaned up by the main bot lifecycle.
