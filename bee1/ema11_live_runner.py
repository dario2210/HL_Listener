"""
ema11_live_runner.py
====================
Runner paper/live – używa DOKŁADNIE tej samej logiki co backtest (ema11_engine).

Punkty z briefu:
  [2]  Adapter wykonawczy bez własnej logiki strategii
  [9]  Tryb paper trading (symulacja bez wysyłania zleceń)
  [10] Mechanizmy bezpieczeństwa: max 1 pozycja, blokada duplikatów,
       recovery po restarcie, sanity check, limit dziennej straty, pause
  [15] Live safety: weryfikacja pozycji z giełdy po restarcie

Użycie:
  python ema11_live_runner.py              → paper trading (domyślnie)
  python ema11_live_runner.py --mode live  → live (wymaga exchange client)
  python ema11_live_runner.py --pause      → ustawia flagę PAUSED w state
"""

from __future__ import annotations

import json
import logging
import os
import sys
import time
import argparse
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

import numpy as np
import pandas as pd

from ema11_params  import (
    BINANCE_SYMBOL, BINANCE_INTERVAL, BINANCE_MARKET,
    FEE_RATE, INITIAL_CAPITAL,
    load_params,
)
from ema11_data    import prepare_indicators
from ema11_binance import update_csv_cache, get_bars_per_day
from ema11_engine  import (
    BarData, Signal, PositionState,
    generate_entry_signal, generate_exit_signal,
    compute_trade_close, apply_slippage, bar_from_row as _bar_from_row,
)

# ──────────────────────────────────────────────────────────────────────────────
# KONFIGURACJA
# ──────────────────────────────────────────────────────────────────────────────

STATE_FILE      = "ema11_live_state.json"
TRADES_LOG      = "ema11_live_trades.jsonl"
LOG_FILE        = "ema11_live_runner.log"
LOOP_SLEEP_SEC  = 60        # co ile sekund sprawdzamy nową świecę

# Bezpieczeństwo (punkt 10)
MAX_DAILY_LOSS_PCT  = 3.0   # % kapitału – po przekroczeniu runner zatrzymuje się
MAX_POSITIONS       = 1     # nigdy więcej niż 1 pozycja jednocześnie

# ──────────────────────────────────────────────────────────────────────────────
# LOGGER
# ──────────────────────────────────────────────────────────────────────────────

logging.basicConfig(
    level   = logging.INFO,
    format  = "%(asctime)s [%(levelname)s] %(message)s",
    handlers= [
        logging.FileHandler(LOG_FILE),
        logging.StreamHandler(sys.stdout),
    ],
)
log = logging.getLogger("ema11_live")

# ──────────────────────────────────────────────────────────────────────────────
# STATE – zapis / odczyt (punkt 10: recovery po restarcie)
# ──────────────────────────────────────────────────────────────────────────────

def load_state() -> dict:
    if not Path(STATE_FILE).exists():
        return {
            "position"        : None,   # None | "long" | "short"
            "entry_price"     : None,
            "entry_time"      : None,
            "rsi_peak"        : None,
            "over_zone"       : False,
            "bars_in_position": 0,
            "capital"         : INITIAL_CAPITAL,
            "capital_at_open" : None,   # kapitał z momentu otwarcia pozycji
            "last_bar_time"   : None,   # blokada duplikatów (punkt 10)
            "daily_loss_usd"  : 0.0,
            "daily_date"      : None,
            "paused"          : False,
            "mode"            : "paper",
        }
    with open(STATE_FILE) as f:
        return json.load(f)


def save_state(state: dict) -> None:
    with open(STATE_FILE, "w") as f:
        json.dump(state, f, indent=2, default=str)


def log_trade(trade: dict) -> None:
    with open(TRADES_LOG, "a") as f:
        f.write(json.dumps(trade, default=str) + "\n")


# ──────────────────────────────────────────────────────────────────────────────
# MECHANIZMY BEZPIECZEŃSTWA (punkt 10)
# ──────────────────────────────────────────────────────────────────────────────

