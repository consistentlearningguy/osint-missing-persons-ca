"""API routes for OSINT investigations and leads.

The API endpoint creates the Investigation record upfront (so it can return
the ID immediately), then hands off to investigate.run_investigation() which
is the SINGLE orchestrator — no duplicate code paths.
"""

import asyncio
from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query
from loguru import logger
from pydantic import BaseModel
from sqlalchemy import desc
from sqlalchemy.orm import Session

from backend.core.database import get_db, SessionLocal
from backend.models.case import MissingCase
from backend.models.investigation import Investigation, Lead
from backend.analysis.investigate import run_investigation

router = APIRouter(prefix="/api/investigations", tags=["investigations"])


# --- Track running investigations to prevent duplicates ---
_running_investigations: set[int] = set()  # case_objectids currently being investigated
_MAX_CONCURRENT_INVESTIGATIONS = 3


def cleanup_stale_investigations(db: Session) -> int:
    """Mark stale 'running' investigations as 'failed' on startup.

    If the server crashed or was restarted while investigations were running,
    those records are stuck in 'running' forever. This cleans them up.

    Returns the number of stale investigations cleaned up.
    """
    stale = (
        db.query(Investigation)
        .filter(Investigation.status == "running")
        .all()
    )
    count = 0
    for inv in stale:
        inv.status = "failed"  # type: ignore[assignment]
        inv.completed_at = datetime.now(timezone.utc)  # type: ignore[assignment]
        inv.error_message = "Server restarted while investigation was running"  # type: ignore[assignment]
        count += 1

    if count > 0:
        db.commit()
        logger.warning(f"Cleaned up {count} stale 'running' investigation(s)")

    return count


@router.post("/{case_objectid}")
async def start_investigation(
    case_objectid: int,
    background_tasks: BackgroundTasks,
    run_usernames: bool = Query(True, description="Run username enumeration"),
    run_web: bool = Query(True, description="Run web mention scanning"),
    run_faces: bool = Query(True, description="Run face recognition"),
    db: Session = Depends(get_db),
):
    """Trigger an OSINT investigation for a case.

    This starts the investigation in the background and returns immediately
    with the investigation ID. Poll GET /api/investigations/{case_objectid}
    to check status.
    """
    # Rate limit concurrent investigations
    if len(_running_investigations) >= _MAX_CONCURRENT_INVESTIGATIONS:
        raise HTTPException(
            status_code=429,
            detail=f"Too many concurrent investigations (max {_MAX_CONCURRENT_INVESTIGATIONS}). "
                   "Please wait for one to complete.",
        )

    # Check case exists
    case = db.query(MissingCase).filter(MissingCase.objectid == case_objectid).first()
    if not case:
        raise HTTPException(status_code=404, detail="Case not found")

    if not case.name:
        raise HTTPException(status_code=400, detail="Case has no name — cannot investigate")

    # Check if already running
    if case_objectid in _running_investigations:
        raise HTTPException(
            status_code=409,
            detail="Investigation already running for this case"
        )

    # Create investigation record immediately so we can return its ID
    investigation = Investigation(
        case_objectid=case_objectid,
        status="running",
        ran_username_search=run_usernames,
        ran_web_mentions=run_web,
        ran_face_search=run_faces,
    )
    db.add(investigation)
    db.commit()
    db.refresh(investigation)

    # Run in background
    _running_investigations.add(case_objectid)
    background_tasks.add_task(
        _run_investigation_background,
        investigation_id=investigation.id,
        case_objectid=case_objectid,
        run_usernames=run_usernames,
        run_web=run_web,
        run_faces=run_faces,
    )

    return {
        "investigation_id": investigation.id,
        "status": "running",
        "message": f"Investigation started for {case.name}",
    }


