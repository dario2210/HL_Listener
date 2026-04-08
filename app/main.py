from __future__ import annotations

import asyncio
import logging
import signal

import aiohttp
from dotenv import load_dotenv

from app.config import load_settings
from app.db import TradeRepository, create_pool_with_retry
from app.hyperliquid import HyperliquidTradeListener


def configure_logging(level: str) -> None:
    logging.basicConfig(
        level=getattr(logging, level, logging.INFO),
        format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
    )


async def async_main() -> None:
    load_dotenv()
    settings = load_settings()
    configure_logging(settings.log_level)
    logger = logging.getLogger(__name__)

    stop_event = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, stop_event.set)
        except NotImplementedError:
            pass

    timeout = aiohttp.ClientTimeout(total=None, connect=30, sock_connect=30, sock_read=None)
    async with aiohttp.ClientSession(timeout=timeout) as session:
        pool = await create_pool_with_retry(
            settings.database_url,
            settings.database_connect_retry_seconds,
        )
        try:
            repository = TradeRepository(pool)
            listener = HyperliquidTradeListener(settings)

            async def handle_trades(trades):
                inserted = await repository.insert_many(trades)
                if inserted:
                    biggest = max(trade.notional_usd for trade in trades)
                    logger.info(
                        "Processed %d filtered trades. Largest notional in batch: %s USD",
                        inserted,
                        biggest,
                    )

            await listener.run(session, handle_trades, stop_event)
        finally:
            await pool.close()


def main() -> None:
    asyncio.run(async_main())


if __name__ == "__main__":
    main()
