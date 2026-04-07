"""Generate a consolidated intelligence report from all investigation runs.

Usage:
    python -m scripts.generate_intel_report
    python -m scripts.generate_intel_report --case-id 8119
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from backend.core.config import settings
from backend.core.database import SessionLocal, init_db
from backend.models.case import Case
from backend.models.investigation import InvestigationRun, Lead


RELEVANCE_THRESHOLD = 0.30
ACTIONABLE_THRESHOLD = 0.45


def _bar(score: float, width: int = 20) -> str:
    filled = int(score * width)
    return "█" * filled + "░" * (width - filled)


def _classify_lead(lead: Lead) -> str:
    """Classify a lead into an intelligence category."""
    rationale_text = " ".join(lead.rationale or []).lower()
    title_lower = (lead.title or "").lower()
    excerpt_lower = (lead.content_excerpt or "").lower()
    combined = f"{title_lower} {excerpt_lower} {rationale_text}"

    if "found safe" in combined or "located safe" in combined:
        return "RESOLUTION"
    if "repeat-missing" in combined or "case update" in combined or "still missing" in combined:
        return "REPEAT-MISSING"
    if any(kw in combined for kw in ("companion", "together with", "also missing", "pair was")):
        return "COMPANION-IDENTIFIED"
    if lead.lead_type == "authority-post" or lead.source_kind == "official":
        return "OFFICIAL-ANCHOR"
    if any(kw in combined for kw in ("sighting", "spotted", "seen at", "seen near")):
        return "POSSIBLE-SIGHTING"
    if any(kw in combined for kw in ("community", "share", "facebook group", "good life")):
        return "COMMUNITY-AMPLIFICATION"
    if "news" in (lead.category or ""):
        return "MEDIA-COVERAGE"
    return "WEB-MENTION"


def generate_report(session, case_id: int | None = None) -> str:
    """Generate intelligence report text."""
    from sqlalchemy import select

    if case_id:
        runs = list(session.scalars(
            select(InvestigationRun)
            .where(InvestigationRun.case_id == case_id)
            .order_by(InvestigationRun.completed_at.desc())
        ).all())
    else:
        runs = list(session.scalars(
            select(InvestigationRun)
            .order_by(InvestigationRun.completed_at.desc())
        ).all())

    if not runs:
        return "No investigation runs found."

    # Group runs by case
    cases_data: dict[int, dict] = {}
    for run in runs:
        case = session.get(Case, run.case_id)
        if not case:
            continue
        if case.id not in cases_data:
            cases_data[case.id] = {
                "case": case,
                "runs": [],
                "all_leads": [],
            }
        cases_data[case.id]["runs"].append(run)
        cases_data[case.id]["all_leads"].extend(run.leads)

    lines: list[str] = []
    lines.append("=" * 80)
    lines.append("  MAAT INTELLIGENCE REPORT — Truth from Chaos")
    lines.append(f"  Generated: {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}")
    lines.append(f"  Cases analyzed: {len(cases_data)}")
    lines.append("=" * 80)

    for cid, data in cases_data.items():
        case = data["case"]
        leads = sorted(data["all_leads"], key=lambda l: l.confidence, reverse=True)

        # Deduplicate leads by URL
        seen_urls: set[str] = set()
        unique_leads: list[Lead] = []
        for lead in leads:
            url = lead.source_url or ""
            if url and url in seen_urls:
                continue
            if url:
                seen_urls.add(url)
            unique_leads.append(lead)

        # Classify leads
        classified: dict[str, list[Lead]] = {}
        for lead in unique_leads:
            cat = _classify_lead(lead)
            classified.setdefault(cat, []).append(lead)

        actionable = [l for l in unique_leads if l.confidence >= ACTIONABLE_THRESHOLD]
        relevant = [l for l in unique_leads if l.confidence >= RELEVANCE_THRESHOLD]

        lines.append("")
        lines.append("=" * 80)
        lines.append(f"  CASE: {case.name} (ID: {case.id})")
        lines.append("-" * 80)

        ms = case.missing_since
        if ms:
            if not ms.tzinfo:
                ms = ms.replace(tzinfo=timezone.utc)
            elapsed = (datetime.now(timezone.utc) - ms).days
            lines.append(f"  Missing since: {ms.strftime('%Y-%m-%d')} ({elapsed} days ago)")
        lines.append(f"  Age: {case.age} | Gender: {case.gender}")
        lines.append(f"  Location: {case.city}, {case.province}")
        lines.append(f"  Authority: {case.authority_name}")
        if case.authority_case_url:
            lines.append(f"  Authority URL: {case.authority_case_url}")
        if case.risk_flags:
            lines.append(f"  Risk flags: {', '.join(case.risk_flags)}")
        lines.append(f"  Investigation runs: {len(data['runs'])}")
        lines.append(f"  Total unique leads: {len(unique_leads)}")
        lines.append(f"  Actionable leads (≥{ACTIONABLE_THRESHOLD}): {len(actionable)}")

        # KEY FINDINGS
        lines.append("")
        lines.append("  ┌─ KEY FINDINGS ─────────────────────────────────────────────┐")

        # Check for resolution
        if "RESOLUTION" in classified:
            lines.append("  │                                                             │")
            lines.append("  │  ★ RESOLUTION DETECTED                                     │")
            for lead in classified["RESOLUTION"][:3]:
                title = (lead.title or "")[:60]
                lines.append(f"  │    → {title}")
                if lead.content_excerpt:
                    excerpt = lead.content_excerpt[:70]
                    lines.append(f"  │      {excerpt}")
                lines.append(f"  │      Source: {lead.source_url}")

        # Check for repeat-missing
        if "REPEAT-MISSING" in classified:
            lines.append("  │                                                             │")
            lines.append("  │  ⚠ REPEAT-MISSING PATTERN DETECTED                         │")
            lines.append("  │  This person has been reported missing multiple times.      │")
            for lead in classified["REPEAT-MISSING"][:3]:
                title = (lead.title or "")[:60]
                lines.append(f"  │    → {title}")
                if lead.content_excerpt:
                    excerpt = lead.content_excerpt[:70]
                    lines.append(f"  │      {excerpt}")

        # Check for companion
        if "COMPANION-IDENTIFIED" in classified:
            lines.append("  │                                                             │")
            lines.append("  │  ★ COMPANION IDENTIFIED                                    │")
            for lead in classified["COMPANION-IDENTIFIED"][:3]:
                if lead.content_excerpt:
                    excerpt = lead.content_excerpt[:70]
                    lines.append(f"  │    → {excerpt}")

        # Check for possible sightings
        if "POSSIBLE-SIGHTING" in classified:
            lines.append("  │                                                             │")
            lines.append("  │  ★ POSSIBLE SIGHTING(S)                                    │")
            for lead in classified["POSSIBLE-SIGHTING"][:3]:
                title = (lead.title or "")[:60]
                lines.append(f"  │    → {title}")
                if lead.location_text:
                    lines.append(f"  │      Location: {lead.location_text}")

        # Check for community amplification
        if "COMMUNITY-AMPLIFICATION" in classified:
            count = len(classified["COMMUNITY-AMPLIFICATION"])
            lines.append("  │                                                             │")
            lines.append(f"  │  📢 COMMUNITY AMPLIFICATION: {count} share(s) detected       │")
            for lead in classified["COMMUNITY-AMPLIFICATION"][:2]:
                title = (lead.title or "")[:60]
                lines.append(f"  │    → {title}")

        # Media coverage
        if "MEDIA-COVERAGE" in classified:
            media = [l for l in classified["MEDIA-COVERAGE"] if l.confidence >= RELEVANCE_THRESHOLD]
            if media:
                lines.append("  │                                                             │")
                lines.append(f"  │  📰 MEDIA COVERAGE: {len(media)} relevant article(s)          │")
                for lead in media[:3]:
                    title = (lead.title or "")[:60]
                    lines.append(f"  │    → {title}")
                    lines.append(f"  │      [{_bar(lead.confidence, 15)} {lead.confidence:.2f}]")

        lines.append("  │                                                             │")
        lines.append("  └─────────────────────────────────────────────────────────────┘")

        # TOP ACTIONABLE LEADS
        if actionable:
            lines.append("")
            lines.append(f"  TOP {min(len(actionable), 10)} ACTIONABLE LEADS:")
            lines.append(f"  {'─' * 60}")
            for i, lead in enumerate(actionable[:10], 1):
                cat = _classify_lead(lead)
                lines.append(f"  {i:2}. [{_bar(lead.confidence)} {lead.confidence:.3f}] [{cat}]")
                lines.append(f"      {lead.title}")
                lines.append(f"      URL: {lead.source_url}")
                if lead.content_excerpt:
                    excerpt = lead.content_excerpt[:120]
                    if len(lead.content_excerpt) > 120:
                        excerpt += "..."
                    lines.append(f"      Excerpt: {excerpt}")
                lines.append("")

        # REPORTING INSTRUCTIONS
        lines.append(f"  ┌─ REPORTING INSTRUCTIONS ──────────────────────────────────┐")
        lines.append(f"  │  Authority: {(case.authority_name or 'Unknown')[:48]:48s} │")
        if case.authority_phone:
            lines.append(f"  │  Phone: {case.authority_phone:52s} │")
        if case.authority_case_url:
            url_display = case.authority_case_url[:52]
            lines.append(f"  │  URL: {url_display:53s} │")
        lines.append(f"  │  MCSC Tips: tips@mcsc.ca | 1-800-661-6160              │")
        lines.append(f"  │                                                          │")
        lines.append(f"  │  ⚠ ALL LEADS ARE UNVERIFIED. Do NOT contact the missing  │")
        lines.append(f"  │  person or their family directly. Report to authorities. │")
        lines.append(f"  └──────────────────────────────────────────────────────────┘")

    # SUMMARY STATISTICS
    total_cases = len(cases_data)
    total_leads = sum(len(d["all_leads"]) for d in cases_data.values())
    total_actionable = sum(
        len([l for l in d["all_leads"] if l.confidence >= ACTIONABLE_THRESHOLD])
        for d in cases_data.values()
    )
    resolutions = sum(
        1 for d in cases_data.values()
        for l in d["all_leads"]
        if "found safe" in ((l.content_excerpt or "") + (l.title or "")).lower()
           or "located safe" in ((l.content_excerpt or "") + (l.title or "")).lower()
    )

    lines.append("")
    lines.append("=" * 80)
    lines.append("  PIPELINE SUMMARY")
    lines.append("=" * 80)
    lines.append(f"  Cases investigated: {total_cases}")
    lines.append(f"  Total leads generated: {total_leads}")
    lines.append(f"  Actionable leads (≥{ACTIONABLE_THRESHOLD}): {total_actionable}")
    lines.append(f"  Resolution signals detected: {resolutions}")
    lines.append("")

    return "\n".join(lines)


def main() -> None:
    parser = argparse.ArgumentParser(description="Generate consolidated intelligence report")
    parser.add_argument("--case-id", type=int, help="Report for a specific case")
    parser.add_argument("--output", type=str, help="Output file path (default: stdout + file)")
    args = parser.parse_args()

    init_db()
    with SessionLocal() as session:
        report = generate_report(session, args.case_id)
        print(report)

        # Also save to file
        out_dir = settings.data_dir / "reports"
        out_dir.mkdir(parents=True, exist_ok=True)
        suffix = f"_case_{args.case_id}" if args.case_id else ""
        out_file = out_dir / f"intel_report{suffix}_{datetime.now(timezone.utc).strftime('%Y%m%d_%H%M')}.txt"
        with open(out_file, "w", encoding="utf-8") as f:
            f.write(report)
        print(f"\n  Report saved to: {out_file}")


if __name__ == "__main__":
    main()
