"""
ema11_main.py
=============
Entry point bota EMA-11 (RSI + TMA + EMA Trend Filter).

Tryby uruchomienia:
  python ema11_main.py                         → backtest z domyślnymi parametrami (CSV)
  python ema11_main.py --fetch                 → pobierz/aktualizuj dane z Binance, potem backtest
  python ema11_main.py --mode wfo --fetch      → WFO na świeżych danych z Binance
  python ema11_main.py --mode backtest --start 2023-01-01 --end 2024-12-31

Flagi:
  --mode      backtest | wfo           (domyślnie: backtest)
  --fetch                              (pobierz dane z Binance przed uruchomieniem)
  --symbol    ETHUSDT                  (para, domyślnie z params)
  --interval  1h                       (timeframe: 1m,5m,15m,30m,1h,4h,1d)
  --market    spot | futures           (domyślnie: spot)
  --start     YYYY-MM-DD              (filtr daty od, dla backtest)
  --end       YYYY-MM-DD              (filtr daty do, dla backtest)
  --freq      ME | QE | YE            (rozliczenie opłat, domyślnie ME)
  --save                              (zapisz wyniki do CSV)
  --csv       ścieżka                 (nadpisz ścieżkę pliku CSV)
"""

from __future__ import annotations

import argparse
import sys
import pandas as pd

from ema11_params   import (
    CSV_PATH, INITIAL_CAPITAL, WFO_BEST_PARAMS_PATH,
    BINANCE_SYMBOL, BINANCE_INTERVAL, BINANCE_MARKET, BINANCE_START_DATE,
    BINANCE_CSV_CACHE,
    save_params, load_params,
)
from ema11_data     import load_klines, prepare_indicators
from ema11_strategy import EMA11Strategy
from ema11_wfo      import walk_forward_optimization, get_latest_best_params
from ema11_stats    import compute_stats, print_wfo_windows, fee_summary_by_period
from ema11_binance  import update_csv_cache, wfo_bars


# ──────────────────────────────────────────────────────────────────────────────
# BACKTEST SIMPLE
# ──────────────────────────────────────────────────────────────────────────────

def run_backtest(
    df      : pd.DataFrame,
    params  : dict,
    start   : str | None = None,
    end     : str | None = None,
    freq    : str        = "ME",
    save    : bool       = False,
) -> None:
    """Prosty backtest z jednym zestawem parametrów."""

    # filtr daty
    if start:
        df = df[df["time"] >= pd.Timestamp(start, tz="UTC")]
    if end:
        df = df[df["time"] <= pd.Timestamp(end,   tz="UTC")]
    df = df.reset_index(drop=True)

    print(f"\n[BACKTEST] Dane: {df['time'].iloc[0]} → {df['time'].iloc[-1]}  ({len(df)} świec)")
    print(f"[BACKTEST] Parametry: {params}\n")

    strat = EMA11Strategy(params)
    trades, equity, final_cap = strat.run(df, INITIAL_CAPITAL)

    stats = compute_stats(trades, equity, INITIAL_CAPITAL, label="BACKTEST – RSI+TMA+EMA")

    # zestawienie opłat w czasie
    if not trades.empty:
        fee_table = fee_summary_by_period(trades, INITIAL_CAPITAL, freq=freq)
        if not fee_table.empty:
            freq_label = {"ME": "miesięczne", "QE": "kwartalne", "YE": "roczne"}.get(freq, freq)
            print(f"\n── Opłaty ({freq_label}) ──────────────────────────────────────")
            print(fee_table.to_string(index=False, float_format=lambda x: f"{x:.4f}"))

    if save:
        trades.to_csv("ema11_backtest_trades.csv", index=False)
        equity.to_csv("ema11_backtest_equity.csv",  index=False)
        print("\n[SAVE] ema11_backtest_trades.csv, ema11_backtest_equity.csv")


# ──────────────────────────────────────────────────────────────────────────────
# WFO
# ──────────────────────────────────────────────────────────────────────────────

