from __future__ import annotations

import asyncio
import json
import logging
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
from json import JSONDecodeError
from typing import Any

import aiohttp

from app.config import Settings
from app.db import TradeRecord


LOGGER = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class Market:
    dex: str
    coin: str


class HyperliquidTradeListener:
    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._dex_by_coin: dict[str, str] = {}

    async def discover_markets(self, session: aiohttp.ClientSession) -> list[Market]:
        dexes = [""] + await self._fetch_builder_dexes(session)
        discovered: dict[str, Market] = {}

        for dex in dexes:
            meta = await self._post_info(session, {"type": "meta", "dex": dex})
            universe = meta.get("universe", []) if isinstance(meta, dict) else []
            for asset in universe:
                coin = str(asset.get("name", "")).strip()
                if coin:
                    discovered[coin] = Market(dex=dex, coin=coin)

        if self._settings.print_discovered_markets:
            LOGGER.info("Discovered markets: %s", ", ".join(sorted(discovered)))

        selected = self._select_markets(list(discovered.values()))
        if not selected:
            raise RuntimeError(
                "No markets matched TRACKED_COINS / TRACKED_KEYWORDS. "
                "Enable PRINT_DISCOVERED_MARKETS=true to inspect available market names."
            )

        self._dex_by_coin = {market.coin: market.dex for market in selected}
        LOGGER.info("Selected markets: %s", ", ".join(market.coin for market in selected))
        return selected

    async def run(
        self,
        session: aiohttp.ClientSession,
        on_trades,
        stop_event: asyncio.Event,
    ) -> None:
        reconnect_delay = self._settings.reconnect_initial_delay_seconds

        while not stop_event.is_set():
            try:
                markets = await self.discover_markets(session)
                await self._stream_markets(session, markets, on_trades, stop_event)
                reconnect_delay = self._settings.reconnect_initial_delay_seconds
            except asyncio.CancelledError:
                raise
            except Exception:
                LOGGER.exception(
                    "Listener stopped unexpectedly. Reconnecting in %.1f seconds.",
                    reconnect_delay,
                )
                try:
                    await asyncio.wait_for(stop_event.wait(), timeout=reconnect_delay)
                except TimeoutError:
                    pass
                reconnect_delay = min(
                    reconnect_delay * 2,
                    self._settings.reconnect_max_delay_seconds,
                )

    async def _stream_markets(
        self,
        session: aiohttp.ClientSession,
        markets: list[Market],
        on_trades,
        stop_event: asyncio.Event,
    ) -> None:
        async with session.ws_connect(
            self._settings.hyperliquid_ws_url,
            heartbeat=self._settings.ws_heartbeat_seconds,
            autoping=True,
            autoclose=True,
        ) as websocket:
            for market in markets:
                await websocket.send_json(
                    {
                        "method": "subscribe",
                        "subscription": {"type": "trades", "coin": market.coin},
                    }
                )

            LOGGER.info("Subscribed to %d trade streams.", len(markets))

            while not stop_event.is_set():
                receive_task = asyncio.create_task(websocket.receive())
                stop_task = asyncio.create_task(stop_event.wait())
                done, pending = await asyncio.wait(
                    {receive_task, stop_task},
                    return_when=asyncio.FIRST_COMPLETED,
                )

                for task in pending:
                    task.cancel()
                if pending:
                    await asyncio.gather(*pending, return_exceptions=True)

                if stop_task in done and stop_event.is_set():
                    await websocket.close()
                    break

                message = receive_task.result()

                if message.type == aiohttp.WSMsgType.TEXT:
                    try:
                        payload = json.loads(message.data)
                    except JSONDecodeError:
                        LOGGER.debug("Skipping non-JSON websocket payload: %s", message.data)
                        continue
                    channel = payload.get("channel")
                    if channel == "subscriptionResponse":
                        LOGGER.debug("Subscription ack: %s", payload)
                        continue
                    if channel != "trades":
                        continue

                    trades = self._parse_trade_batch(payload.get("data", []))
                    if trades:
                        await on_trades(trades)
                    continue

                if message.type in {aiohttp.WSMsgType.CLOSED, aiohttp.WSMsgType.CLOSE}:
                    raise RuntimeError("WebSocket closed by remote host.")

                if message.type == aiohttp.WSMsgType.ERROR:
                    raise RuntimeError(f"WebSocket error: {websocket.exception()}")

    async def _fetch_builder_dexes(self, session: aiohttp.ClientSession) -> list[str]:
        payload = await self._post_info(session, {"type": "perpDexs"})
        dex_names: list[str] = []
        if isinstance(payload, list):
            for item in payload:
                if isinstance(item, dict):
                    name = str(item.get("name", "")).strip()
                    if name:
                        dex_names.append(name)
        return dex_names

    async def _post_info(self, session: aiohttp.ClientSession, body: dict[str, Any]) -> Any:
        async with session.post(self._settings.hyperliquid_info_url, json=body) as response:
            response.raise_for_status()
            return await response.json()

    def _select_markets(self, discovered: list[Market]) -> list[Market]:
        exact = {coin.upper() for coin in self._settings.tracked_coins}
        keywords = self._settings.tracked_keywords

        selected = []
        for market in sorted(discovered, key=lambda item: (item.dex, item.coin)):
            coin_upper = market.coin.upper()
            matches_exact = coin_upper in exact
            matches_keyword = any(keyword in coin_upper for keyword in keywords)
            if matches_exact or matches_keyword:
                selected.append(market)
        return selected

    def _parse_trade_batch(self, payload: Any) -> list[TradeRecord]:
        if not isinstance(payload, list):
            return []

        kept: list[TradeRecord] = []
        for raw_trade in payload:
            record = self._parse_trade(raw_trade)
            if record is not None:
                kept.append(record)
        return kept

    def _parse_trade(self, raw_trade: Any) -> TradeRecord | None:
        if not isinstance(raw_trade, dict):
            return None

        try:
            users = raw_trade["users"]
            if not isinstance(users, list) or len(users) != 2:
                return None

            price = Decimal(str(raw_trade["px"]))
            size = Decimal(str(raw_trade["sz"]))
            notional_usd = abs(price * size)
            if notional_usd < self._settings.min_notional_usd:
                return None

            coin = str(raw_trade["coin"])
            dex = self._dex_by_coin.get(coin, "")
            trade_time = datetime.fromtimestamp(int(raw_trade["time"]) / 1000, tz=UTC)

            return TradeRecord(
                dex=dex,
                coin=coin,
                trade_time=trade_time,
                side=str(raw_trade.get("side", "")),
                price=price,
                size=size,
                notional_usd=notional_usd,
                trade_hash=str(raw_trade["hash"]),
                tid=int(raw_trade["tid"]),
                buyer_address=str(users[0]),
                seller_address=str(users[1]),
                raw_payload=raw_trade,
            )
        except (KeyError, TypeError, ValueError, InvalidOperation):
            LOGGER.debug("Skipping malformed trade payload: %s", raw_trade)
            return None
