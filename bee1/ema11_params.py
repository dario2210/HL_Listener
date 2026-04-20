"""
ema11_params.py
===============
Centralny moduł parametrów strategii RSI + TMA(RSI) + EMA Trend Filter (11E).
Zmień tylko tutaj – reszta kodu czyta stąd.
"""

import os
import json

# ─── Dane wejściowe ────────────────────────────────────────────────────────
CSV_PATH         = "eth_1h_2021_2025.csv"
INITIAL_CAPITAL  = 10_000.0

# ─── Binance – pobieranie danych ───────────────────────────────────────────
BINANCE_SYMBOL     = "ETHUSDT"
BINANCE_INTERVAL   = "1h"          # timeframe: 1m,5m,15m,30m,1h,4h,1d itd.
BINANCE_MARKET     = "spot"        # "spot" lub "futures"
BINANCE_START_DATE = "2021-01-01"  # od kiedy pobierać przy pierwszym uruchomieniu
# CSV cache – jeśli None, nazwa generowana automatycznie: {symbol}_{interval}.csv
BINANCE_CSV_CACHE  = None

# ─── Wskaźniki (stałe – nie optymalizowane) ────────────────────────────────
RSI_LEN  = 14
TMA_LEN  = 14

# Strefy TMA(RSI) – stałe
TMA_LOW_MIN  = 30.0
TMA_LOW_MAX  = 45.0
TMA_HIGH_MIN = 55.0
TMA_HIGH_MAX = 70.0

# ─── Opłaty ───────────────────────────────────────────────────────────────
FEE_RATE = 0.0007   # 0.07% za każdą stronę (open + close = 2× fee)

# ─── WFO – okna (w dniach – przeliczane na bary wg BINANCE_INTERVAL) ──────
OPT_DAYS  = 90   # długość okna optymalizacji (w dniach)
LIVE_DAYS = 14   # długość okna live (w dniach)
# Uwaga: ema11_wfo.py automatycznie przelicza dni → bary używając ema11_binance.wfo_bars()

# ─── Siatka parametrów WFO ────────────────────────────────────────────────
TP_GRID        = [0.035, 0.040, 0.050]
SL_GRID        = [0.0050, 0.0075, 0.0100]
TRAIL_DROP_GRID= [3.0, 5.0, 6.0]
EMA_LEN_GRID   = [50, 100, 150, 200]

# ─── Siatka stref TMA(RSI) dla WFO ────────────────────────────────────────
TMA_LOW_MIN_GRID  = [28.0, 30.0, 32.0]
TMA_LOW_MAX_GRID  = [42.0, 45.0, 48.0]
TMA_HIGH_MIN_GRID = [52.0, 55.0, 58.0]
TMA_HIGH_MAX_GRID = [68.0, 70.0, 72.0]

# ─── Domyślne parametry strategii (fallback, gdy brak JSON z WFO) ─────────
DEFAULT_PARAMS = {
    "tp_pct"              : 0.050,
    "sl_pct"              : 0.0050,
    "trail_drop"          : 3.0,
    "ema_len"             : 200,
    "tma_low_min"         : TMA_LOW_MIN,
    "tma_low_max"         : TMA_LOW_MAX,
    "tma_high_min"        : TMA_HIGH_MIN,
    "tma_high_max"        : TMA_HIGH_MAX,
    "fee_rate"            : FEE_RATE,
    "slippage_bps"        : 2.0,
    "spread_bps"          : 1.0,
    "min_ema_distance_pct": 0.0025,
    "max_ema_distance_pct": 0.03,
    "min_ema_slope"       : 0.0,
    "max_bars_in_trade"   : 18,
    "use_rsi_cross_exit"  : False,
}

# ─── Ścieżka do JSON z najlepszymi parametrami WFO ────────────────────────
WFO_BEST_PARAMS_PATH = "ema11_wfo_best_params.json"


def load_params() -> dict:
    """
    Zwraca parametry strategii.
    1) Startuje od DEFAULT_PARAMS,
    2) Nadpisuje z WFO_BEST_PARAMS_PATH (jeśli istnieje).
    """
    params = dict(DEFAULT_PARAMS)

    json_path = os.path.join(os.path.dirname(__file__), WFO_BEST_PARAMS_PATH)
    if os.path.exists(json_path):
        try:
            with open(json_path, "r") as f:
                data = json.load(f)
            if isinstance(data, dict):
                for key in DEFAULT_PARAMS:
                    if key in data:
                        params[key] = data[key]
            print(f"[params] Wczytano parametry WFO z {WFO_BEST_PARAMS_PATH}")
        except Exception as e:
            print(f"[params] ⚠️  Nie udało się wczytać {WFO_BEST_PARAMS_PATH}: {e!r}")
    else:
        print(f"[params] Brak {WFO_BEST_PARAMS_PATH} – używam DEFAULT_PARAMS")

    return params


def save_params(params: dict, path: str = WFO_BEST_PARAMS_PATH) -> None:
    """Zapisuje parametry do JSON."""
    with open(path, "w") as f:
        json.dump(params, f, indent=2)
    print(f"[params] Zapisano parametry → {path}")
