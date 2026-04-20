"""
ema11_wfo.py
============
Walk-Forward Optimization.
Używa ema11_wfo_scoring dla rozbudowanego scoringu (punkt 5).
"""

from __future__ import annotations

import pandas as pd
import numpy as np
from typing import Optional

from ema11_params import (
    INITIAL_CAPITAL, OPT_DAYS, LIVE_DAYS,
    TP_GRID, SL_GRID, TRAIL_DROP_GRID, EMA_LEN_GRID,
    TMA_LOW_MIN, TMA_LOW_MAX, TMA_HIGH_MIN, TMA_HIGH_MAX,
    TMA_LOW_MIN_GRID, TMA_LOW_MAX_GRID, TMA_HIGH_MIN_GRID, TMA_HIGH_MAX_GRID,
    WFO_BEST_PARAMS_PATH, FEE_RATE, BINANCE_INTERVAL,
    save_params,
)
from ema11_strategy import EMA11Strategy
from ema11_binance import wfo_bars
from ema11_wfo_scoring import score_params


def walk_forward_optimization(
    df              : pd.DataFrame,
    interval        : str   = BINANCE_INTERVAL,
    score_mode      : str   = "balanced",
    verbose         : bool  = True,
    on_window_done  = None,
    optimize_tma    : bool  = False,   # True → optymalizuj też strefy TMA(RSI)
    fee_rate        : float = FEE_RATE, # przekazywalne z zewnątrz (UI)
) -> tuple[pd.DataFrame, Optional[pd.DataFrame], pd.DataFrame, float]:
    """
    Walk-Forward Optimization.

    Parametry:
      df            – DataFrame ze wskaźnikami
      interval      – timeframe (przelicza dni → bary)
      score_mode    – "return_only" | "balanced" | "defensive"
      verbose       – drukuj postęp
      optimize_tma  – True → przeszukuj też siatki stref TMA(RSI)
                      (uwaga: znacznie wydłuża czas obliczeń)

    Zwraca: (all_trades_df, equity_df, windows_df, final_capital)
    """
    opt_bars, live_bars = wfo_bars(interval, OPT_DAYS, LIVE_DAYS)
    OPT_CAPITAL = 10_000.0

    n               = len(df)
    start           = 0
    window_id       = 0
    current_capital = INITIAL_CAPITAL

    all_live_trades : list[pd.DataFrame]    = []
    global_equity   : Optional[pd.DataFrame]= None
    window_stats    : list[dict]            = []

    # siatki stref TMA – jeśli nie optymalizujemy, używamy stałych domyślnych
    tma_low_min_grid  = TMA_LOW_MIN_GRID  if optimize_tma else [TMA_LOW_MIN]
    tma_low_max_grid  = TMA_LOW_MAX_GRID  if optimize_tma else [TMA_LOW_MAX]
    tma_high_min_grid = TMA_HIGH_MIN_GRID if optimize_tma else [TMA_HIGH_MIN]
    tma_high_max_grid = TMA_HIGH_MAX_GRID if optimize_tma else [TMA_HIGH_MAX]

    total_windows = max(0, (n - opt_bars) // live_bars)
    if verbose:
        print(f"[WFO] Świec: {n} | okna: ~{total_windows} | "
              f"opt={OPT_DAYS}d ({opt_bars} barów) | live={LIVE_DAYS}d ({live_bars} barów) | "
              f"score_mode={score_mode} | optimize_tma={optimize_tma}")
        print("─" * 70)

    while start + opt_bars + live_bars <= n:
        opt_slice  = df.iloc[start            : start + opt_bars]
        live_slice = df.iloc[start + opt_bars : start + opt_bars + live_bars]

        # ── faza optymalizacji ─────────────────────────────────────────────
        best_score  = -1e9
        best_params = None
        best_opt_trades = None
        best_opt_cap    = OPT_CAPITAL

        for ema_len in EMA_LEN_GRID:
            for tp in TP_GRID:
                for sl in SL_GRID:
                    for trail in TRAIL_DROP_GRID:
                        for tma_lmin in tma_low_min_grid:
                            for tma_lmax in tma_low_max_grid:
                                for tma_hmin in tma_high_min_grid:
                                    for tma_hmax in tma_high_max_grid:
                                        params = {
                                            "tp_pct"     : tp,
                                            "sl_pct"     : sl,
                                            "trail_drop" : trail,
                                            "ema_len"    : ema_len,
                                            "tma_low_min" : tma_lmin,
                                            "tma_low_max" : tma_lmax,
                                            "tma_high_min": tma_hmin,
                                            "tma_high_max": tma_hmax,
                                        }
                                        strat = EMA11Strategy(params, fee_rate=fee_rate)
                                        t, _, fc = strat.run(opt_slice, OPT_CAPITAL)
                                        s = score_params(t, fc, OPT_CAPITAL, mode=score_mode)
                                        if s > best_score:
                                            best_score      = s
                                            best_params     = params
                                            best_opt_trades = t
                                            best_opt_cap    = fc

        if best_params is None:
            if verbose:
                print(f"[WFO] Okno {window_id}: brak danych – przerywam.")
            break

        # ── metryki fazy optymalizacji ─────────────────────────────────────
        opt_n_trades = 0 if best_opt_trades is None or best_opt_trades.empty \
                       else len(best_opt_trades)
        opt_ret_pct  = (best_opt_cap / OPT_CAPITAL - 1.0) * 100.0
        opt_pf       = 0.0
        opt_max_dd   = 0.0
        if best_opt_trades is not None and not best_opt_trades.empty:
            wins   = best_opt_trades[best_opt_trades["pnl"] > 0]["pnl"].sum()
            losses = best_opt_trades[best_opt_trades["pnl"] <= 0]["pnl"].sum()
            opt_pf = wins / abs(losses) if losses < 0 else 0.0
            equity = np.array([OPT_CAPITAL] + list(
                OPT_CAPITAL + best_opt_trades["pnl"].cumsum().values
            ))
            running_max = np.maximum.accumulate(equity)
            dd_arr      = (equity - running_max) / running_max
            opt_max_dd  = dd_arr.min() * 100.0

        # ── faza live ──────────────────────────────────────────────────────
        strat = EMA11Strategy(best_params, fee_rate=fee_rate)
        trades_live, equity_live, final_cap_live = strat.run(live_slice, current_capital)

        if not trades_live.empty:
            t2 = trades_live.copy()
            t2["window_id"] = window_id
            all_live_trades.append(t2)

        if equity_live is not None and not equity_live.empty:
            global_equity = equity_live.copy() if global_equity is None else \
                pd.concat([global_equity, equity_live.iloc[1:]], ignore_index=True)

        live_ret_pct = (final_cap_live / current_capital - 1.0) * 100.0 \
                       if current_capital > 0 else 0.0
        n_trades = 0 if trades_live.empty else len(trades_live)

        window_stats.append({
            "window_id"      : window_id,
            "live_start"     : live_slice["time"].iloc[0],
            "live_end"       : live_slice["time"].iloc[-1],
            "best_tp"        : best_params["tp_pct"],
            "best_sl"        : best_params["sl_pct"],
            "best_trail"     : best_params["trail_drop"],
            "best_ema_len"   : best_params["ema_len"],
            "best_tma_lmin"  : best_params["tma_low_min"],
            "best_tma_lmax"  : best_params["tma_low_max"],
            "best_tma_hmin"  : best_params["tma_high_min"],
            "best_tma_hmax"  : best_params["tma_high_max"],
            "opt_score"      : best_score,
            "opt_return_pct" : opt_ret_pct,
            "opt_pf"         : opt_pf,
            "opt_max_dd_pct" : opt_max_dd,
            "opt_n_trades"   : opt_n_trades,
            "live_return_pct": live_ret_pct,
            "live_final_cap" : final_cap_live,
            "n_trades_live"  : n_trades,
        })

        if verbose:
            print(
                f"[WFO] {window_id:3d} | "
                f"{live_slice['time'].iloc[0].strftime('%Y-%m-%d')} → "
                f"{live_slice['time'].iloc[-1].strftime('%Y-%m-%d')} | "
                f"ret={live_ret_pct:+.2f}%  tr={n_trades}  "
                f"tp={best_params['tp_pct']:.3f}  sl={best_params['sl_pct']:.4f}  "
                f"trail={best_params['trail_drop']}  ema={best_params['ema_len']}"
            )

        if on_window_done is not None:
            on_window_done(
                window_id, total_windows,
                list(window_stats),
                list(all_live_trades),
                global_equity.copy() if global_equity is not None else None,
                current_capital,
            )

        current_capital = final_cap_live
        start          += live_bars
        window_id      += 1

    all_trades_df = pd.concat(all_live_trades, ignore_index=True) \
                    if all_live_trades else pd.DataFrame()
    windows_df = pd.DataFrame(window_stats)

    return all_trades_df, global_equity, windows_df, current_capital


def get_latest_best_params(windows_df: pd.DataFrame) -> dict:
    """
    Zwraca stabilne parametry z ostatnich 5 okien WFO.
    Zamiast brać parametry z ostatniego okna (podatne na szum),
    liczy medianę TP/SL/trail i modę EMA_LEN z 5 ostatnich okien.
    Uwzględnia tylko okna z co najmniej 2 transakcjami live (jeśli dostępne).
    """
    if windows_df is None or windows_df.empty:
        return {}

    recent = windows_df.tail(5).copy()

    # preferuj okna z transakcjami, o ile jest ich wystarczająco
    if "n_trades_live" in recent.columns:
        active = recent[recent["n_trades_live"] >= 2]
        if len(active) >= 2:
            recent = active

    tp    = float(recent["best_tp"].median())
    sl    = float(recent["best_sl"].median())
    trail = float(recent["best_trail"].median())
    ema   = int(recent["best_ema_len"].mode().iloc[0])

    # strefy TMA – mediana, jeśli były optymalizowane
    tma_lmin = float(recent["best_tma_lmin"].median()) \
               if "best_tma_lmin" in recent.columns else TMA_LOW_MIN
    tma_lmax = float(recent["best_tma_lmax"].median()) \
               if "best_tma_lmax" in recent.columns else TMA_LOW_MAX
    tma_hmin = float(recent["best_tma_hmin"].median()) \
               if "best_tma_hmin" in recent.columns else TMA_HIGH_MIN
    tma_hmax = float(recent["best_tma_hmax"].median()) \
               if "best_tma_hmax" in recent.columns else TMA_HIGH_MAX

    return {
        "tp_pct"      : tp,
        "sl_pct"      : sl,
        "trail_drop"  : trail,
        "ema_len"     : ema,
        "tma_low_min" : tma_lmin,
        "tma_low_max" : tma_lmax,
        "tma_high_min": tma_hmin,
        "tma_high_max": tma_hmax,
    }
