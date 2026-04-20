"""
ema11_strategy.py
=================
Silnik backtestu – używa wspólnej warstwy ema11_engine.

Zmiany (brief):
  [1] Logika sygnałów przeniesiona do ema11_engine – brak duplikacji z live
  [3] Slippage i spread konfigurowalne przez params
  [4] TradeRecord przechowuje dokładne fee_usd, capital_before, capital_after,
      position_notional – bez aproksymacji
"""

from __future__ import annotations

import numpy as np
import pandas as pd
from dataclasses import dataclass, field
from typing import Optional

from ema11_params import FEE_RATE
from ema11_engine import (
    BarData, Signal, PositionState,
    generate_entry_signal, generate_exit_signal,
    compute_trade_close, apply_slippage, bar_from_row,
)


@dataclass
class TradeRecord:
    # ── dane finansowe ────────────────────────────────────────────────────────
    side              : str
    entry_time        : object
    exit_time         : object
    entry_price       : float
    exit_price        : float
    gross_ret         : float
    fee_ret           : float
    net_ret           : float
    pnl               : float
    reason            : str
    capital_before    : float
    capital_after     : float
    position_notional : float
    fee_usd           : float
    slippage_usd      : float
    # ── snapshot wejścia ──────────────────────────────────────────────────────
    entry_rsi        : float = 0.0
    entry_tma_rsi    : float = 0.0
    entry_ema        : float = 0.0
    entry_ema_slope  : float = 0.0
    entry_ema_dist   : float = 0.0   # dist_long dla long, dist_short dla short
    entry_tma_zone   : str   = ""
    entry_cross_type : str   = ""
    entry_trend_up   : bool  = False
    entry_prev_rsi   : float = 0.0
    entry_prev_tma   : float = 0.0
    entry_tp_price   : float = 0.0
    entry_sl_price   : float = 0.0
    # ── snapshot wyjścia ──────────────────────────────────────────────────────
    exit_rsi         : float = 0.0
    exit_tma_rsi     : float = 0.0
    exit_rsi_peak    : float = float("nan")
    exit_over_zone   : bool  = False
    exit_bars        : int   = 0
    exit_tp_price    : float = 0.0
    exit_sl_price    : float = 0.0
    exit_trail_drop  : float = 0.0
    exit_trigger     : str   = ""


