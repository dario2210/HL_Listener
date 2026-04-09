"""
ema11_engine.py
===============
Wspólna warstwa decyzji strategii EMA-11.

CEL (punkt 1 z briefu):
  Jeden moduł sygnałów używany przez backtest, WFO i live runner.
  Live NIE ma własnej alternatywnej logiki – wywołuje dokładnie te funkcje.

Eksportuje:
  BarData          – dane jednego baru (input do silnika)
  Signal           – wynik decyzji (action + reason)
  PositionState    – stan otwartej pozycji
  generate_entry_signal(bar, prev_bar, params, position) -> Signal
  generate_exit_signal(bar, prev_bar, params, position)  -> Signal
  compute_trade_close(entry_price, exit_price, side, fee_rate, capital_at_open) -> dict
  apply_slippage(price, side, action, slippage_bps, spread_bps) -> float
"""

from __future__ import annotations

import numpy as np
from dataclasses import dataclass, field
from typing import Optional, Literal

# ──────────────────────────────────────────────────────────────────────────────
# TYPY
# ──────────────────────────────────────────────────────────────────────────────

Side   = Literal["long", "short"]
Action = Literal["none", "open_long", "open_short", "close_sl", "close_tp",
                 "close_trail", "close_force"]


@dataclass
class BarData:
    """Dane jednej świecy (już obliczone wskaźniki)."""
    time      : object   # pd.Timestamp
    close     : float
    rsi       : float
    tma_rsi   : float
    ema       : float    # EMA filtr trendu (długość z params["ema_len"])
    ema_slope : float = 0.0   # pct_change(3) EMA – nachylenie


@dataclass
class Signal:
    action    : Action
    reason    : str              = ""
    exit_price: Optional[float] = None
    meta      : dict             = field(default_factory=dict)


@dataclass
class PositionState:
    side             : Side
    entry_price      : float
    entry_time       : object
    rsi_peak         : Optional[float] = None
    over_zone        : bool            = False
    bars_in_position : int             = 0
    entry_meta       : dict            = field(default_factory=dict)


# ──────────────────────────────────────────────────────────────────────────────
# WSPÓLNA FUNKCJA ROZLICZENIA TRANSAKCJI
# ──────────────────────────────────────────────────────────────────────────────

def compute_trade_close(
    entry_price    : float,
    exit_price     : float,
    side           : Side,
    fee_rate       : float,
    capital_at_open: float,
) -> dict:
    """
    Wspólna logika rozliczenia zamknięcia pozycji.
    Używana przez backtest (ema11_strategy) i live runner.

    Zwraca słownik:
      gross_ret, net_ret, fee_ret, fee_usd, pnl
    """
    if side == "long":
        gross_ret = (exit_price / entry_price) - 1.0
    else:
        gross_ret = (entry_price / exit_price) - 1.0

    net_ret = (1.0 + gross_ret) * (1.0 - fee_rate) ** 2 - 1.0
    fee_ret = net_ret - gross_ret
    fee_usd = abs(fee_ret) * capital_at_open
    pnl     = capital_at_open * net_ret

    return {
        "gross_ret": gross_ret,
        "net_ret"  : net_ret,
        "fee_ret"  : fee_ret,
        "fee_usd"  : fee_usd,
        "pnl"      : pnl,
    }


# ──────────────────────────────────────────────────────────────────────────────
# SYGNAŁY WEJŚCIA
# ──────────────────────────────────────────────────────────────────────────────

