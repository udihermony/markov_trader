"""DESIGN.md §7: "APScheduler in the worker process. Daily wallet runs fire
after market close; each wallet is an independent job." """
from __future__ import annotations

import logging

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger

from backend.worker.wallet_runner import run_all_active_wallets

log = logging.getLogger(__name__)

DAILY_RUN_HOUR = 16
DAILY_RUN_MINUTE = 30
DAILY_RUN_TIMEZONE = "America/New_York"  # after US market close


def start_scheduler() -> BackgroundScheduler:
    scheduler = BackgroundScheduler()
    scheduler.add_job(
        run_all_active_wallets,
        CronTrigger(hour=DAILY_RUN_HOUR, minute=DAILY_RUN_MINUTE, timezone=DAILY_RUN_TIMEZONE),
        id="daily_wallet_runs",
        replace_existing=True,
    )
    scheduler.start()
    log.info(
        "scheduler started: daily_wallet_runs at %02d:%02d %s",
        DAILY_RUN_HOUR, DAILY_RUN_MINUTE, DAILY_RUN_TIMEZONE,
    )
    return scheduler
