from __future__ import annotations

import asyncio
import logging
import signal

from dotenv import load_dotenv

from app.config import load_settings
from app.csv_exporter import CSVExporter
from app.db import create_pool_with_retry


def configure_logging(level: str) -> None:
    logging.basicConfig(
        level=getattr(logging, level, logging.INFO),
        format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
    )


async def async_main() -> None:
    load_dotenv()
    settings = load_settings()
    configure_logging(settings.log_level)

    stop_event = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, stop_event.set)
        except NotImplementedError:
            pass

    pool = await create_pool_with_retry(
        settings.database_url,
        settings.database_connect_retry_seconds,
    )
    try:
        exporter = CSVExporter(
            pool=pool,
            export_dir=settings.csv_export_dir,
            interval_seconds=settings.csv_export_interval_seconds,
        )
        await exporter.run(stop_event)
    finally:
        await pool.close()


def main() -> None:
    asyncio.run(async_main())


if __name__ == "__main__":
    main()
