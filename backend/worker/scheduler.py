"""DESIGN.md §7: "APScheduler in the worker process. Daily wallet runs fire
after market close; each wallet is an independent job." M9 adds a second,
much more frequent job here: polling the Postgres job queue
(backend/worker/jobs.py) for unattended AI sessions to run."""
from __future__ import annotations

import logging

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.interval import IntervalTrigger

from backend.worker.jobs import poll_jobs
from backend.worker.wallet_runner import run_all_active_wallets

log = logging.getLogger(__name__)

DAILY_RUN_HOUR = 16
DAILY_RUN_MINUTE = 30
DAILY_RUN_TIMEZONE = "America/New_York"  # after US market close

JOB_POLL_INTERVAL_SECONDS = 10


def start_scheduler() -> BackgroundScheduler:
    scheduler = BackgroundScheduler()
    scheduler.add_job(
        run_all_active_wallets,
        CronTrigger(hour=DAILY_RUN_HOUR, minute=DAILY_RUN_MINUTE, timezone=DAILY_RUN_TIMEZONE),
        id="daily_wallet_runs",
        replace_existing=True,
    )
    scheduler.add_job(
        poll_jobs,
        IntervalTrigger(seconds=JOB_POLL_INTERVAL_SECONDS),
        id="poll_jobs",
        replace_existing=True,
        max_instances=1,  # one job runs to completion before the next poll tick starts another
    )
    scheduler.start()
    log.info(
        "scheduler started: daily_wallet_runs at %02d:%02d %s, poll_jobs every %ds",
        DAILY_RUN_HOUR, DAILY_RUN_MINUTE, DAILY_RUN_TIMEZONE, JOB_POLL_INTERVAL_SECONDS,
    )
    return scheduler
