"""Worker process entrypoint: `python -m backend.worker.main`."""
from __future__ import annotations

import logging
import time

from backend.worker.scheduler import start_scheduler

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")


def main() -> None:
    scheduler = start_scheduler()
    try:
        while True:
            time.sleep(60)
    except KeyboardInterrupt:
        scheduler.shutdown()


if __name__ == "__main__":
    main()