def check_daily_loss(state: dict, capital: float) -> bool:
    """Zwraca True jeśli dzienny limit straty przekroczony → należy zatrzymać."""
    today = datetime.now(timezone.utc).date().isoformat()
    if state.get("daily_date") != today:
        state["daily_date"]     = today
        state["daily_loss_usd"] = 0.0

    loss_pct = state["daily_loss_usd"] / capital * 100.0
    if loss_pct >= MAX_DAILY_LOSS_PCT:
        log.warning(f"[SAFETY] Dzienny limit straty osiągnięty: {loss_pct:.2f}% "
                    f"(limit: {MAX_DAILY_LOSS_PCT}%). Runner zatrzymany.")
        return True
    return False


def is_duplicate_bar(state: dict, bar_time: str) -> bool:
    """Blokada podwójnego przetworzenia tej samej świecy (punkt 10)."""
    if state.get("last_bar_time") == bar_time:
        return True
    return False


# ──────────────────────────────────────────────────────────────────────────────
# LIVE SAFETY: weryfikacja pozycji z giełdy (punkt 15)
# ──────────────────────────────────────────────────────────────────────────────

def sync_position_with_exchange(state: dict, mode: str) -> None:
    """
    Weryfikuje lokalny state z rzeczywistą pozycją na giełdzie.
    W trybie live: pobiera pozycję z giełdy i porównuje z state.
    Przy rozbieżności: loguje alert i blokuje nowe wejścia do ręcznej akceptacji.

    UWAGA: Wymaga podłączenia klienta giełdy – stub do implementacji.
    """
    if mode != "live":
        return

    # live stub – zastąp własnym klientem giełdy
    log.warning(
        "[LIVE-SAFETY] sync_position_with_exchange() nie jest zaimplementowane. "
        "Podłącz klienta giełdy i pobierz realną pozycję. "
        "Do czasu implementacji lokalne state jest jedynym źródłem prawdy – "
        "w produkcji to NIEWYSTARCZAJĄCE."
    )
    # Przykładowa logika po podłączeniu klienta:
    #   exchange_pos = exchange_client.get_position(BINANCE_SYMBOL)
    #   local_pos    = state.get("position")
    #   if exchange_pos != local_pos:
    #       log.error(f"[SYNC] Rozbieżność: giełda={exchange_pos}, local={local_pos}")
    #       state["paused"] = True
    #       save_state(state)


# ──────────────────────────────────────────────────────────────────────────────
# REKONSTRUKCJA STANU POZYCJI Z STATE
# ──────────────────────────────────────────────────────────────────────────────

def position_from_state(state: dict) -> Optional[PositionState]:
    if not state.get("position"):
        return None
    return PositionState(
        side             = state["position"],
        entry_price      = float(state["entry_price"]),
        entry_time       = state["entry_time"],
        rsi_peak         = state.get("rsi_peak"),
        over_zone        = state.get("over_zone", False),
        bars_in_position = int(state.get("bars_in_position", 0)),
    )


def position_to_state(state: dict, pos: Optional[PositionState]) -> None:
    if pos is None:
        state["position"]         = None
        state["entry_price"]      = None
        state["entry_time"]       = None
        state["rsi_peak"]         = None
        state["over_zone"]        = False
        state["bars_in_position"] = 0
        state["capital_at_open"]  = None
    else:
        state["position"]         = pos.side
        state["entry_price"]      = pos.entry_price
        state["entry_time"]       = str(pos.entry_time)
        state["rsi_peak"]         = pos.rsi_peak
        state["over_zone"]        = pos.over_zone
        state["bars_in_position"] = pos.bars_in_position


# ──────────────────────────────────────────────────────────────────────────────
# EGZEKUCJA (paper / live stub)
# ──────────────────────────────────────────────────────────────────────────────

def execute_order(
    side       : str,
    action     : str,   # "open" | "close"
    price      : float,
    capital    : float,
    mode       : str,   # "paper" | "live"
    reason     : str = "",
) -> float:
    """
    Wysyła zlecenie lub symuluje je (paper).
    Zwraca rzeczywistą cenę egzekucji.

    W trybie live: TUTAJ podłącz klienta giełdy (HL / Binance).
    Moduł nie zawiera własnej logiki strategii – tylko wykonuje decyzję.
    """
    if mode == "paper":
        log.info(f"[PAPER] {action.upper()} {side.upper()} @ {price:.2f}  "
                 f"capital={capital:.2f}  reason={reason}")
        return price   # paper: egzekucja po dokładnie tej cenie

    # live stub – zastąp własnym klientem giełdy
    log.warning("[LIVE] Stub egzekucji – podłącz klienta giełdy!")
    raise NotImplementedError(
        "Tryb live wymaga podłączenia klienta giełdy. "
        "Zaimplementuj execute_order() w ema11_live_runner.py."
    )


