"""Daily backup scheduler — polls every 30 s, fires at the configured HH:MM.

Same shape as the Daily Oracle scheduler: a 30 s tick so a minute is never
missed, then a 60 s sleep after firing so the same minute cannot fire twice.
Imports live inside the loop so an import error is logged rather than killing
startup.
"""
from __future__ import annotations

import asyncio
import logging
from datetime import datetime

logger = logging.getLogger(__name__)


async def run_backup_scheduler(app) -> None:
    while True:
        await asyncio.sleep(30)
        try:
            from ..api.invoke import _oracle_tz
            from ..jobs.runners import run_backup
            from ..runtime_config import get_runtime_config
            from ..spooler.models import JobLane

            cfg = await get_runtime_config(app.state.db)
            if not cfg.get("backup_enabled", True):
                continue

            tz = _oracle_tz({"invoke_daily_oracle_timezone": cfg.get("backup_timezone", "")})
            raw = str(cfg.get("backup_time", "04:30") or "04:30")
            try:
                h, m = (int(x) for x in raw.split(":", 1))
            except ValueError:
                logger.warning("[backup_scheduler] bad backup_time %r, using 04:30", raw)
                h, m = 4, 30
            now = datetime.now(tz)
            if now.hour != h or now.minute != m:
                continue

            app.state.spooler.submit(
                JobLane.SYNC, "backup", run_backup,
                priority=-10,
                db=app.state.db,
            )
            logger.info("[backup_scheduler] submitted daily backup")
            await asyncio.sleep(60)  # skip past this minute

        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.exception("[backup_scheduler] unexpected error: %s", exc)