def run_wfo(
    df       : pd.DataFrame,
    interval : str  = BINANCE_INTERVAL,
    freq     : str  = "ME",
    save     : bool = False,
    verbose  : bool = True,
) -> None:
    """Walk-Forward Optimization."""

    all_trades, equity_wfo, windows_df, final_cap = walk_forward_optimization(
        df, interval=interval, verbose=verbose
    )

    # statystyki globalne
    compute_stats(
        all_trades, equity_wfo, INITIAL_CAPITAL,
        label="WFO – RSI+TMA+EMA TREND (live okna)"
    )

    # statystyki okien
    print_wfo_windows(windows_df)

    # zestawienie opłat w czasie
    if all_trades is not None and not all_trades.empty:
        fee_table = fee_summary_by_period(all_trades, INITIAL_CAPITAL, freq=freq)
        if not fee_table.empty:
            freq_label = {"ME": "miesięczne", "QE": "kwartalne", "YE": "roczne"}.get(freq, freq)
            print(f"\n── Opłaty ({freq_label}) ──────────────────────────────────────")
            print(fee_table.to_string(index=False, float_format=lambda x: f"{x:.4f}"))

    # zapis stabilnych parametrów z ostatnich 5 okien (mediana TP/SL/trail, moda EMA)
    if windows_df is not None and not windows_df.empty:
        best = get_latest_best_params(windows_df)
        save_params(best)
        print(f"\n[WFO] Stabilne parametry (5 okien) zapisane → {WFO_BEST_PARAMS_PATH}")
        print(f"      {best}")

    if save:
        if all_trades is not None and not all_trades.empty:
            all_trades.to_csv("ema11_wfo_trades.csv", index=False)
        if equity_wfo is not None:
            equity_wfo.to_csv("ema11_wfo_equity.csv", index=False)
        if windows_df is not None and not windows_df.empty:
            windows_df.to_csv("ema11_wfo_windows.csv", index=False)
        print("[SAVE] ema11_wfo_trades.csv, ema11_wfo_equity.csv, ema11_wfo_windows.csv")


# ──────────────────────────────────────────────────────────────────────────────
# CLI
# ──────────────────────────────────────────────────────────────────────────────

def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="EMA-11 Bot – RSI + TMA(RSI) + EMA Trend Filter"
    )
    p.add_argument("--mode",     choices=["backtest", "wfo"], default="backtest")
    p.add_argument("--fetch",    action="store_true",
                   help="Pobierz/zaktualizuj dane z Binance przed uruchomieniem")
    p.add_argument("--symbol",   default=BINANCE_SYMBOL,   help="Para np. ETHUSDT")
    p.add_argument("--interval", default=BINANCE_INTERVAL, help="Timeframe: 1h,4h,1d itd.")
    p.add_argument("--market",   default=BINANCE_MARKET,   choices=["spot","futures"])
    p.add_argument("--since",    default=BINANCE_START_DATE, help="Data od dla Binance (YYYY-MM-DD)")
    p.add_argument("--start",    default=None,  help="Filtr daty od dla backtest (YYYY-MM-DD)")
    p.add_argument("--end",      default=None,  help="Filtr daty do dla backtest (YYYY-MM-DD)")
    p.add_argument("--freq",     default="ME",  help="Opłaty: ME/QE/YE")
    p.add_argument("--save",     action="store_true")
    p.add_argument("--csv",      default=None,
                   help="Ścieżka CSV (domyślnie: z params lub {symbol}_{interval}.csv)")
    return p.parse_args()


def main() -> None:
    args = parse_args()

    interval = args.interval

    # ── ustal ścieżkę CSV ────────────────────────────────────────────────────
    if args.csv:
        csv_path = args.csv
    elif BINANCE_CSV_CACHE:
        csv_path = BINANCE_CSV_CACHE
    elif args.fetch:
        csv_path = f"{args.symbol.lower()}_{interval}.csv"
    else:
        csv_path = CSV_PATH

    # ── opcjonalne pobieranie z Binance ──────────────────────────────────────
    if args.fetch:
        print(f"[MAIN] Pobieranie danych z Binance: {args.symbol} {interval} ({args.market})")
        update_csv_cache(
            csv_path   = csv_path,
            symbol     = args.symbol,
            interval   = interval,
            start_date = args.since,
            market     = args.market,
            verbose    = True,
        )
        print()

    # ── wczytanie danych ─────────────────────────────────────────────────────
    print(f"[MAIN] Wczytuję dane: {csv_path}")
    df = load_klines(csv_path)
    df = prepare_indicators(df)
    print(f"[MAIN] Załadowano {len(df)} świec  ({df['time'].iloc[0]} → {df['time'].iloc[-1]})")

    # info o WFO barach dla wybranego TF
    ob, lb = wfo_bars(interval, 90, 14)   # 90/14 dni – domyślne okna
    print(f"[MAIN] Timeframe: {interval}  |  WFO okna: opt={ob} barów / live={lb} barów")
    print()

    if args.mode == "wfo":
        run_wfo(df, interval=interval, freq=args.freq, save=args.save, verbose=True)
    else:
        params = load_params()
        run_backtest(df, params, start=args.start, end=args.end,
                     freq=args.freq, save=args.save)


if __name__ == "__main__":
    main()