# ──────────────────────────────────────────────────────────────────────────────
# JEDEN PRZEBIEG (jedna świeca)
# ──────────────────────────────────────────────────────────────────────────────

def process_bar(
    bar    : BarData,
    prev   : BarData,
    params : dict,
    state  : dict,
    mode   : str,
) -> None:
    """
    Przetwarza jedną zamkniętą świecę.
    Wywołuje DOKŁADNIE tę samą logikę co backtest (ema11_engine).
    """
    bar_time_str = str(bar.time)
    capital      = float(state.get("capital", INITIAL_CAPITAL))
    fee_rate     = params.get("fee_rate", FEE_RATE)
    slip_bps     = params.get("slippage_bps", 0.0)
    spread_bps   = params.get("spread_bps",   0.0)

    # ── blokada duplikatów (punkt 10) ──────────────────────────────────────
    if is_duplicate_bar(state, bar_time_str):
        log.debug(f"[SKIP] Duplikat baru: {bar_time_str}")
        return

    # ── bezpieczeństwo: dzienny limit straty ───────────────────────────────
    if check_daily_loss(state, capital):
        state["paused"] = True
        save_state(state)
        return

    # ── bezpieczeństwo: pause ──────────────────────────────────────────────
    if state.get("paused"):
        log.info("[PAUSED] Runner wstrzymany – usuń flagę paused w state.")
        return

    # ── rekonstrukcja pozycji ─────────────────────────────────────────────
    position = position_from_state(state)

    # ── obsługa otwartej pozycji: exit signal ─────────────────────────────
    if position is not None:
        sig = generate_exit_signal(bar, prev, params, position)
        if sig.action != "none":
            raw_exit   = sig.exit_price if sig.exit_price else bar.close
            exec_price = apply_slippage(raw_exit, position.side, "close",
                                        slip_bps, spread_bps)
            exec_price = execute_order(
                position.side, "close", exec_price, capital, mode, sig.reason
            )

            # kapitał z momentu otwarcia (punkt 2: capital_at_open)
            capital_at_open = float(state.get("capital_at_open") or capital)

            result  = compute_trade_close(
                entry_price     = position.entry_price,
                exit_price      = exec_price,
                side            = position.side,
                fee_rate        = fee_rate,
                capital_at_open = capital_at_open,
            )
            gross_ret = result["gross_ret"]
            net_ret   = result["net_ret"]
            fee_usd   = result["fee_usd"]
            pnl       = result["pnl"]

            # aktualizacja dziennej straty
            if pnl < 0:
                state["daily_loss_usd"] = state.get("daily_loss_usd", 0.0) + abs(pnl)

            capital += pnl
            state["capital"] = capital

            trade_log = {
                "ts"             : bar_time_str,
                "side"           : position.side,
                "entry_price"    : position.entry_price,
                "exit_price"     : exec_price,
                "gross_ret"      : gross_ret,
                "net_ret"        : net_ret,
                "pnl_usd"        : pnl,
                "fee_usd"        : fee_usd,
                "reason"         : sig.reason,
                "capital"        : capital,
                "capital_at_open": capital_at_open,
                "mode"           : mode,
            }
            log_trade(trade_log)
            log.info(f"[CLOSE] {position.side.upper()} | "
                     f"entry={position.entry_price:.2f} exit={exec_price:.2f} | "
                     f"net={net_ret*100:.3f}% | pnl={pnl:.2f} USD | {sig.reason}")

            position_to_state(state, None)
        else:
            # zapisz zaktualizowany bars_in_position z powrotem do state
            state["bars_in_position"] = position.bars_in_position

    # ── bezpieczeństwo: max 1 pozycja (punkt 10) ──────────────────────────
    if state.get("position") is None:
        sig = generate_entry_signal(bar, prev, params, None)

        if sig.action in ("open_long", "open_short"):
            side        = "long" if sig.action == "open_long" else "short"
            raw_entry   = bar.close
            exec_price  = apply_slippage(raw_entry, side, "open",
                                         slip_bps, spread_bps)
            exec_price  = execute_order(side, "open", exec_price, capital, mode, sig.reason)

            new_pos = PositionState(side=side, entry_price=exec_price,
                                    entry_time=bar_time_str)
            position_to_state(state, new_pos)
            # zapisz capital_at_open w momencie otwarcia (punkt 2)
            state["capital_at_open"] = capital
            log.info(f"[OPEN]  {side.upper()} @ {exec_price:.2f} | {sig.reason}")

    # ── zapisz timestamp baru (blokada duplikatów) ─────────────────────────
    state["last_bar_time"] = bar_time_str
    save_state(state)


