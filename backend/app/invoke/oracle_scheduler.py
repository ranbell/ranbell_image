"""Internal scheduler for Daily Oracle — polls every 30 s, fires at configured HH:MM."""
from __future__ import annotations

import asyncio
import logging
import shutil
from datetime import datetime

from ..config import settings as _settings

logger = logging.getLogger(__name__)


async def run_oracle_scheduler(app) -> None:
    """Long-running background coroutine started at app startup.

    Checks every 30 s so a 60 s-aligned sleep can't miss the target minute.
    After submitting a job, sleeps 60 s to skip past the current minute and
    prevent double-submission — no stateful flag needed.
    Imports are inside the loop so startup import errors are caught and logged.
    """
    while True:
        await asyncio.sleep(30)
        try:
            from ..api.invoke import _oracle_date_str, _oracle_hm, _oracle_tz
            from ..jobs.runners import run_invoke_daily_oracle
            from ..runtime_config import get_runtime_config
            from ..spooler.models import JobLane

            cfg = await get_runtime_config(app.state.db)
            enabled = cfg.get("invoke_daily_oracle_enabled", False)
            tz = _oracle_tz(cfg)
            h, m = _oracle_hm(cfg)
            now = datetime.now(tz)
            logger.debug("[oracle_scheduler] tick: enabled=%s, now=%02d:%02d, target=%02d:%02d, tz=%s",
                         enabled, now.hour, now.minute, h, m, tz)
            if not enabled:
                continue

            if now.hour != h or now.minute != m:
                continue

            workflow = cfg.get("invoke_daily_oracle_workflow", "")
            if not workflow:
                logger.warning("[oracle_scheduler] no workflow configured, skipping")
                await asyncio.sleep(60)
                continue

            today = _oracle_date_str(cfg)
            existing = await app.state.db.get_daily_oracle(today)
            if existing:
                logger.info("[oracle_scheduler] already done for %s", today)
                await asyncio.sleep(60)
                continue

            min_free_gb = cfg.get("invoke_daily_oracle_min_free_gb", 5.0)
            free_gb = shutil.disk_usage(str(_settings.generated_images_dir)).free / 1024**3
            if free_gb < min_free_gb:
                logger.warning("[oracle_scheduler] low disk (%.1f GB free < %.1f GB threshold), skipping",
                               free_gb, min_free_gb)
                await asyncio.sleep(60)
                continue

            topic = cfg.get("invoke_daily_oracle_topic", "") or ""
            logger.info("[oracle_scheduler] firing for %s (topic=%r)", today, topic or "<auto>")
            app.state.spooler.submit(
                JobLane.SYNC,
                "invoke.daily_oracle",
                run_invoke_daily_oracle,
                meta={"daily_oracle_date": today},
                priority=-10,
                db=app.state.db,
                ollama=app.state.ollama,
                comfy=app.state.comfy,
                spooler=app.state.spooler,
                session_manager=app.state.invoke_session_manager,
                daily_oracle_date=today,
                workflow_name=workflow,
                topic=topic,
            )
            logger.info("[oracle_scheduler] job submitted for %s", today)
            await asyncio.sleep(60)  # skip past this minute

        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.exception("[oracle_scheduler] unexpected error: %s", exc)
