"""Transparent lead scoring with rationale output."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone

from backend.models.case import Case
from backend.osint.normalization.models import NormalizedLead
from shared.utils.dates import ensure_utc
from shared.utils.geo import haversine_km
from shared.utils.text import token_similarity


@dataclass(slots=True)
class ScoredLead:
    """Lead score details."""

    score: float
    rationale: list[str]


def _recency_score(published_at: datetime | None, missing_since: datetime | None) -> tuple[float, str | None]:
    if published_at is None:
        return 0.1, None
    pub = ensure_utc(published_at)
    if missing_since is None:
        age_days = max(0, int((datetime.now(timezone.utc) - pub).total_seconds() // 86400))
        if age_days <= 7:
            return 0.7, "Published within the last 7 days."
        if age_days <= 30:
            return 0.45, "Published within the last 30 days."
        return 0.2, "Older content lowers recency relevance."

    ms = ensure_utc(missing_since)
    delta_days = int((pub - ms).total_seconds() // 86400)
    if delta_days < 0:
        return 0.05, "Content predates the disappearance."
    if delta_days <= 7:
        return 1.0, "Content appeared within a week of the disappearance."
    if delta_days <= 30:
        return 0.7, "Content appeared within a month of the disappearance."
    if delta_days <= 180:
        return 0.4, "Content appeared months after the disappearance."
    return 0.2, "Content is temporally distant from the disappearance."


_MISSING_KEYWORDS = {
    "missing", "disappeared", "last seen", "police", "rcmp", "amber alert",
    "vulnerable", "appeal", "search", "located", "found safe", "sighting",
    "tips", "reward", "abducted", "runaway", "endangered",
}

_IRRELEVANT_KEYWORDS = {
    "obituary", "funeral", "rip", "condolences", "memorial",
    "sports score", "roster", "fantasy", "draft pick", "trade",
    "recipe", "cookbook", "restaurant review",
    "pornstar", "porn", "xxx", "onlyfans", "escort",
    "nylon-queens", "foxy reviews",
}


def _name_in_text(name: str, text: str) -> bool:
    """Check if the full name appears in the text (case-insensitive)."""
    return name.lower() in text.lower()


def _relevance_score(case: Case, lead: NormalizedLead) -> tuple[float, list[str]]:
    """Score how relevant a lead is to a missing-person case vs coincidental name match."""
    text_blob = " ".join(filter(None, [
        lead.title or "", lead.summary or "", lead.content_excerpt or "",
    ])).lower()
    reasons: list[str] = []
    score = 0.0

    name_present = bool(case.name and _name_in_text(case.name, text_blob))

    matched_keywords = [kw for kw in _MISSING_KEYWORDS if kw in text_blob]
    if matched_keywords:
        boost = min(0.5, len(matched_keywords) * 0.15)
        score += boost
        reasons.append(f"Missing-person keywords found: {', '.join(matched_keywords[:4])}")

    repeat_missing_signals = [
        "case update", "updated photo", "still missing", "still trying to locate",
        "last seen on", "updated information", "previously reported missing",
        "continue to search", "renewed appeal",
    ]
    repeat_hits = [s for s in repeat_missing_signals if s in text_blob]
    if repeat_hits and name_present:
        score += 0.35
        reasons.append(f"REPEAT-MISSING PATTERN: historical record for same person ({', '.join(repeat_hits[:2])})")

    if case.city and case.city.lower() in text_blob:
        score += 0.2
        reasons.append("Lead mentions the case city.")
    if case.authority_name and case.authority_name.lower() in text_blob:
        score += 0.25
        reasons.append("Lead mentions the investigating authority.")
    if case.age is not None and str(case.age) in text_blob:
        age_contexts = [f"{case.age}-year", f"{case.age} year", f"age {case.age}", f"age: {case.age}"]
        if any(ctx in text_blob for ctx in age_contexts):
            score += 0.15
            reasons.append("Lead mentions the subject's age in context.")

    irrelevant_hits = [kw for kw in _IRRELEVANT_KEYWORDS if kw in text_blob]
    if irrelevant_hits:
        penalty = min(0.4, len(irrelevant_hits) * 0.15)
        score -= penalty
        reasons.append(f"Irrelevant content detected: {', '.join(irrelevant_hits[:3])}")

    if lead.published_at and case.missing_since:
        pub = ensure_utc(lead.published_at)
        ms = ensure_utc(case.missing_since)
        if pub and ms:
            delta = (pub - ms).total_seconds() / 86400
            if delta < -30:
                if matched_keywords and name_present:
                    score += 0.1
                    reasons.append("Historical missing-person record for the same individual - valuable context.")
                else:
                    score -= 0.35
                    reasons.append("Content published well before the disappearance with no missing-person context - likely a different person.")

    if not matched_keywords and not name_present and lead.source_kind != "official":
        score -= 0.2
        reasons.append("No missing-person keywords and name not found - likely irrelevant.")

    # Penalize leads where no part of the person's name appears at all.
    # This filters cross-case contamination (e.g., different missing persons
    # returned by broad keyword searches).
    if case.name and lead.source_kind != "official":
        name_parts = [p.lower() for p in case.name.split() if len(p) >= 3]
        parts_found = sum(1 for p in name_parts if p in text_blob)
        if parts_found == 0 and name_parts:
            score -= 0.30
            reasons.append("No part of the subject's name found in lead text — likely about a different person.")

    if lead.source_kind == "official":
        score += 0.2
        reasons.append("Official source gets a relevance boost.")

    return max(0.0, min(1.0, score)), reasons


def score_lead(case: Case, lead: NormalizedLead) -> ScoredLead:
    """Score a normalized lead and return rationale."""
    rationale = []
    total = 0.0

    relevance_component, relevance_reasons = _relevance_score(case, lead)
    total += relevance_component * 0.30
    rationale.extend(relevance_reasons)

    name_similarity = token_similarity(case.name or "", lead.title or "")
    alias_similarity = max((token_similarity(alias, lead.title or "") for alias in case.aliases), default=0.0)
    name_component = max(name_similarity, alias_similarity)
    total += name_component * 0.15
    if name_component:
        rationale.append(f"Name/alias match quality contributed {name_component:.2f}.")

    geo_component = 0.0
    text_blob = " ".join(filter(None, [lead.summary, lead.content_excerpt, lead.location_text or ""])).lower()
    if case.city and case.city.lower() in text_blob:
        geo_component = max(geo_component, 0.8)
    if case.province and case.province.lower() in text_blob:
        geo_component = max(geo_component, 0.5)
    if case.latitude is not None and case.longitude is not None and lead.latitude is not None and lead.longitude is not None:
        distance = haversine_km(case.latitude, case.longitude, lead.latitude, lead.longitude)
        if distance <= 25:
            geo_component = max(geo_component, 1.0)
            rationale.append("Lead coordinates are within 25 km of the last known location.")
        elif distance <= 100:
            geo_component = max(geo_component, 0.6)
            rationale.append("Lead coordinates are within 100 km of the last known location.")
    total += geo_component * 0.15

    age_component = 0.0
    if case.age is not None and case.age <= 12:
        age_component = 0.3
        rationale.append("Younger-child cases receive a modest urgency boost.")
    total += age_component * 0.05

    recency_component, recency_reason = _recency_score(lead.published_at, case.missing_since)
    total += recency_component * 0.15
    if recency_reason:
        rationale.append(recency_reason)

    trust_component = max(0.0, min(1.0, lead.source_trust))
    total += trust_component * 0.10
    rationale.append(f"Source credibility contributed {trust_component:.2f}.")

    corroboration_component = min(1.0, lead.corroboration_count / 3)
    total += corroboration_component * 0.10
    if lead.corroboration_count > 1:
        rationale.append("Cross-source corroboration increased the score.")

    if lead.source_kind == "dark-web-capable":
        total -= 0.05
        rationale.append("Dark-web-capable indexing results are down-weighted until manually reviewed.")

    for existing_reason in lead.rationale:
        rationale.append(existing_reason)

    score = round(max(0.0, min(1.0, total)), 3)
    return ScoredLead(score=score, rationale=rationale)