def generate_entry_signal(
    bar      : BarData,
    prev_bar : BarData,
    params   : dict,
    position : Optional[PositionState],
) -> Signal:
    """
    Sprawdza warunki wejścia w pozycję.
    Zwraca Signal(action="open_long" / "open_short" / "none").

    Warunki Long:
      - close > EMA(ema_len)            → trend wzrostowy
      - slope EMA > min_ema_slope       → EMA faktycznie rośnie
      - min_ema_dist <= dist(close,EMA) <= max_ema_dist  → nie za blisko / nie za daleko
      - tma_low_min <= TMA(RSI) <= tma_low_max
      - RSI crossover w górę: rsi_prev < tma_prev AND rsi >= tma

    Warunki Short:
      - close < EMA(ema_len)            → trend spadkowy
      - slope EMA < -min_ema_slope      → EMA faktycznie spada
      - min_ema_dist <= dist(close,EMA) <= max_ema_dist
      - tma_high_min <= TMA(RSI) <= tma_high_max
      - RSI crossover w dół: rsi_prev > tma_prev AND rsi <= tma
    """
    if position is not None:
        return Signal(action="none")   # już w pozycji

    if any(np.isnan(v) for v in [bar.rsi, bar.tma_rsi, bar.ema,
                                   prev_bar.rsi, prev_bar.tma_rsi]):
        return Signal(action="none")

    tma_low_min  = params["tma_low_min"]
    tma_low_max  = params["tma_low_max"]
    tma_high_min = params["tma_high_min"]
    tma_high_max = params["tma_high_max"]

    min_ema_dist  = params.get("min_ema_distance_pct", 0.0)
    max_ema_dist  = params.get("max_ema_distance_pct", 1.0)
    min_ema_slope = params.get("min_ema_slope", 0.0)

    ema_dist_long  = bar.close / bar.ema - 1.0    # > 0 gdy close > ema
    ema_dist_short = bar.ema  / bar.close - 1.0   # > 0 gdy close < ema

    trend_up   = (bar.close > bar.ema
                  and bar.ema_slope >= min_ema_slope
                  and min_ema_dist <= ema_dist_long <= max_ema_dist)
    trend_down = (bar.close < bar.ema
                  and bar.ema_slope <= -min_ema_slope
                  and min_ema_dist <= ema_dist_short <= max_ema_dist)

    long_cond = (
        trend_up
        and tma_low_min <= bar.tma_rsi <= tma_low_max
        and prev_bar.rsi < prev_bar.tma_rsi
        and bar.rsi >= bar.tma_rsi
    )
    short_cond = (
        trend_down
        and tma_high_min <= bar.tma_rsi <= tma_high_max
        and prev_bar.rsi > prev_bar.tma_rsi
        and bar.rsi <= bar.tma_rsi
    )

    tp_pct = params.get("tp_pct", 0.05)
    sl_pct = params.get("sl_pct", 0.005)
    _meta_base = {
        "entry_close"     : bar.close,
        "entry_ema"       : bar.ema,
        "entry_ema_slope" : bar.ema_slope,
        "entry_rsi"       : bar.rsi,
        "entry_tma_rsi"   : bar.tma_rsi,
        "prev_rsi"        : prev_bar.rsi,
        "prev_tma_rsi"    : prev_bar.tma_rsi,
        "ema_dist_long"   : round(ema_dist_long,  6),
        "ema_dist_short"  : round(ema_dist_short, 6),
        "trend_up"        : trend_up,
        "trend_down"      : trend_down,
        "long_cond"       : long_cond,
        "short_cond"      : short_cond,
    }

    if long_cond:
        return Signal(action="open_long", reason="RSI_CROSS_UP+EMA_UP", meta={
            **_meta_base,
            "tma_zone"  : "low",
            "cross_type": "up",
            "tp_price"  : round(bar.close * (1.0 + tp_pct), 4),
            "sl_price"  : round(bar.close * (1.0 - sl_pct), 4),
        })
    if short_cond:
        return Signal(action="open_short", reason="RSI_CROSS_DN+EMA_DN", meta={
            **_meta_base,
            "tma_zone"  : "high",
            "cross_type": "down",
            "tp_price"  : round(bar.close * (1.0 - tp_pct), 4),
            "sl_price"  : round(bar.close * (1.0 + sl_pct), 4),
        })
    return Signal(action="none")


# ──────────────────────────────────────────────────────────────────────────────
# SYGNAŁY WYJŚCIA
# ──────────────────────────────────────────────────────────────────────────────

