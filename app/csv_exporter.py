from __future__ import annotations

import asyncio
import csv
import logging
import os
from datetime import datetime
from pathlib import Path
from typing import Any

import asyncpg


LOGGER = logging.getLogger(__name__)

TRADE_HEADERS = [
    "id",
    "venue",
    "dex",
    "coin",
    "trade_time",
    "ingest_time",
    "side",
    "price",
    "size",
    "notional_usd",
    "hash",
    "tid",
    "buyer_address",
    "seller_address",
]

WALLET_EVENT_HEADERS = [
    "trade_row_id",
    "venue",
    "dex",
    "coin",
    "trade_time",
    "ingest_time",
    "side",
    "price",
    "size",
    "notional_usd",
    "hash",
    "tid",
    "wallet_role",
    "wallet_address",
]

TRADE_EXPORT_SQL = """
select
    id,
    venue,
    dex,
    coin,
    trade_time,
    ingest_time,
    side,
    price,
    size,
    notional_usd,
    hash,
    tid,
    buyer_address,
    seller_address
from hl_trades
order by id
"""

WALLET_EVENT_EXPORT_SQL = """
select
    trade_row_id,
    venue,
    dex,
    coin,
    trade_time,
    ingest_time,
    side,
    price,
    size,
    notional_usd,
    hash,
    tid,
    wallet_role,
    wallet_address
from wallet_trade_events
order by trade_row_id, wallet_role
"""


class CSVExporter:
    def __init__(self, pool: asyncpg.Pool, export_dir: str, interval_seconds: float) -> None:
        self._pool = pool
        self._export_dir = Path(export_dir)
        self._interval_seconds = interval_seconds

    async def run(self, stop_event: asyncio.Event) -> None:
        self._export_dir.mkdir(parents=True, exist_ok=True)

        while not stop_event.is_set():
            await self.export_once()
            try:
                await asyncio.wait_for(stop_event.wait(), timeout=self._interval_seconds)
            except TimeoutError:
                pass

    async def export_once(self) -> None:
        async with self._pool.acquire() as conn:
            trades = await conn.fetch(TRADE_EXPORT_SQL)
            wallet_events = await conn.fetch(WALLET_EVENT_EXPORT_SQL)

        self._write_csv(self._export_dir / "hl_trades.csv", TRADE_HEADERS, trades)
        self._write_csv(
            self._export_dir / "wallet_trade_events.csv",
            WALLET_EVENT_HEADERS,
            wallet_events,
        )

        LOGGER.info(
            "Exported %d trade rows and %d wallet event rows to %s.",
            len(trades),
            len(wallet_events),
            self._export_dir,
        )

    def _write_csv(
        self,
        path: Path,
        headers: list[str],
        rows: list[asyncpg.Record],
    ) -> None:
        temp_path = path.with_suffix(f"{path.suffix}.tmp")
        with temp_path.open("w", newline="", encoding="utf-8") as handle:
            writer = csv.writer(handle)
            writer.writerow(headers)
            for row in rows:
                writer.writerow([self._serialize_value(row[column]) for column in headers])

        os.replace(temp_path, path)

    @staticmethod
    def _serialize_value(value: Any) -> Any:
        if isinstance(value, datetime):
            return value.isoformat()
        return value