class EMA11Strategy:
    """
    RSI + TMA(RSI) + EMA Trend Filter.

    params może zawierać:
      tp_pct, sl_pct, trail_drop, ema_len
      tma_low_min, tma_low_max, tma_high_min, tma_high_max
      fee_rate        (domyślnie FEE_RATE)
      slippage_bps    (domyślnie 0)
      spread_bps      (domyślnie 0)
    """

    def __init__(self, params: dict, fee_rate: float = FEE_RATE):
        self.params       = params
        self.fee_rate     = params.get("fee_rate",     fee_rate)
        self.slippage_bps = params.get("slippage_bps", 0.0)
        self.spread_bps   = params.get("spread_bps",   0.0)
        self.position     : Optional[PositionState] = None

    def _close_position(self, capital, bar, signal, capital_at_open, entry_meta=None):
        pos = self.position
        raw_exit   = signal.exit_price if signal.exit_price is not None else bar.close
        exit_price = apply_slippage(raw_exit, pos.side, "close",
                                    self.slippage_bps, self.spread_bps)

        result = compute_trade_close(
            entry_price     = pos.entry_price,
            exit_price      = exit_price,
            side            = pos.side,
            fee_rate        = self.fee_rate,
            capital_at_open = capital_at_open,
        )
        gross_ret    = result["gross_ret"]
        net_ret      = result["net_ret"]
        fee_ret      = result["fee_ret"]
        fee_usd      = result["fee_usd"]
        pnl          = result["pnl"]
        slip_delta   = abs(exit_price - raw_exit)
        slippage_usd = (slip_delta / pos.entry_price) * capital_at_open
        new_capital  = capital + pnl

        em = entry_meta or pos.entry_meta or {}
        xm = signal.meta or {}

        rec = TradeRecord(
            side              = pos.side,
            entry_time        = pos.entry_time,
            exit_time         = bar.time,
            entry_price       = pos.entry_price,
            exit_price        = exit_price,
            gross_ret         = gross_ret,
            fee_ret           = fee_ret,
            net_ret           = net_ret,
            pnl               = pnl,
            reason            = signal.reason,
            capital_before    = capital_at_open,
            capital_after     = new_capital,
            position_notional = capital_at_open,
            fee_usd           = fee_usd,
            slippage_usd      = slippage_usd,
            # entry snapshot
            entry_rsi        = em.get("entry_rsi",       0.0),
            entry_tma_rsi    = em.get("entry_tma_rsi",   0.0),
            entry_ema        = em.get("entry_ema",        0.0),
            entry_ema_slope  = em.get("entry_ema_slope",  0.0),
            entry_ema_dist   = em.get("ema_dist_long",    0.0)
                               if pos.side == "long"
                               else em.get("ema_dist_short", 0.0),
            entry_tma_zone   = em.get("tma_zone",    ""),
            entry_cross_type = em.get("cross_type",  ""),
            entry_trend_up   = em.get("trend_up",    False),
            entry_prev_rsi   = em.get("prev_rsi",    0.0),
            entry_prev_tma   = em.get("prev_tma_rsi",0.0),
            entry_tp_price   = em.get("tp_price",    0.0),
            entry_sl_price   = em.get("sl_price",    0.0),
            # exit snapshot
            exit_rsi         = xm.get("exit_rsi",         0.0),
            exit_tma_rsi     = xm.get("exit_tma_rsi",     0.0),
            exit_rsi_peak    = xm.get("rsi_peak")   if xm.get("rsi_peak") is not None else float("nan"),
            exit_over_zone   = xm.get("over_zone",        False),
            exit_bars        = xm.get("bars_in_position", 0),
            exit_tp_price    = xm.get("tp_price",         0.0),
            exit_sl_price    = xm.get("sl_price",         0.0),
            exit_trail_drop  = xm.get("trail_drop",       0.0),
            exit_trigger     = xm.get("exit_trigger",     signal.reason),
        )
        self.position = None
        return rec, new_capital

    def run(self, df, initial_capital):
        ema_col = f"ema_{self.params['ema_len']}"
        if ema_col not in df.columns:
            raise ValueError(f"Brak kolumny {ema_col}.")

        capital         = initial_capital
        capital_at_open = initial_capital
        trades          = []
        equity_curve    = []
        self.position   = None

        if len(df) == 0:
            return pd.DataFrame(), pd.DataFrame(columns=["time","equity"]), capital

        equity_curve.append((df["time"].iloc[0], capital))

        for i in range(1, len(df)):
            bar  = bar_from_row(df.iloc[i],     ema_col)
            prev = bar_from_row(df.iloc[i - 1], ema_col)

            if any(np.isnan(v) for v in [bar.rsi, bar.tma_rsi, bar.ema,
                                          prev.rsi, prev.tma_rsi]):
                continue

            if self.position is not None:
                sig = generate_exit_signal(bar, prev, self.params, self.position)
                if sig.action != "none":
                    rec, capital = self._close_position(capital, bar, sig, capital_at_open)
                    trades.append(rec)
                    equity_curve.append((bar.time, capital))

            if self.position is None:
                sig = generate_entry_signal(bar, prev, self.params, self.position)
                if sig.action in ("open_long", "open_short"):
                    side = "long" if sig.action == "open_long" else "short"
                    entry_price = apply_slippage(bar.close, side, "open",
                                                 self.slippage_bps, self.spread_bps)
                    self.position   = PositionState(side, entry_price, bar.time,
                                                     entry_meta=sig.meta)
                    capital_at_open = capital

        if self.position is not None:
            last_bar  = bar_from_row(df.iloc[-1], ema_col)
            force_sig = Signal(action="close_force", reason="FORCE_EXIT_END",
                               exit_price=last_bar.close)
            rec, capital = self._close_position(capital, last_bar, force_sig, capital_at_open)
            trades.append(rec)
            equity_curve.append((last_bar.time, capital))

        if trades:
            _COLS = [
                "side","entry_time","exit_time","entry_price","exit_price",
                "gross_ret","fee_ret","net_ret","pnl","reason",
                "capital_before","capital_after","position_notional",
                "fee_usd","slippage_usd",
                # entry snapshot
                "entry_rsi","entry_tma_rsi","entry_ema","entry_ema_slope",
                "entry_ema_dist","entry_tma_zone","entry_cross_type",
                "entry_trend_up","entry_prev_rsi","entry_prev_tma",
                "entry_tp_price","entry_sl_price",
                # exit snapshot
                "exit_rsi","exit_tma_rsi","exit_rsi_peak","exit_over_zone",
                "exit_bars","exit_tp_price","exit_sl_price",
                "exit_trail_drop","exit_trigger",
            ]
            trades_df = pd.DataFrame([
                {c: getattr(t, c) for c in _COLS} for t in trades
            ])
        else:
            _COLS_BASE = [
                "side","entry_time","exit_time","entry_price","exit_price",
                "gross_ret","fee_ret","net_ret","pnl","reason",
                "capital_before","capital_after","position_notional",
                "fee_usd","slippage_usd",
                "entry_rsi","entry_tma_rsi","entry_ema","entry_ema_slope",
                "entry_ema_dist","entry_tma_zone","entry_cross_type",
                "entry_trend_up","entry_prev_rsi","entry_prev_tma",
                "entry_tp_price","entry_sl_price",
                "exit_rsi","exit_tma_rsi","exit_rsi_peak","exit_over_zone",
                "exit_bars","exit_tp_price","exit_sl_price",
                "exit_trail_drop","exit_trigger",
            ]
            trades_df = pd.DataFrame(columns=_COLS_BASE)

        equity_df = pd.DataFrame(equity_curve, columns=["time","equity"])
        equity_df = equity_df.dropna(subset=["time"]).reset_index(drop=True)
        return trades_df, equity_df, capital
