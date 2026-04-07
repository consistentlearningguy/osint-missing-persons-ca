"""Full investigation pipeline: sync cases, run all enabled connectors, and output leads.

Usage:
    python -m scripts.investigate_case                  # investigate newest open case
    python -m scripts.investigate_case --case-id 8123   # investigate specific case
    python -m scripts.investigate_case --top 3          # investigate top 3 newest cases
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

# Ensure project root is on sys.path
PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from backend.core.config import settings
from backend.core.database import SessionLocal, init_db
from backend.enrichment.official_context import extract_official_context
from backend.ingestion.mcsc import MCSCArcGISClient, normalize_case_feature
from backend.models.case import Case, CasePhoto, ResourceLink, SourceRecord
from backend.models.investigation import InvestigationRun, Lead
from backend.osint.aggregation import merge_normalized_leads
from backend.osint.connectors.registry import enabled_connectors
from backend.osint.normalization.models import QueryContext
from backend.osint.scoring.lead_scoring import score_lead
from backend.osint.synthesis import synthesize_investigation
from backend.osint.hypothesis import generate_hypothesis
from backend.enrichment.resources import resource_links_for_province
from shared.utils.dates import isoformat


def _print_section(title: str, char: str = "=") -> None:
    print(f"\n{char * 70}")
    print(f"  {title}")
    print(f"{char * 70}")


def _print_lead(index: int, lead: Lead) -> None:
    confidence_bar = "█" * int(lead.confidence * 20) + "░" * (20 - int(lead.confidence * 20))
    print(f"\n  [{index}] {lead.title}")
    print(f"      Score: {lead.confidence:.3f} [{confidence_bar}]")
    print(f"      Type: {lead.lead_type} | Category: {lead.category}")
    print(f"      Source: {lead.source_name} ({lead.source_kind})")
    print(f"      URL: {lead.source_url}")
    if lead.published_at:
        print(f"      Published: {lead.published_at.strftime('%Y-%m-%d %H:%M UTC')}")
    if lead.location_text:
        print(f"      Location: {lead.location_text}")
    if lead.content_excerpt:
        excerpt = lead.content_excerpt[:200]
        if len(lead.content_excerpt) > 200:
            excerpt += "..."
        print(f"      Excerpt: {excerpt}")
    if lead.rationale:
        print(f"      Rationale:")
        for reason in lead.rationale[:5]:
            print(f"        - {reason}")
    print(f"      Trust: {lead.source_trust:.2f} | Corroboration: {lead.corroboration_count}")


async def sync_cases(session) -> dict:
    """Sync open cases from the MCSC ArcGIS feed."""
    print("  Fetching open cases from MCSC ArcGIS feed...")
    client = MCSCArcGISClient()
    features = await client.fetch_open_cases()
    normalized = [normalize_case_feature(f) for f in features]

    seen_ids = set()
    added = 0
    updated = 0

    for payload in normalized:
        seen_ids.add(payload["id"])
        existing = session.get(Case, payload["id"])
        photos = payload.pop("photos", [])
        source_records = payload.pop("source_records", [])
        payload.pop("status_label", None)

        if existing is None:
            existing = Case(**payload)
            session.add(existing)
            session.flush()
            added += 1
        else:
            for key, value in payload.items():
                setattr(existing, key, value)
            updated += 1

        existing.photos.clear()
        for photo in photos:
            existing.photos.append(CasePhoto(**photo))

        existing.source_records.clear()
        for source in source_records:
            existing.source_records.append(SourceRecord(**source))

        existing.resource_links.clear()
        for resource in resource_links_for_province(existing.province):
            existing.resource_links.append(
                ResourceLink(
                    province=existing.province,
                    category=resource["category"],
                    label=resource["label"],
                    url=resource["url"],
                    authority_type=resource.get("authority_type"),
                    official=True,
                )
            )

    session.commit()
    result = {"added": added, "updated": updated, "total": len(normalized)}
    print(f"  Synced: {result['added']} new, {result['updated']} updated, {result['total']} total open cases")
    return result


async def investigate_case(session, case: Case) -> InvestigationRun:
    """Run all enabled connectors against a case and return the investigation run."""
    connectors = enabled_connectors()
    connector_names = [c.metadata.name for c in connectors]

    _print_section(f"INVESTIGATING: {case.name} (ID: {case.id})")
    print(f"  Age: {case.age} | Gender: {case.gender}")
    print(f"  City: {case.city}, {case.province}")
    if case.missing_since:
        ms = case.missing_since if case.missing_since.tzinfo else case.missing_since.replace(tzinfo=timezone.utc)
        elapsed = (datetime.now(timezone.utc) - ms).days
        print(f"  Missing since: {ms.strftime('%Y-%m-%d %H:%M UTC')} ({elapsed} days ago)")
    print(f"  Status: {case.status} | Case status: {case.case_status}")
    print(f"  Authority: {case.authority_name}")
    if case.authority_case_url:
        print(f"  Authority URL: {case.authority_case_url}")
    if case.risk_flags:
        print(f"  Risk flags: {', '.join(case.risk_flags)}")

    # Parse official context
    official_context = extract_official_context(
        case.official_summary_html,
        city=case.city,
        province=case.province,
    )
    if official_context.get("location_text"):
        print(f"  Last-seen location: {official_context['location_text']}")
    if official_context.get("descriptor_chips"):
        print(f"  Description: {', '.join(official_context['descriptor_chips'])}")

    # Create investigation run
    run = InvestigationRun(
        case_id=case.id,
        status="running",
        connector_names=connector_names,
        feature_flags=settings.feature_flags,
        facts_summary="Official facts from MCSC/public police resources only.",
        inference_summary="Running connectors...",
    )
    session.add(run)
    session.commit()
    session.refresh(run)

    # Build query context
    query_context = QueryContext(
        case_id=case.id,
        name=case.name or "",
        aliases=case.aliases or [],
        city=case.city,
        province=case.province,
        age=case.age,
        missing_since=case.missing_since,
        location_text=official_context.get("location_text"),
        authority_name=case.authority_name,
        authority_case_url=case.authority_case_url,
        case_reference_url=(
            f"{settings.mcsc_feature_server_url}/query?where=objectid%3D{case.id}&outFields=*"
            "&returnGeometry=true&f=json"
        ),
        source_urls=[
            v for v in [case.authority_case_url, case.source_url,
                        *(r.source_url for r in case.source_records)]
            if v
        ],
        image_urls=[p.url for p in case.photos if p.url],
    )

    _print_section(f"Running {len(connectors)} enabled connectors", "-")
    print(f"  Connectors: {', '.join(connector_names)}")

    collected_leads = []
    connector_failures = []

    from backend.models.investigation import SearchQueryLog

    for connector in connectors:
        name = connector.metadata.name
        print(f"\n  >> {name}...", end=" ", flush=True)
        try:
            result = await connector.run(query_context)
            if result.warning:
                print(f"⚠ {result.warning[:80]}")
                run.query_logs.append(SearchQueryLog(
                    connector_name=name,
                    source_kind=connector.metadata.source_kind,
                    query_used="[connector warning]",
                    status="warning",
                    notes=result.warning,
                    completed_at=datetime.now(timezone.utc),
                ))
            else:
                print(f"OK ({len(result.leads)} leads, {len(result.query_logs)} queries)")

            for ql in result.query_logs:
                run.query_logs.append(SearchQueryLog(
                    connector_name=ql["connector_name"],
                    source_kind=ql["source_kind"],
                    query_used=ql["query_used"],
                    status=ql.get("status", "completed"),
                    http_status=ql.get("http_status"),
                    result_count=ql.get("result_count", 0),
                    notes=ql.get("notes"),
                    completed_at=datetime.now(timezone.utc),
                ))

            collected_leads.extend(result.leads)

        except Exception as exc:
            print(f"FAILED: {exc}")
            connector_failures.append(f"{name}: {exc}")
            run.query_logs.append(SearchQueryLog(
                connector_name=name,
                source_kind=connector.metadata.source_kind,
                query_used="[connector invocation]",
                status="failed",
                notes=str(exc),
                completed_at=datetime.now(timezone.utc),
            ))

    # Deduplicate and score leads
    normalized_leads = merge_normalized_leads(collected_leads)

    _print_section(f"Scoring {len(normalized_leads)} deduplicated leads", "-")

    for normalized in normalized_leads:
        scored = score_lead(case, normalized)
        run.leads.append(Lead(
            case_id=case.id,
            lead_type=normalized.lead_type,
            category=normalized.category,
            source_kind=normalized.source_kind,
            source_name=normalized.source_name,
            source_url=normalized.source_url,
            query_used=normalized.query_used,
            title=normalized.title,
            summary=normalized.summary,
            content_excerpt=normalized.content_excerpt,
            published_at=normalized.published_at,
            found_at=normalized.found_at,
            location_text=normalized.location_text,
            latitude=normalized.latitude,
            longitude=normalized.longitude,
            confidence=scored.score,
            source_trust=normalized.source_trust,
            corroboration_count=normalized.corroboration_count,
            rationale=scored.rationale,
            human_reason="Generated by investigator-mode connectors.",
        ))

    # Finalize run
    if connector_failures and normalized_leads:
        run.status = "completed_with_warnings"
    elif connector_failures and not normalized_leads:
        run.status = "failed"
    else:
        run.status = "completed"

    run.inference_summary = (
        f"{len(normalized_leads)} deduplicated lead(s) from {len(collected_leads)} raw results "
        f"across {len(run.query_logs)} query log entries."
    )
    if connector_failures:
        run.error_message = " | ".join(connector_failures)
    run.completed_at = datetime.now(timezone.utc)
    session.commit()
    session.refresh(run)

    # Print results
    sorted_leads = sorted(run.leads, key=lambda l: l.confidence, reverse=True)

    _print_section("INVESTIGATION RESULTS")
    print(f"  Case: {case.name} (ID: {case.id})")
    print(f"  Status: {run.status}")
    print(f"  Total leads: {len(sorted_leads)}")
    print(f"  Query logs: {len(run.query_logs)}")
    if connector_failures:
        print(f"  Connector failures: {len(connector_failures)}")
        for f in connector_failures:
            print(f"    - {f}")

    # High-value leads
    high_value = [l for l in sorted_leads if l.confidence >= 0.3]
    medium_value = [l for l in sorted_leads if 0.15 <= l.confidence < 0.3]
    low_value = [l for l in sorted_leads if l.confidence < 0.15]

    if high_value:
        _print_section(f"HIGH-VALUE LEADS ({len(high_value)})", "*")
        for i, lead in enumerate(high_value, 1):
            _print_lead(i, lead)

    if medium_value:
        _print_section(f"MEDIUM-VALUE LEADS ({len(medium_value)})", "-")
        for i, lead in enumerate(medium_value, 1):
            _print_lead(i, lead)

    if low_value:
        _print_section(f"LOW-VALUE LEADS ({len(low_value)})", ".")
        for i, lead in enumerate(low_value[:5], 1):
            _print_lead(i, lead)
        if len(low_value) > 5:
            print(f"\n  ... and {len(low_value) - 5} more low-value leads")

    # ── MAAT Intelligence Synthesis ──────────────────────────────────
    _print_section("MAAT INTELLIGENCE SYNTHESIS")
    lead_dicts = [
        {
            "title": l.title,
            "confidence": l.confidence,
            "lead_type": l.lead_type,
            "category": l.category,
            "source_name": l.source_name,
            "source_url": l.source_url,
            "source_kind": l.source_kind,
            "published_at": isoformat(l.published_at),
            "found_at": isoformat(l.found_at),
            "location_text": l.location_text,
            "latitude": l.latitude,
            "longitude": l.longitude,
            "content_excerpt": l.content_excerpt,
            "rationale": l.rationale,
            "source_trust": l.source_trust,
            "corroboration_count": l.corroboration_count,
        }
        for l in sorted_leads
    ]

    case_dict = {
        "name": case.name,
        "age": case.age,
        "city": case.city,
        "province": case.province,
        "missing_since": isoformat(case.missing_since),
        "authority_name": case.authority_name,
        "authority_phone": case.authority_phone,
        "mcsc_email": case.mcsc_email,
        "mcsc_phone": case.mcsc_phone,
    }

    from dataclasses import asdict
    synthesis = synthesize_investigation(
        case_id=case.id,
        case_name=case.name or "",
        leads=lead_dicts,
        missing_since=case.missing_since,
        case_lat=case.latitude,
        case_lon=case.longitude,
        authority_name=case.authority_name,
        authority_phone=case.authority_phone,
    )
    synth_data = asdict(synthesis)

    # Print situation summary
    if synthesis.situation_summary:
        print(f"\n  SITUATION ASSESSMENT:")
        print(f"  {synthesis.situation_summary}")

    # Print key metrics
    print(f"\n  INTELLIGENCE METRICS:")
    print(f"    Lead clusters:  {len(synthesis.clusters)}")
    print(f"    Timeline events: {len(synthesis.timeline)}")
    print(f"    Geo patterns:   {len(synthesis.geographic_patterns)}")
    print(f"    Temporal patterns: {len(synthesis.temporal_patterns)}")
    print(f"    Recommendations: {len(synthesis.recommendations)}")

    # Print recommendations by priority
    if synthesis.recommendations:
        _print_section("MAAT RECOMMENDATIONS", "*")
        for rec in synthesis.recommendations:
            priority_label = {1: "CRITICAL", 2: "HIGH", 3: "MEDIUM"}.get(rec.priority, "LOW")
            print(f"\n  [{priority_label}] {rec.action}")
            print(f"    Rationale: {rec.rationale}")
            if rec.contact_info:
                print(f"    Contact: {rec.contact_info}")

    # Print lead clusters
    if synthesis.clusters:
        _print_section("LEAD CLUSTERS", "-")
        for i, cluster in enumerate(synthesis.clusters, 1):
            conf_bar = "█" * int(cluster.max_confidence * 20) + "░" * (20 - int(cluster.max_confidence * 20))
            print(f"\n  Cluster {i}: {cluster.theme.upper()}")
            print(f"    Leads: {len(cluster.lead_ids)} | Max conf: {cluster.max_confidence:.3f} [{conf_bar}]")
            print(f"    Sources: {', '.join(cluster.unique_sources[:5])}")
            if cluster.location_text:
                print(f"    Location: {cluster.location_text}")
            if cluster.date_range_start or cluster.date_range_end:
                print(f"    Date range: {cluster.date_range_start or '?'} → {cluster.date_range_end or '?'}")

    # Print geographic patterns
    if synthesis.geographic_patterns:
        _print_section("GEOGRAPHIC PATTERNS", "-")
        for pat in synthesis.geographic_patterns:
            sig = (pat.get('significance') or 'medium').upper()
            print(f"\n  [{sig}] {pat.get('type', 'pattern')}")
            print(f"    {pat.get('label', '')}")

    # Print temporal patterns
    if synthesis.temporal_patterns:
        _print_section("TEMPORAL PATTERNS", "-")
        for pat in synthesis.temporal_patterns:
            sig = (pat.get('significance') or 'medium').upper()
            print(f"\n  [{sig}] {pat.get('type', 'pattern')}")
            print(f"    {pat.get('label', '')}")

    # Print authority brief
    if synthesis.authority_brief:
        _print_section("AUTHORITY NOTIFICATION BRIEF", "=")
        print(f"\n{synthesis.authority_brief}")

    # ── MAAT Hypothesis Engine — Educated Guess ─────────────────────
    _print_section("MAAT HYPOTHESIS ENGINE — Analytical Conclusion", "=")

    from backend.enrichment.geospatial import build_geo_context
    geo_context = build_geo_context(case.latitude, case.longitude)

    hypothesis = generate_hypothesis(
        case_id=case.id,
        case_name=case.name or "",
        case_age=case.age,
        case_city=case.city,
        case_province=case.province,
        case_lat=case.latitude,
        case_lon=case.longitude,
        missing_since=case.missing_since,
        leads=lead_dicts,
        geo_context=geo_context,
    )
    hypothesis_data = {
        "primary_scenario": hypothesis.primary_scenario,
        "primary_scenario_confidence": hypothesis.primary_scenario_confidence,
        "confidence_level": hypothesis.confidence_level,
        "demographic_profile": hypothesis.demographic_profile,
        "behavioral_indicators": hypothesis.behavioral_indicators,
        "scenarios": [
            {
                "name": s.name,
                "weight": s.weight,
                "confidence": s.confidence,
                "evidence_for": s.evidence_for,
                "evidence_against": s.evidence_against,
            }
            for s in hypothesis.scenarios
        ],
        "geographic_assessment": {
            "probable_zone": hypothesis.geographic_assessment.probable_zone,
            "confidence": hypothesis.geographic_assessment.confidence,
            "nearby_infrastructure": hypothesis.geographic_assessment.nearby_infrastructure,
        },
        "conclusion": hypothesis.conclusion,
        "key_evidence_summary": hypothesis.key_evidence_summary,
        "recommended_search_areas": hypothesis.recommended_search_areas,
        "critical_actions": hypothesis.critical_actions,
        "data_quality_note": hypothesis.data_quality_note,
    }

    # Print demographic profile
    print(f"\n  DEMOGRAPHIC PROFILE:")
    print(f"  {hypothesis.demographic_profile}")
    print(f"\n  BEHAVIORAL INDICATORS:")
    for ind in hypothesis.behavioral_indicators:
        print(f"    - {ind}")

    # Print scenarios ranked by weight
    print(f"\n  SCENARIO ANALYSIS:")
    for i, scenario in enumerate(hypothesis.scenarios, 1):
        bar = "█" * int(scenario.weight * 20) + "░" * (20 - int(scenario.weight * 20))
        print(f"\n    {i}. {scenario.name} [{bar}] {scenario.weight:.2f} ({scenario.confidence})")
        if scenario.evidence_for:
            for ev in scenario.evidence_for:
                print(f"       + {ev}")
        if scenario.evidence_against:
            for ev in scenario.evidence_against:
                print(f"       - {ev}")

    # Print geographic assessment
    print(f"\n  GEOGRAPHIC PROBABILITY:")
    print(f"    {hypothesis.geographic_assessment.probable_zone}")
    if hypothesis.geographic_assessment.nearby_infrastructure:
        print(f"\n    Nearby infrastructure:")
        for infra in hypothesis.geographic_assessment.nearby_infrastructure:
            print(f"      - {infra}")

    # Print the conclusive educated guess
    _print_section("EDUCATED GUESS — Final Assessment", "*")
    print(f"\n{hypothesis.conclusion}")
    print(f"\n  Overall confidence: {hypothesis.confidence_level.upper()}")
    print(f"  Data quality: {hypothesis.data_quality_note}")

    if hypothesis.key_evidence_summary:
        print(f"\n  KEY EVIDENCE:")
        for ev in hypothesis.key_evidence_summary:
            print(f"    - {ev}")

    if hypothesis.recommended_search_areas:
        print(f"\n  RECOMMENDED SEARCH AREAS:")
        for area in hypothesis.recommended_search_areas:
            print(f"    - {area}")

    if hypothesis.critical_actions:
        print(f"\n  CRITICAL NEXT ACTIONS:")
        for action in hypothesis.critical_actions:
            print(f"    >> {action}")

    # Contact information
    _print_section("CONTACT FOR TIPS")
    if case.authority_name:
        print(f"\n  Investigating authority: {case.authority_name}")
    if case.authority_phone:
        print(f"  Authority phone: {case.authority_phone}")
    if case.mcsc_email:
        print(f"  MCSC tips email: {case.mcsc_email}")
    if case.mcsc_phone:
        print(f"  MCSC phone: {case.mcsc_phone}")

    # Export leads to JSON
    export_path = settings.data_dir / "investigations"
    export_path.mkdir(parents=True, exist_ok=True)
    export_file = export_path / f"case_{case.id}_run_{run.id}.json"

    export_data = {
        "case": {
            "id": case.id,
            "name": case.name,
            "age": case.age,
            "city": case.city,
            "province": case.province,
            "missing_since": isoformat(case.missing_since),
            "authority": case.authority_name,
            "authority_url": case.authority_case_url,
        },
        "run": {
            "id": run.id,
            "status": run.status,
            "connectors": run.connector_names,
            "total_leads": len(sorted_leads),
            "completed_at": isoformat(run.completed_at),
        },
        "leads": [
            {
                "rank": i + 1,
                "title": l.title,
                "confidence": l.confidence,
                "type": l.lead_type,
                "category": l.category,
                "source": l.source_name,
                "url": l.source_url,
                "published_at": isoformat(l.published_at),
                "location": l.location_text,
                "excerpt": l.content_excerpt[:300] if l.content_excerpt else None,
                "rationale": l.rationale,
            }
            for i, l in enumerate(sorted_leads)
        ],
        "synthesis": synth_data,
        "hypothesis": hypothesis_data,
    }

    with open(export_file, "w", encoding="utf-8") as f:
        json.dump(export_data, f, indent=2, default=str)
    print(f"\n  Leads exported to: {export_file}")

    return run


async def main() -> None:
    parser = argparse.ArgumentParser(description="Investigate missing person cases")
    parser.add_argument("--case-id", type=int, help="Specific case ID to investigate")
    parser.add_argument("--top", type=int, default=1, help="Number of newest cases to investigate")
    parser.add_argument("--skip-sync", action="store_true", help="Skip syncing cases from MCSC")
    args = parser.parse_args()

    _print_section("MAAT INTELLIGENCE PIPELINE — Truth from Chaos")
    print(f"  Investigator mode: {settings.enable_investigator_mode}")
    print(f"  Clear web connectors: {settings.enable_clear_web_connectors}")
    print(f"  Time: {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}")

    if not settings.enable_investigator_mode:
        print("\n  ERROR: ENABLE_INVESTIGATOR_MODE must be true in .env")
        sys.exit(1)

    init_db()

    with SessionLocal() as session:
        # Step 1: Sync cases
        if not args.skip_sync:
            _print_section("STEP 1: Syncing cases from MCSC ArcGIS")
            await sync_cases(session)
        else:
            print("\n  Skipping sync (--skip-sync)")

        # Step 2: Select cases to investigate
        if args.case_id:
            cases = [session.get(Case, args.case_id)]
            if not cases[0]:
                print(f"\n  ERROR: Case {args.case_id} not found in database")
                sys.exit(1)
        else:
            from sqlalchemy import select
            stmt = (
                select(Case)
                .where(Case.is_active.is_(True), Case.case_status == "open")
                .order_by(Case.missing_since.desc())
                .limit(args.top)
            )
            cases = list(session.scalars(stmt).all())

        if not cases:
            print("\n  No open cases found to investigate.")
            sys.exit(0)

        _print_section(f"STEP 2: Investigating {len(cases)} case(s)")
        for case in cases:
            print(f"  - {case.name} (ID: {case.id}, missing since: {case.missing_since})")

        # Step 3: Run investigations
        for case in cases:
            try:
                await investigate_case(session, case)
            except Exception as exc:
                print(f"\n  ERROR investigating case {case.id}: {exc}")
                import traceback
                traceback.print_exc()

    _print_section("PIPELINE COMPLETE")


if __name__ == "__main__":
    asyncio.run(main())
