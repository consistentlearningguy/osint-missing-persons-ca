from datetime import datetime, timezone

from backend.osint.normalization.models import QueryContext
from backend.osint.query_planner import (
    build_investigator_query_plan,
    build_news_query_plan,
    build_public_query_plan,
    build_trace_labs_query_groups,
)


def test_build_public_query_plan_is_bounded_and_deduplicated():
    context = QueryContext(
        case_id=1,
        name="Sample Case Toronto",
        aliases=["SCT", "Sample Case Toronto"],
        city="Toronto",
        province="Ontario",
        age=14,
        missing_since=datetime.now(timezone.utc),
        location_text="Wynford Dr & Concorde Pl, Toronto, ON",
    )

    queries = build_public_query_plan(context, limit=10)

    assert queries[0] == '"Sample Case Toronto"'
    assert any('"Wynford Dr & Concorde Pl, Toronto, ON"' in query for query in queries)
    assert any('"Toronto"' in query for query in queries)
    assert any('"Ontario"' in query for query in queries)
    assert any('"SCT"' in query for query in queries)
    assert len(queries) <= 10
    assert len(set(queries)) == len(queries)


def test_build_trace_labs_query_groups_cover_social_employment_and_timeline():
    context = QueryContext(
        case_id=1,
        name="Sample Case Toronto",
        aliases=["SCT", "CaseTO"],
        city="Toronto",
        province="Ontario",
        age=14,
        missing_since=datetime(2026, 3, 14, tzinfo=timezone.utc),
        location_text="Wynford Dr & Concorde Pl, Toronto, ON",
    )

    groups = build_trace_labs_query_groups(context)
    slugs = {group["slug"] for group in groups}

    assert "general-sweep" in slugs
    assert "social-profile-sweep" in slugs
    assert "employment-school" in slugs
    assert "timeline-advancement" in slugs

    social_group = next(group for group in groups if group["slug"] == "social-profile-sweep")
    assert any("site:instagram.com" in query for query in social_group["queries"])
    assert any('"Toronto"' in query for query in social_group["queries"])
    timeline_group = next(group for group in groups if group["slug"] == "timeline-advancement")
    assert any('"Wynford Dr & Concorde Pl, Toronto, ON"' in query for query in timeline_group["queries"])


def test_build_investigator_query_plan_is_bounded_and_includes_trace_labs_style_pivots():
    context = QueryContext(
        case_id=1,
        name="Sample Case Toronto",
        aliases=["SCT"],
        city="Toronto",
        province="Ontario",
        age=14,
        missing_since=datetime(2026, 3, 14, tzinfo=timezone.utc),
        location_text="Wynford Dr & Concorde Pl, Toronto, ON",
    )

    queries = build_investigator_query_plan(context, limit=10)

    assert len(queries) <= 10
    assert len(set(queries)) == len(queries)
    assert any("site:instagram.com" in query or "site:tiktok.com" in query for query in queries)
    assert any('"2026"' in query for query in queries)


def test_build_news_query_plan_stays_news_focused_and_bounded():
    context = QueryContext(
        case_id=1,
        name="Sample Case Toronto",
        aliases=["SCT"],
        city="Toronto",
        province="Ontario",
        age=14,
        missing_since=datetime(2026, 3, 14, tzinfo=timezone.utc),
        location_text="Wynford Dr & Concorde Pl, Toronto, ON",
    )

    queries = build_news_query_plan(context, limit=6)

    assert len(queries) <= 6
    assert len(set(queries)) == len(queries)
    assert all("site:" not in query for query in queries)
    assert any("missing" in query or "last seen" in query for query in queries)
    assert any('"Wynford Dr & Concorde Pl, Toronto, ON"' in query for query in queries)
    assert any('"2026"' in query for query in queries)