def generate_exit_signal(
    bar      : BarData,
    prev_bar : BarData,
    params   : dict,
    position : PositionState,
) -> Signal:
    """
    Sprawdza warunki wyjścia z pozycji i aktualizuje stan trailing.
    Zwraca Signal z action = close_sl / close_tp / close_trail / close_force / none.

    Uwaga: modyfikuje position.rsi_peak, position.over_zone i position.bars_in_position
    in-place (trailing RSI, time stop).
    """
    tp_pct     = params["tp_pct"]
    sl_pct     = params["sl_pct"]
    trail_drop = params["trail_drop"]
    max_bars   = params.get("max_bars_in_trade", 0)
    use_cross  = params.get("use_rsi_cross_exit", False)

    close = bar.close
    rsi   = bar.rsi

    # inkrementuj licznik barów w pozycji
    position.bars_in_position += 1

    # ── hoist sl/tp (używane w warunkach i w meta) ───────────────────────────
    if position.side == "long":
        sl_price = position.entry_price * (1.0 - sl_pct)
        tp_price = position.entry_price * (1.0 + tp_pct)
    else:
        sl_price = position.entry_price * (1.0 + sl_pct)
        tp_price = position.entry_price * (1.0 - tp_pct)

    def _meta(trigger: str) -> dict:
        return {
            "exit_close"       : close,
            "exit_rsi"         : rsi,
            "exit_tma_rsi"     : bar.tma_rsi,
            "rsi_peak"         : position.rsi_peak,
            "over_zone"        : position.over_zone,
            "bars_in_position" : position.bars_in_position,
            "tp_price"         : round(tp_price, 4),
            "sl_price"         : round(sl_price, 4),
            "trail_drop"       : trail_drop,
            "exit_trigger"     : trigger,
        }

    # ── time stop ─────────────────────────────────────────────────────────────
    if max_bars > 0 and position.bars_in_position >= max_bars:
        return Signal(action="close_force", reason="TIME_STOP",
                      meta=_meta("TIME_STOP"))

    if position.side == "long":
        if rsi >= 60.0:
            position.over_zone = True
            position.rsi_peak  = rsi if position.rsi_peak is None \
                                     else max(position.rsi_peak, rsi)

        if close <= sl_price:
            return Signal(action="close_sl",    reason="SL",
                          exit_price=sl_price,  meta=_meta("SL"))
        if close >= tp_price:
            return Signal(action="close_tp",    reason="TP",
                          exit_price=tp_price,  meta=_meta("TP"))
        if (position.over_zone and position.rsi_peak is not None
                and (rsi < position.rsi_peak - trail_drop or rsi < 60.0)):
            return Signal(action="close_trail", reason="TRAIL_RSI",
                          meta=_meta("TRAIL_RSI"))
        if use_cross and prev_bar.rsi > prev_bar.tma_rsi and rsi <= bar.tma_rsi:
            return Signal(action="close_trail", reason="RSI_CROSS_BACK",
                          meta=_meta("RSI_CROSS_BACK"))

    elif position.side == "short":
        if rsi <= 40.0:
            position.over_zone = True
            position.rsi_peak  = rsi if position.rsi_peak is None \
                                     else min(position.rsi_peak, rsi)

        if close >= sl_price:
            return Signal(action="close_sl",    reason="SL",
                          exit_price=sl_price,  meta=_meta("SL"))
        if close <= tp_price:
            return Signal(action="close_tp",    reason="TP",
                          exit_price=tp_price,  meta=_meta("TP"))
        if (position.over_zone and position.rsi_peak is not None
                and (rsi > position.rsi_peak + trail_drop or rsi > 40.0)):
            return Signal(action="close_trail", reason="TRAIL_RSI",
                          meta=_meta("TRAIL_RSI"))
        if use_cross and prev_bar.rsi < prev_bar.tma_rsi and rsi >= bar.tma_rsi:
            return Signal(action="close_trail", reason="RSI_CROSS_BACK",
                          meta=_meta("RSI_CROSS_BACK"))

    return Signal(action="none")


# ──────────────────────────────────────────────────────────────────────────────
# SLIPPAGE / SPREAD  (punkt 3)
# ──────────────────────────────────────────────────────────────────────────────

def apply_slippage(
    price        : float,
    side         : Side,
    action       : str,   # "open" lub "close"
    slippage_bps : float = 0.0,
    spread_bps   : float = 0.0,
) -> float:
    """
    Koryguje cenę egzekucji o slippage i spread.

    Logika (rynek kryptowalut, market order):
      - Przy wejściu long  → płacimy więcej  (price * (1 + slip + spread/2))
      - Przy wejściu short → dostajemy mniej (price * (1 - slip - spread/2))
      - Przy zamknięciu odwrotnie.

    Parametry:
      slippage_bps – poślizg w bps (1 bps = 0.01%), typowo 1–5 bps
      spread_bps   – spread bid/ask w bps, typowo 1–3 bps na płynnych parach

    Przy slippage_bps=0 i spread_bps=0 → zwraca price bez zmian.
    """
    if slippage_bps == 0.0 and spread_bps == 0.0:
        return price

    total_bps = slippage_bps + spread_bps / 2.0
    factor    = total_bps / 10_000.0

    opening = (action == "open")

    if (side == "long"  and opening) or (side == "short" and not opening):
        return price * (1.0 + factor)   # kupujemy drożej / sprzedajemy drożej (niekorzystnie)
    else:
        return price * (1.0 - factor)   # sprzedajemy taniej / kupujemy taniej (niekorzystnie)


# ──────────────────────────────────────────────────────────────────────────────
# POMOCNICZE – budowanie BarData z DataFrame row
# ──────────────────────────────────────────────────────────────────────────────

def bar_from_row(row, ema_col: str) -> BarData:
    """Tworzy BarData z wiersza DataFrame."""
    slope_col = f"{ema_col}_slope"
    ema_slope = float(row[slope_col]) if slope_col in row.index and not np.isnan(row[slope_col]) else 0.0
    return BarData(
        time      = row["time"],
        close     = float(row["close"]),
        rsi       = float(row["rsi"]),
        tma_rsi   = float(row["tma_rsi"]),
        ema       = float(row[ema_col]),
        ema_slope = ema_slope,
    )