# ──────────────────────────────────────────────────────────────────────────────
# GŁÓWNA PĘTLA
# ──────────────────────────────────────────────────────────────────────────────

def main_loop(mode: str = "paper") -> None:
    log.info(f"=== ema11_live_runner START | mode={mode} | "
             f"symbol={BINANCE_SYMBOL} interval={BINANCE_INTERVAL} ===")

    params = load_params()
    state  = load_state()
    state["mode"] = mode
    save_state(state)

    # punkt 15: weryfikacja pozycji z giełdy po restarcie
    sync_position_with_exchange(state, mode)

    csv_path = f"{BINANCE_SYMBOL.lower()}_{BINANCE_INTERVAL}.csv"

    while True:
        try:
            # ── pobierz / zaktualizuj dane ─────────────────────────────────
            df_raw = update_csv_cache(
                csv_path = csv_path,
                symbol   = BINANCE_SYMBOL,
                interval = BINANCE_INTERVAL,
                market   = BINANCE_MARKET,
                verbose  = False,
            )
            df = prepare_indicators(df_raw)
            df = df.dropna(subset=["rsi", "tma_rsi"]).reset_index(drop=True)

            # punkt 1: potrzebujemy min 3 wierszy – bierzemy [-2] i [-3]
            if len(df) < 3:
                log.warning("Za mało danych po wskaźnikach.")
                time.sleep(LOOP_SLEEP_SEC)
                continue

            ema_col = f"ema_{params['ema_len']}"
            if ema_col not in df.columns:
                log.error(f"Brak kolumny {ema_col}.")
                time.sleep(LOOP_SLEEP_SEC)
                continue

            # ── weź OSTATNIĄ ZAMKNIĘTĄ świecę (punkt 1: closed candle only) ─
            # df.iloc[-1] to bieżąca, jeszcze tworząca się świeca → pomijamy
            # df.iloc[-2] to ostatnia ZAMKNIĘTA świeca → to jest "bar"
            # df.iloc[-3] to świeca przed nią → to jest "prev"
            bar  = _bar_from_row(df.iloc[-2], ema_col)
            prev = _bar_from_row(df.iloc[-3], ema_col)

            log.info(f"Bar: {bar.time}  close={bar.close:.2f}  "
                     f"rsi={bar.rsi:.2f}  tma={bar.tma_rsi:.2f}  ema={bar.ema:.2f}")

            process_bar(bar, prev, params, state, mode)

        except KeyboardInterrupt:
            log.info("Runner zatrzymany przez użytkownika.")
            break
        except Exception as e:
            log.exception(f"Błąd w pętli głównej: {e!r}")

        time.sleep(LOOP_SLEEP_SEC)


# ──────────────────────────────────────────────────────────────────────────────
# CLI
# ──────────────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    p = argparse.ArgumentParser(description="EMA-11 Live/Paper Runner")
    p.add_argument("--mode",  default="paper", choices=["paper","live"],
                   help="Tryb: paper (domyślny) lub live")
    p.add_argument("--pause", action="store_true",
                   help="Ustaw flagę PAUSED w state i wyjdź")
    p.add_argument("--resume",action="store_true",
                   help="Usuń flagę PAUSED i wyjdź")
    args = p.parse_args()

    if args.pause:
        state = load_state()
        state["paused"] = True
        save_state(state)
        print("[PAUSE] Runner wstrzymany.")
        sys.exit(0)

    if args.resume:
        state = load_state()
        state["paused"] = False
        save_state(state)
        print("[RESUME] Runner wznowiony.")
        sys.exit(0)

    main_loop(mode=args.mode)
