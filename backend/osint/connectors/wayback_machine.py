"""Wayback Machine CDX connector — checks Internet Archive for archived pages.

Uses the public CDX API to find archived snapshots of official missing-person
pages, news articles, and social media posts. No API key required.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Callable

import httpx

from backend.core.config import settings
from backend.osint.connectors.base import ConnectorMetadata, rate_limit_sleep
from backend.osint.normalization.models import ConnectorRunResult, NormalizedLead, QueryContext


def _parse_wayback_timestamp(ts: str) -> datetime | None:
    """Parse a Wayback Machine timestamp (YYYYMMDDHHmmss) into datetime."""
    try:
        return datetime.strptime(ts, "%Y%m%d%H%M%S").replace(tzinfo=timezone.utc)
    except (ValueError, TypeError):
        return None


class WaybackMachineConnector:
    """Check the Internet Archive for archived pages related to missing persons."""

    metadata = ConnectorMetadata(
        name="wayback-machine",
        source_kind="clear-web",
        disabled_by_default=True,
        description="Search the Internet Archive's Wayback Machine for archived evidence.",
    )

    def __init__(self, client_factory: Callable[[float], Any] | None = None) -> None:
        self.client_factory = client_factory

    def enabled(self) -> bool:
        return bool(settings.enable_clear_web_connectors)

    async def run(self, context: QueryContext) -> ConnectorRunResult:
        if not self.enabled():
            return ConnectorRunResult(warning="Wayback Machine connector disabled by configuration.")

        # Build URL patterns to check
        urls_to_check: list[tuple[str, str]] = []

        # Build name-based domain searches (CDX works best with domain patterns)
        name = (context.name or "").strip()
        name_slug = name.lower().replace(" ", "").replace("-", "")
        name_hyphen = name.lower().replace(" ", "-")

        if name:
            # Search canadasmissing.ca and missingkids.ca for the person
            urls_to_check.append(
                (f"canadasmissing.ca/pubs/*{name_hyphen}*", "rcmp-missing-db")
            )
            urls_to_check.append(
                (f"missingkids.ca/*{name_hyphen}*", "cccp-missing-db")
            )

        # Check specific official URLs if they are simple enough for CDX
        if context.authority_case_url:
            url = context.authority_case_url
            # Only check URLs that are actual web pages, not API endpoints
            if not any(skip in url for skip in ["arcgis.com", "FeatureServer", "/rest/services/"]):
                urls_to_check.append((url, "official-case-page"))

        if not urls_to_check:
            return ConnectorRunResult(warning="No URLs to check against the Wayback Machine.")

        leads: list[NormalizedLead] = []
        query_logs: list[dict[str, object]] = []
        seen_urls: set[str] = set()
        factory = self.client_factory or (lambda timeout: httpx.AsyncClient(timeout=timeout, follow_redirects=True))

        async with factory(settings.connector_timeout_seconds) as client:
            for check_url, url_type in urls_to_check[:6]:
                try:
                    params: dict[str, str] = {
                        "url": check_url,
                        "output": "json",
                        "limit": "10",
                        "filter": "statuscode:200",
                        "fl": "timestamp,original,mimetype,statuscode",
                        "sort": "reverse",
                    }
                    response = await client.get(
                        "https://web.archive.org/cdx/search/cdx",
                        params=params,
                        headers={
                            "User-Agent": "maat-intelligence/2.0 (research)",
                        },
                    )
                    response.raise_for_status()
                    data = response.json()
                except Exception as exc:
                    query_logs.append({
                        "connector_name": self.metadata.name,
                        "source_kind": self.metadata.source_kind,
                        "query_used": check_url,
                        "status": "failed",
                        "http_status": getattr(getattr(exc, "response", None), "status_code", None),
                        "result_count": 0,
                        "notes": f"Wayback Machine CDX query failed: {exc}",
                    })
                    continue

                await rate_limit_sleep()

                # Skip header row
                rows = data[1:] if len(data) > 1 else []
                added = 0

                for row in rows:
                    if len(row) < 4:
                        continue
                    timestamp, original_url, mimetype, statuscode = row[:4]

                    wayback_url = f"https://web.archive.org/web/{timestamp}/{original_url}"
                    if wayback_url in seen_urls:
                        continue
                    seen_urls.add(wayback_url)
                    added += 1

                    published_at = _parse_wayback_timestamp(timestamp)

                    # Determine trust based on URL type
                    trust = 0.50
                    if url_type == "official-case-page":
                        trust = 0.70
                    elif url_type in ("rcmp-missing-db", "cccp-missing-db"):
                        trust = 0.65
                    elif url_type == "advocacy-site":
                        trust = 0.55

                    leads.append(
                        NormalizedLead(
                            connector_name=self.metadata.name,
                            source_kind=self.metadata.source_kind,
                            lead_type="archived-page",
                            category="archive-evidence",
                            source_name="Internet Archive",
                            source_url=wayback_url,
                            query_used=check_url,
                            found_at=datetime.now(timezone.utc),
                            published_at=published_at,
                            title=f"Archived snapshot of {url_type}: {original_url[:80]}",
                            summary=f"Wayback Machine snapshot from {timestamp[:8]} of {original_url}",
                            content_excerpt=f"Archived {mimetype} page captured on {timestamp[:8]}. "
                                          f"Original URL: {original_url}",
                            location_text=context.city or context.province,
                            source_trust=trust,
                            rationale=[
                                f"Archived evidence from Internet Archive ({url_type}).",
                                f"Snapshot captured: {timestamp[:8]}",
                                "Archived pages provide historical evidence even if originals are removed.",
                            ],
                        )
                    )

                query_logs.append({
                    "connector_name": self.metadata.name,
                    "source_kind": self.metadata.source_kind,
                    "query_used": check_url,
                    "status": "completed",
                    "http_status": response.status_code,
                    "result_count": added,
                    "notes": f"Wayback Machine found {len(rows)} snapshots, {added} new.",
                })

        if not leads:
            return ConnectorRunResult(
                warning="No archived pages found in the Wayback Machine for this case.",
                query_logs=query_logs,
            )

        return ConnectorRunResult(leads=leads, query_logs=query_logs)