async def _run_investigation_background(
    investigation_id: int,
    case_objectid: int,
    run_usernames: bool,
    run_web: bool,
    run_faces: bool,
):
    """Background task that delegates to the single orchestrator.

    We create a new DB session here because the FastAPI request session
    will be closed by the time this runs.
    """
    db = SessionLocal()
    try:
        # Delegate to the SINGLE orchestrator in investigate.py
        # Pass the pre-created investigation_id so it doesn't create a duplicate
        await run_investigation(
            case_objectid=case_objectid,
            db=db,
            investigation_id=investigation_id,
            run_usernames=run_usernames,
            run_web=run_web,
            run_faces=run_faces,
        )
    except Exception as e:
        logger.error(f"Background investigation {investigation_id} failed: {e}")
        # run_investigation already marks the investigation as "failed" in its
        # except block, so we only need to handle truly unexpected errors here
        try:
            investigation = db.query(Investigation).filter(Investigation.id == investigation_id).first()
            if investigation and investigation.status != "failed":
                investigation.status = "failed"  # type: ignore[assignment]
                investigation.completed_at = datetime.now(timezone.utc)  # type: ignore[assignment]
                investigation.error_message = str(e)[:500]  # type: ignore[assignment]
                db.commit()
        except Exception:
            pass
    finally:
        _running_investigations.discard(case_objectid)
        db.close()


@router.get("/{case_objectid}")
def get_investigations(
    case_objectid: int,
    db: Session = Depends(get_db),
):
    """Get all investigations for a case, most recent first."""
    investigations = (
        db.query(Investigation)
        .filter(Investigation.case_objectid == case_objectid)
        .order_by(desc(Investigation.started_at))
        .all()
    )

    return {
        "case_objectid": case_objectid,
        "investigations": [inv.to_dict() for inv in investigations],
        "is_running": case_objectid in _running_investigations,
    }


@router.get("/{case_objectid}/leads")
def get_leads(
    case_objectid: int,
    lead_type: Optional[str] = Query(None, description="Filter by lead type"),
    min_confidence: Optional[float] = Query(None, description="Minimum confidence (0.0-1.0)"),
    reviewed: Optional[bool] = Query(None, description="Filter by review status"),
    sort_by: str = Query("confidence", description="Sort field (confidence, found_at)"),
    order: str = Query("desc", description="Sort order"),
    limit: int = Query(100, ge=1, le=500),
    offset: int = Query(0, ge=0),
    db: Session = Depends(get_db),
):
    """Get all leads for a case across all investigations."""
    query = db.query(Lead).filter(Lead.case_objectid == case_objectid)

    if lead_type:
        query = query.filter(Lead.lead_type == lead_type)
    if min_confidence is not None:
        query = query.filter(Lead.confidence >= min_confidence)
    if reviewed is not None:
        query = query.filter(Lead.reviewed == reviewed)

    total = query.count()

    # Sorting
    if sort_by == "confidence":
        sort_col = Lead.confidence
    elif sort_by == "found_at":
        sort_col = Lead.found_at
    elif sort_by == "content_date":
        sort_col = Lead.content_date
    else:
        sort_col = Lead.confidence

    if order.lower() == "asc":
        query = query.order_by(sort_col.asc())
    else:
        query = query.order_by(sort_col.desc())

    leads = query.offset(offset).limit(limit).all()

    return {
        "case_objectid": case_objectid,
        "total": total,
        "leads": [lead.to_dict() for lead in leads],
    }


class LeadReviewBody(BaseModel):
    reviewed: bool
    is_actionable: Optional[bool] = None
    review_notes: Optional[str] = None


@router.patch("/leads/{lead_id}")
def review_lead(
    lead_id: int,
    body: LeadReviewBody,
    db: Session = Depends(get_db),
):
    """Review a lead — mark it as reviewed, actionable or noise, with notes."""
    lead = db.query(Lead).filter(Lead.id == lead_id).first()
    if not lead:
        raise HTTPException(status_code=404, detail="Lead not found")

    lead.reviewed = body.reviewed  # type: ignore[assignment]
    if body.is_actionable is not None:
        lead.is_actionable = body.is_actionable  # type: ignore[assignment]
    if body.review_notes is not None:
        lead.review_notes = body.review_notes  # type: ignore[assignment]

    db.commit()
    db.refresh(lead)

    return lead.to_dict()
