from __future__ import annotations

import asyncio
import json
import logging
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from typing import Any

import asyncpg


LOGGER = logging.getLogger(__name__)


INSERT_TRADE_SQL = """
insert into hl_trades (
    dex,
    coin,
    trade_time,
    side,
    price,
    size,
    notional_usd,
    hash,
    tid,
    buyer_address,
    seller_address,
    raw_payload
) values (
    $1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12::jsonb
)
on conflict on constraint hl_trades_trade_key do nothing
"""


@dataclass(frozen=True, slots=True)
class TradeRecord:
    dex: str
    coin: str
    trade_time: datetime
    side: str
    price: Decimal
    size: Decimal
    notional_usd: Decimal
    trade_hash: str
    tid: int
    buyer_address: str
    seller_address: str
    raw_payload: dict[str, Any]


async def create_pool_with_retry(database_url: str, retry_seconds: float) -> asyncpg.Pool:
    while True:
        pool: asyncpg.Pool | None = None
        try:
            pool = await asyncpg.create_pool(dsn=database_url, min_size=1, max_size=5)
            async with pool.acquire() as conn:
                await conn.execute("select 1")
            LOGGER.info("Connected to PostgreSQL.")
            return pool
        except asyncio.CancelledError:
            if pool is not None:
                await pool.close()
            raise
        except Exception:
            if pool is not None:
                await pool.close()
            LOGGER.exception("PostgreSQL is not ready yet. Retrying in %.1f seconds.", retry_seconds)
            await asyncio.sleep(retry_seconds)


class TradeRepository:
    def __init__(self, pool: asyncpg.Pool) -> None:
        self._pool = pool

    async def insert_many(self, trades: list[TradeRecord]) -> int:
        if not trades:
            return 0

        rows = [
            (
                trade.dex,
                trade.coin,
                trade.trade_time,
                trade.side,
                trade.price,
                trade.size,
                trade.notional_usd,
                trade.trade_hash,
                trade.tid,
                trade.buyer_address,
                trade.seller_address,
                json.dumps(trade.raw_payload, separators=(",", ":")),
            )
            for trade in trades
        ]

        async with self._pool.acquire() as conn:
            await conn.executemany(INSERT_TRADE_SQL, rows)

        return len(rows)
