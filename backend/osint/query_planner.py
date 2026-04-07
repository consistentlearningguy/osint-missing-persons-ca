"""Bounded public-query planning for lawful connector searches."""

from __future__ import annotations

from backend.osint.normalization.models import QueryContext


def _clean_part(value: str | None) -> str:
    return " ".join(str(value or "").split())


def _push_query(queries: list[str], seen: set[str], query: str) -> None:
    normalized = " ".join(query.split())
    if not normalized or normalized in seen:
        return
    seen.add(normalized)
    queries.append(normalized)


def _context_names(context: QueryContext, limit: int = 3) -> list[str]:
    names: list[str] = []
    seen: set[str] = set()

    for value in [context.name, *context.aliases]:
        cleaned = _clean_part(value)
        key = cleaned.lower()
        if not cleaned or key in seen:
            continue
        seen.add(key)
        names.append(cleaned)
        if len(names) >= limit:
            break

    return names


def _date_markers(context: QueryContext) -> list[str]:
    if context.missing_since is None:
        return []

    missing_since = context.missing_since
    markers: list[str] = []
    seen: set[str] = set()
    for marker in (
        missing_since.strftime("%Y"),
        missing_since.strftime("%B %Y"),
        missing_since.strftime("%b %Y"),
        missing_since.strftime("%Y-%m-%d"),
    ):
        _push_query(markers, seen, marker)
    return markers


def build_public_query_plan(context: QueryContext, limit: int = 10) -> list[str]:
    """Build a small, reviewable set of public-search queries.

    The planner deliberately stays bounded and only uses high-level official facts
    already present in the case record.
    """

    name = _clean_part(context.name)
    city = _clean_part(context.city)
    province = _clean_part(context.province)
    location_text = _clean_part(context.location_text)
    aliases = []
    for alias in context.aliases:
        cleaned = _clean_part(alias)
        if cleaned:
            aliases.append(cleaned)

    if not name:
        return []

    queries: list[str] = []
    seen: set[str] = set()

    _push_query(queries, seen, f'"{name}"')
    _push_query(queries, seen, f'"{name}" missing')
    _push_query(queries, seen, f'"{name}" "last seen"')
    if location_text and location_text.lower() not in {city.lower(), province.lower()}:
        _push_query(queries, seen, f'"{name}" "{location_text}"')
    if city:
        _push_query(queries, seen, f'"{name}" "{city}"')
        _push_query(queries, seen, f'"{name}" missing "{city}"')
    if province:
        _push_query(queries, seen, f'"{name}" "{province}"')
    if city and province:
        _push_query(queries, seen, f'"{name}" "{city}" "{province}"')

    if aliases:
        first_alias = aliases[0]
        _push_query(queries, seen, f'"{first_alias}"')
        if city:
            _push_query(queries, seen, f'"{first_alias}" "{city}"')

    if context.age is not None:
        _push_query(queries, seen, f'"{name}" {context.age}')
    if context.age is not None and city:
        _push_query(queries, seen, f'"{name}" {context.age} "{city}"')

    for alias in aliases[1:]:
        _push_query(queries, seen, f'"{alias}"')
        if city:
            _push_query(queries, seen, f'"{alias}" "{city}"')
        if province:
            _push_query(queries, seen, f'"{alias}" "{province}"')
        if city and province:
            _push_query(queries, seen, f'"{alias}" "{city}" "{province}"')
        if len(queries) >= limit:
            break

    return queries[:limit]


