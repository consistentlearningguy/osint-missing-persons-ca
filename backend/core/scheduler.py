"""Optional background scheduler for sync and export jobs."""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone

try:
    from apscheduler.schedulers.background import BackgroundScheduler
except ModuleNotFoundError:  # pragma: no cover - import fallback
    BackgroundScheduler = None

from backend.core.config import settings
from backend.core.database import SessionLocal
from backend.services.case_service import CaseService
from backend.services.export_service import ExportService

scheduler = BackgroundScheduler(timezone="UTC") if BackgroundScheduler else None


def _run_coroutine(coro):
    asyncio.run(coro)


def scheduled_sync_and_export() -> None:
    with SessionLocal() as session:
        _run_coroutine(CaseService(session).sync_from_mcsc())
        ExportService(session).write_public_export(settings.public_export_path)


def start_scheduler() -> None:
    if scheduler is None or scheduler.running:
        return
    scheduler.add_job(
        scheduled_sync_and_export,
        trigger="interval",
        minutes=settings.sync_interval_minutes,
        id="sync_and_export",
        next_run_time=datetime.now(timezone.utc),
        replace_existing=True,
    )
    scheduler.start()


def stop_scheduler() -> None:
    if scheduler is None or not scheduler.running:
        return
    scheduler.shutdown(wait=False)
