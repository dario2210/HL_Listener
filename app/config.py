from __future__ import annotations

import os
from dataclasses import dataclass
from decimal import Decimal


def _split_csv(value: str) -> tuple[str, ...]:
    return tuple(item.strip() for item in value.split(",") if item.strip())


def _as_bool(value: str, default: bool = False) -> bool:
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


@dataclass(frozen=True, slots=True)
class Settings:
    database_url: str
    hyperliquid_info_url: str
    hyperliquid_ws_url: str
    tracked_coins: tuple[str, ...]
    tracked_keywords: tuple[str, ...]
    min_notional_usd: Decimal
    ws_heartbeat_seconds: float
    reconnect_initial_delay_seconds: float
    reconnect_max_delay_seconds: float
    database_connect_retry_seconds: float
    log_level: str
    print_discovered_markets: bool
    csv_export_dir: str
    csv_export_interval_seconds: float


def load_settings() -> Settings:
    tracked_coins = _split_csv(os.getenv("TRACKED_COINS", "BTC,ETH"))
    tracked_keywords = tuple(
        keyword.upper() for keyword in _split_csv(os.getenv("TRACKED_KEYWORDS", ""))
    )

    return Settings(
        database_url=os.environ["DATABASE_URL"],
        hyperliquid_info_url=os.getenv("HYPERLIQUID_INFO_URL", "https://api.hyperliquid.xyz/info"),
        hyperliquid_ws_url=os.getenv("HYPERLIQUID_WS_URL", "wss://api.hyperliquid.xyz/ws"),
        tracked_coins=tracked_coins,
        tracked_keywords=tracked_keywords,
        min_notional_usd=Decimal(os.getenv("MIN_NOTIONAL_USD", "100000")),
        ws_heartbeat_seconds=float(os.getenv("WS_HEARTBEAT_SECONDS", "20")),
        reconnect_initial_delay_seconds=float(os.getenv("RECONNECT_INITIAL_DELAY_SECONDS", "2")),
        reconnect_max_delay_seconds=float(os.getenv("RECONNECT_MAX_DELAY_SECONDS", "30")),
        database_connect_retry_seconds=float(os.getenv("DATABASE_CONNECT_RETRY_SECONDS", "5")),
        log_level=os.getenv("LOG_LEVEL", "INFO").upper(),
        print_discovered_markets=_as_bool(os.getenv("PRINT_DISCOVERED_MARKETS", "false")),
        csv_export_dir=os.getenv("CSV_EXPORT_DIR", "/exports"),
        csv_export_interval_seconds=float(os.getenv("CSV_EXPORT_INTERVAL_SECONDS", "30")),
    )