def build_trace_labs_query_groups(context: QueryContext) -> list[dict[str, object]]:
    """Build grouped, passive investigator queries inspired by Trace Labs workflows."""

    names = _context_names(context)
    if not names:
        return []

    primary_name = names[0]
    city = _clean_part(context.city)
    province = _clean_part(context.province)
    location_text = _clean_part(context.location_text)
    date_markers = _date_markers(context)

    groups: list[dict[str, object]] = []

    general_queries = build_public_query_plan(context, limit=6)
    if general_queries:
        groups.append(
            {
                "slug": "general-sweep",
                "title": "General Name Sweep",
                "trace_labs_category": "Basic Subject Info",
                "summary": "Baseline public-web sweep for names, aliases, city, province, and age pivots.",
                "queries": general_queries,
            }
        )

    social_queries: list[str] = []
    social_seen: set[str] = set()
    for name in names[:2]:
        for domain in (
            "instagram.com",
            "tiktok.com",
            "facebook.com",
            "reddit.com",
            "linkedin.com",
            "youtube.com",
            "github.com",
        ):
            _push_query(social_queries, social_seen, f'site:{domain} "{name}"')
            if city:
                _push_query(social_queries, social_seen, f'site:{domain} "{name}" "{city}"')
            if len(social_queries) >= 10:
                break
        if len(social_queries) >= 10:
            break
    if social_queries:
        groups.append(
            {
                "slug": "social-profile-sweep",
                "title": "Social Profile Sweep",
                "trace_labs_category": "Basic Subject Info",
                "summary": "Site-scoped searches for public profiles, handles, and posts across major platforms.",
                "queries": social_queries,
            }
        )

    network_queries: list[str] = []
    network_seen: set[str] = set()
    for term in ("family", "friend", "mother", "father", "sister", "brother"):
        _push_query(network_queries, network_seen, f'"{primary_name}" "{term}"')
        if city:
            _push_query(network_queries, network_seen, f'"{primary_name}" "{term}" "{city}"')
        if len(network_queries) >= 6:
            break
    if network_queries:
        groups.append(
            {
                "slug": "family-friends",
                "title": "Family And Friends Pivot",
                "trace_labs_category": "Family / Friends",
                "summary": "Public relationship pivots that can surface relatives, close contacts, and corroborating posts.",
                "queries": network_queries,
            }
        )

    employment_queries: list[str] = []
    employment_seen: set[str] = set()
    for term in ("school", "college", "university", "employer", "work", "linkedin"):
        _push_query(employment_queries, employment_seen, f'"{primary_name}" "{term}"')
        if city:
            _push_query(employment_queries, employment_seen, f'"{primary_name}" "{term}" "{city}"')
        if province:
            _push_query(employment_queries, employment_seen, f'"{primary_name}" "{term}" "{province}"')
        if len(employment_queries) >= 6:
            break
    if employment_queries:
        groups.append(
            {
                "slug": "employment-school",
                "title": "Employment And School Pivot",
                "trace_labs_category": "Employment",
                "summary": "Searches for school, work, employer, and LinkedIn traces tied to the case.",
                "queries": employment_queries,
            }
        )

    timeline_queries: list[str] = []
    timeline_seen: set[str] = set()
    for marker in date_markers[:3]:
        _push_query(timeline_queries, timeline_seen, f'"{primary_name}" "{marker}"')
        if location_text:
            _push_query(timeline_queries, timeline_seen, f'"{primary_name}" "{location_text}" "{marker}"')
        if city:
            _push_query(timeline_queries, timeline_seen, f'"{primary_name}" "{city}" "{marker}"')
        if province:
            _push_query(timeline_queries, timeline_seen, f'"{primary_name}" "{province}" "{marker}"')
        if len(timeline_queries) >= 6:
            break
    if timeline_queries:
        groups.append(
            {
                "slug": "timeline-advancement",
                "title": "Timeline Advancement",
                "trace_labs_category": "Day Last Seen / Advancing The Timeline",
                "summary": "Date-bounded pivots for last-seen context and post-missing activity.",
                "queries": timeline_queries,
            }
        )

    return groups


def build_news_query_plan(context: QueryContext, limit: int = 8) -> list[str]:
    """Build a bounded set of news-focused queries for timeline/news connectors."""

    names = _context_names(context, limit=2)
    if not names:
        return []

    primary_name = names[0]
    city = _clean_part(context.city)
    province = _clean_part(context.province)
    location_text = _clean_part(context.location_text)
    date_markers = _date_markers(context)

    queries: list[str] = []
    seen: set[str] = set()

    _push_query(queries, seen, f'"{primary_name}" missing')
    _push_query(queries, seen, f'"{primary_name}" "last seen"')
    if location_text:
        _push_query(queries, seen, f'"{primary_name}" "{location_text}"')
    if city:
        _push_query(queries, seen, f'"{primary_name}" missing "{city}"')
        _push_query(queries, seen, f'"{primary_name}" "last seen" "{city}"')
    for marker in date_markers[:1]:
        _push_query(queries, seen, f'"{primary_name}" "{marker}"')
        if location_text:
            _push_query(queries, seen, f'"{primary_name}" "{location_text}" "{marker}"')
        if city:
            _push_query(queries, seen, f'"{primary_name}" "{city}" "{marker}"')
    if province:
        _push_query(queries, seen, f'"{primary_name}" missing "{province}"')

    for alias in names[1:]:
        _push_query(queries, seen, f'"{alias}" missing')
        if city:
            _push_query(queries, seen, f'"{alias}" "{city}"')

    for marker in date_markers[:2]:
        _push_query(queries, seen, f'"{primary_name}" "{marker}"')
        if city:
            _push_query(queries, seen, f'"{primary_name}" "{city}" "{marker}"')
        if len(queries) >= limit:
            break

    return queries[:limit]


def build_investigator_query_plan(context: QueryContext, limit: int = 18) -> list[str]:
    """Flatten grouped Trace Labs-style queries into a bounded automation-safe plan."""

    grouped = {
        group["slug"]: list(group.get("queries", []))
        for group in build_trace_labs_query_groups(context)
    }
    order = (
        "general-sweep",
        "social-profile-sweep",
        "timeline-advancement",
        "employment-school",
        "family-friends",
    )
    positions = {slug: 0 for slug in order}

    queries: list[str] = []
    seen: set[str] = set()
    while len(queries) < limit:
        progressed = False
        for slug in order:
            current_group = grouped.get(slug, [])
            index = positions[slug]
            if index >= len(current_group):
                continue
            progressed = True
            positions[slug] += 1
            _push_query(queries, seen, current_group[index])
            if len(queries) >= limit:
                return queries

        if not progressed:
            break

    return queries[:limit]
