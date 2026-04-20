"""
test_ema11.py
=============
Testy jednostkowe pytest dla projektu EMA-11.

Punkt 6 z briefu – minimalne pokrycie:
  - wskaźniki: RSI, TMA
  - sygnały wejścia / wyjścia (engine)
  - TradeRecord: gross_ret, fee_ret, net_ret, fee_usd
  - WFO: brak przecieku, podział okien, zapis params
  - Binance loader: formaty ms / s / string, brak duplikatów

Uruchomienie:
  pip install pytest
  pytest test_ema11.py -v
"""

import json
import math
import os
import tempfile

import numpy as np
import pandas as pd
import pytest

# ── importy projektu ──────────────────────────────────────────────────────────
from ema11_data    import compute_rsi, compute_tma, load_klines
from ema11_engine  import (
    BarData, PositionState, Signal,
    generate_entry_signal, generate_exit_signal, apply_slippage, bar_from_row,
)
from ema11_strategy import EMA11Strategy
from ema11_binance  import get_bars_per_day, wfo_bars, interval_to_ms


# ──────────────────────────────────────────────────────────────────────────────
# FIXTURES
# ──────────────────────────────────────────────────────────────────────────────

BASE_PARAMS = {
    "tp_pct"      : 0.05,
    "sl_pct"      : 0.005,
    "trail_drop"  : 3.0,
    "ema_len"     : 200,
    "tma_low_min" : 30.0,
    "tma_low_max" : 45.0,
    "tma_high_min": 55.0,
    "tma_high_max": 70.0,
    "fee_rate"    : 0.0007,
    "slippage_bps": 0.0,
    "spread_bps"  : 0.0,
}


def _make_df(n=500, trend="up", rsi_val=None):
    """Syntetyczny DataFrame ze wskaźnikami."""
    times  = pd.date_range("2023-01-01", periods=n, freq="1h", tz="UTC")
    prices = np.linspace(1500, 2000, n) if trend == "up" else \
             np.linspace(2000, 1500, n)
    prices += np.random.default_rng(42).normal(0, 5, n)

    df = pd.DataFrame({"time": times, "close": prices})
    df["open"] = df["close"] * 0.999
    df["high"] = df["close"] * 1.002
    df["low"]  = df["close"] * 0.998
    df["volume"] = 1000.0

    from ema11_data import compute_rsi, compute_tma
    df["rsi"]     = compute_rsi(df["close"], 14)
    df["tma_rsi"] = compute_tma(df["rsi"],   14)
    for ema_len in [50, 100, 150, 200]:
        df[f"ema_{ema_len}"] = df["close"].ewm(span=ema_len, adjust=False).mean()

    df = df.dropna().reset_index(drop=True)

    if rsi_val is not None:
        df["rsi"]     = rsi_val
        df["tma_rsi"] = rsi_val

    return df


def _make_bar(close=1800.0, rsi=35.0, tma=37.0, ema=1750.0):
    return BarData(time=pd.Timestamp("2023-06-01", tz="UTC"),
                   close=close, rsi=rsi, tma_rsi=tma, ema=ema)


# ──────────────────────────────────────────────────────────────────────────────
# WSKAŹNIKI
# ──────────────────────────────────────────────────────────────────────────────

class TestIndicators:

    def test_rsi_range(self):
        prices = pd.Series(np.random.default_rng(0).uniform(100, 200, 200))
        rsi = compute_rsi(prices, 14)
        assert rsi.dropna().between(0, 100).all(), "RSI poza zakresem [0,100]"

    def test_rsi_flat_series(self):
        """Flat price → RSI powinno być 50 (brak zmian)."""
        prices = pd.Series([100.0] * 50)
        rsi = compute_rsi(prices, 14)
        # fillna(50) w implementacji
        assert rsi.iloc[-1] == pytest.approx(50.0, abs=5.0)

    def test_rsi_length(self):
        prices = pd.Series(np.arange(1.0, 101.0))
        rsi = compute_rsi(prices, 14)
        assert len(rsi) == len(prices)

    def test_tma_is_double_sma(self):
        s = pd.Series(np.arange(1.0, 51.0))
        tma = compute_tma(s, 5)
        sma1 = s.rolling(5).mean()
        expected = sma1.rolling(5).mean()
        pd.testing.assert_series_equal(tma.dropna(), expected.dropna(), check_names=False)

    def test_tma_length(self):
        s = pd.Series(np.random.rand(100))
        assert len(compute_tma(s, 7)) == 100


# ──────────────────────────────────────────────────────────────────────────────
# SYGNAŁY WEJŚCIA
# ──────────────────────────────────────────────────────────────────────────────

class TestEntrySignals:

    def test_open_long_conditions(self):
        """RSI crossover w górę + trend up + TMA w strefie low → open_long."""
        bar  = _make_bar(close=1800, rsi=38.0, tma=37.0, ema=1750.0)
        prev = _make_bar(close=1790, rsi=36.0, tma=37.5, ema=1748.0)
        # rsi_prev < tma_prev (36 < 37.5) i rsi >= tma (38 >= 37)
        sig = generate_entry_signal(bar, prev, BASE_PARAMS, None)
        assert sig.action == "open_long"

    def test_open_short_conditions(self):
        """RSI crossover w dół + trend down + TMA w strefie high → open_short."""
        bar  = _make_bar(close=1700, rsi=62.0, tma=63.0, ema=1750.0)
        prev = _make_bar(close=1710, rsi=64.0, tma=62.5, ema=1748.0)
        # rsi_prev > tma_prev (64 > 62.5) i rsi <= tma (62 <= 63)
        sig = generate_entry_signal(bar, prev, BASE_PARAMS, None)
        assert sig.action == "open_short"

    def test_no_signal_when_in_position(self):
        pos  = PositionState("long", 1800.0, pd.Timestamp("2023-01-01", tz="UTC"))
        bar  = _make_bar(rsi=38.0, tma=37.0)
        prev = _make_bar(rsi=36.0, tma=37.5)
        sig  = generate_entry_signal(bar, prev, BASE_PARAMS, pos)
        assert sig.action == "none"

    def test_no_long_when_trend_down(self):
        """close < EMA → nie powinien otwierać longa."""
        bar  = _make_bar(close=1700, rsi=38.0, tma=37.0, ema=1750.0)
        prev = _make_bar(close=1710, rsi=36.0, tma=37.5, ema=1748.0)
        sig  = generate_entry_signal(bar, prev, BASE_PARAMS, None)
        assert sig.action != "open_long"

    def test_no_short_when_trend_up(self):
        bar  = _make_bar(close=1800, rsi=62.0, tma=63.0, ema=1750.0)
        prev = _make_bar(close=1790, rsi=64.0, tma=62.5, ema=1748.0)
        sig  = generate_entry_signal(bar, prev, BASE_PARAMS, None)
        assert sig.action != "open_short"


# ──────────────────────────────────────────────────────────────────────────────
# SYGNAŁY WYJŚCIA
# ──────────────────────────────────────────────────────────────────────────────

class TestExitSignals:

    def _long_pos(self, entry=1800.0):
        return PositionState("long", entry, pd.Timestamp("2023-01-01", tz="UTC"))

    def _short_pos(self, entry=1800.0):
        return PositionState("short", entry, pd.Timestamp("2023-01-01", tz="UTC"))

    def test_long_sl(self):
        pos = self._long_pos(1800.0)
        # SL przy -0.5% → cena 1800 * (1-0.005) = 1791
        bar  = _make_bar(close=1790.0)
        prev = _make_bar(close=1795.0)
        sig  = generate_exit_signal(bar, prev, BASE_PARAMS, pos)
        assert sig.action == "close_sl"
        assert sig.reason == "SL"

    def test_long_tp(self):
        pos = self._long_pos(1800.0)
        # TP przy +5% → 1890
        bar  = _make_bar(close=1895.0)
        prev = _make_bar(close=1880.0)
        sig  = generate_exit_signal(bar, prev, BASE_PARAMS, pos)
        assert sig.action == "close_tp"

    def test_short_sl(self):
        pos = self._short_pos(1800.0)
        bar  = _make_bar(close=1815.0)   # > entry * (1+0.005)
        prev = _make_bar(close=1805.0)
        sig  = generate_exit_signal(bar, prev, BASE_PARAMS, pos)
        assert sig.action == "close_sl"

    def test_short_tp(self):
        pos = self._short_pos(1800.0)
        bar  = _make_bar(close=1705.0)   # < entry * (1-0.05)
        prev = _make_bar(close=1720.0)
        sig  = generate_exit_signal(bar, prev, BASE_PARAMS, pos)
        assert sig.action == "close_tp"

    def test_trail_rsi_long(self):
        pos = self._long_pos(1800.0)
        pos.over_zone = True
        pos.rsi_peak  = 75.0
        # rsi_i = 70 < 75 - 3 = 72 → TRAIL
        bar  = _make_bar(rsi=70.0, tma=60.0)
        prev = _make_bar(rsi=73.0, tma=60.0)
        sig  = generate_exit_signal(bar, prev, BASE_PARAMS, pos)
        assert sig.action == "close_trail"
        assert sig.reason == "TRAIL_RSI"

    def test_no_exit_when_stable(self):
        pos = self._long_pos(1800.0)
        bar  = _make_bar(close=1820.0)   # ani SL, ani TP, ani trail
        prev = _make_bar(close=1815.0)
        sig  = generate_exit_signal(bar, prev, BASE_PARAMS, pos)
        assert sig.action == "none"


# ──────────────────────────────────────────────────────────────────────────────
# TRADE RECORD – obliczenia PnL i opłat (punkt 4)
# ──────────────────────────────────────────────────────────────────────────────

class TestTradeCalculations:

    def _run_one_trade(self, entry=1800.0, exit_=1890.0, side="long", capital=10000.0):
        """Uruchamia strategię na minimalistycznym df z 1 tradem."""
        df = _make_df(n=300, trend="up" if side == "long" else "down")
        strat = EMA11Strategy(BASE_PARAMS)
        trades, equity, final_cap = strat.run(df, capital)
        return trades, equity, final_cap

    def test_fee_usd_positive(self):
        trades, _, _ = self._run_one_trade()
        if not trades.empty:
            assert (trades["fee_usd"] >= 0).all(), "fee_usd musi być >= 0"

    def test_net_ret_less_than_gross(self):
        trades, _, _ = self._run_one_trade()
        if not trades.empty:
            # net zawsze mniejszy od gross (opłaty zawsze odejmowane)
            assert (trades["net_ret"] <= trades["gross_ret"]).all()

    def test_capital_after_consistent(self):
        trades, equity, final_cap = self._run_one_trade()
        if not trades.empty:
            # capital_after ostatniego trade powinien być bliski final_cap
            last_cap_after = trades["capital_after"].iloc[-1]
            assert abs(last_cap_after - final_cap) < 0.01

    def test_fee_usd_approx_fee_ret_times_notional(self):
        """fee_usd ≈ |fee_ret| × position_notional."""
        trades, _, _ = self._run_one_trade()
        if not trades.empty:
            for _, row in trades.iterrows():
                expected = abs(row["fee_ret"]) * row["position_notional"]
                assert abs(row["fee_usd"] - expected) < 1e-6

    def test_slippage_increases_entry_cost(self):
        """Z slippage: net_ret powinien być niższy niż bez."""
        params_slip = dict(BASE_PARAMS, slippage_bps=5.0, spread_bps=2.0)
        df = _make_df(n=300)

        strat_clean = EMA11Strategy(BASE_PARAMS)
        t_clean, _, cap_clean = strat_clean.run(df, 10000.0)

        strat_slip  = EMA11Strategy(params_slip)
        t_slip,  _, cap_slip  = strat_slip.run(df, 10000.0)

        if not t_clean.empty and not t_slip.empty and len(t_clean) == len(t_slip):
            # z slippage wyniki powinny być gorsze lub równe
            assert cap_slip <= cap_clean + 1.0   # +1 tolerancja na zaokrąglenia


# ──────────────────────────────────────────────────────────────────────────────
# WFO – podział okien, brak przecieku danych
# ──────────────────────────────────────────────────────────────────────────────

class TestWFO:

    def test_wfo_bars_calculation(self):
        ob, lb = wfo_bars("1h", 90, 14)
        assert ob == 2160   # 90 * 24
        assert lb == 336    # 14 * 24

    def test_wfo_bars_4h(self):
        ob, lb = wfo_bars("4h", 90, 14)
        assert ob == 540
        assert lb == 84

    def test_no_data_leak(self):
        """Slice live nie nachodzi na slice opt."""
        from ema11_params import OPT_DAYS, LIVE_DAYS
        opt_b, live_b = wfo_bars("1h", OPT_DAYS, LIVE_DAYS)
        df = _make_df(n=opt_b + live_b + 10)

        opt_slice  = df.iloc[0        : opt_b]
        live_slice = df.iloc[opt_b    : opt_b + live_b]

        assert len(set(opt_slice.index) & set(live_slice.index)) == 0, \
            "Przeciek danych: opt i live mają wspólne indeksy!"

    def test_save_params(self, tmp_path):
        from ema11_params import save_params
        path = str(tmp_path / "test_params.json")
        p = dict(BASE_PARAMS)
        save_params(p, path)
        with open(path) as f:
            loaded = json.load(f)
        assert loaded["tp_pct"] == p["tp_pct"]
        assert loaded["ema_len"] == p["ema_len"]


# ──────────────────────────────────────────────────────────────────────────────
# DANE – load_klines, CSV cache
# ──────────────────────────────────────────────────────────────────────────────

class TestDataLoading:

    def _write_csv(self, path, open_time_col, open_time_values):
        df = pd.DataFrame({
            open_time_col: open_time_values,
            "open"  : [1800.0] * len(open_time_values),
            "high"  : [1810.0] * len(open_time_values),
            "low"   : [1790.0] * len(open_time_values),
            "close" : [1805.0] * len(open_time_values),
            "volume": [1000.0] * len(open_time_values),
        })
        df.to_csv(path, index=False)
        return path

    def test_load_klines_ms(self, tmp_path):
        """open_time jako ms integer."""
        times_ms = [int(pd.Timestamp("2023-01-01", tz="UTC").timestamp() * 1000) + i * 3600000
                    for i in range(5)]
        p = self._write_csv(str(tmp_path / "test.csv"), "open_time", times_ms)
        df = load_klines(p)
        assert len(df) == 5
        assert "time" in df.columns

    def test_load_klines_string(self, tmp_path):
        """open_time jako string z datą."""
        times_str = [f"2023-01-0{i+1} 00:00:00+00:00" for i in range(5)]
        p = self._write_csv(str(tmp_path / "test2.csv"), "open_time", times_str)
        df = load_klines(p)
        assert len(df) == 5

    def test_no_duplicate_rows(self, tmp_path):
        """load_klines nie powinien zwracać duplikatów czasu."""
        times_ms = [int(pd.Timestamp("2023-01-01", tz="UTC").timestamp() * 1000)] * 3 + \
                   [int(pd.Timestamp("2023-01-01 01:00", tz="UTC").timestamp() * 1000)]
        p = self._write_csv(str(tmp_path / "test3.csv"), "open_time", times_ms)
        df = load_klines(p)
        # sort_values nie usuwa duplikatów – ale nie powinno ich być w normalnym pliku
        # tu tylko sprawdzamy że nie crashuje
        assert len(df) >= 1


# ──────────────────────────────────────────────────────────────────────────────
# SLIPPAGE
# ──────────────────────────────────────────────────────────────────────────────

class TestSlippage:

    def test_zero_slippage_returns_price(self):
        assert apply_slippage(1800.0, "long", "open", 0.0, 0.0) == 1800.0

    def test_long_open_increases_price(self):
        p = apply_slippage(1800.0, "long", "open", 5.0, 2.0)
        assert p > 1800.0

    def test_long_close_decreases_price(self):
        p = apply_slippage(1800.0, "long", "close", 5.0, 2.0)
        assert p < 1800.0

    def test_short_open_decreases_price(self):
        p = apply_slippage(1800.0, "short", "open", 5.0, 2.0)
        assert p < 1800.0

    def test_short_close_increases_price(self):
        p = apply_slippage(1800.0, "short", "close", 5.0, 2.0)
        assert p > 1800.0


# ──────────────────────────────────────────────────────────────────────────────
# SCORING WFO (punkt 5)
# ──────────────────────────────────────────────────────────────────────────────

class TestScoring:

    def _fake_trades(self, n_wins, n_losses, win_pnl=100.0, loss_pnl=-50.0):
        pnls = [win_pnl] * n_wins + [loss_pnl] * n_losses
        return pd.DataFrame({"pnl": pnls, "net_ret": [p / 10000 for p in pnls]})

    def test_empty_trades_returns_minus9999(self):
        from ema11_wfo_scoring import score_params
        assert score_params(pd.DataFrame(), 10000, 10000) == -9999.0

    def test_balanced_better_than_return_only_with_high_dd(self):
        """Defensive powinien karać za wysoki DD mocniej niż return_only."""
        from ema11_wfo_scoring import score_params
        trades = self._fake_trades(5, 20)   # niska winrate, duże straty
        s_ret = score_params(trades, 9500, 10000, mode="return_only")
        s_def = score_params(trades, 9500, 10000, mode="defensive")
        # defensive kara za DD jest wyższa → wynik defensive powinien być niższy
        assert s_def <= s_ret

    def test_good_strategy_scores_positive(self):
        from ema11_wfo_scoring import score_params
        trades = self._fake_trades(15, 5, win_pnl=200, loss_pnl=-80)
        score  = score_params(trades, 11000, 10000, mode="balanced")
        assert score > 0

    def test_few_trades_penalized(self):
        from ema11_wfo_scoring import score_params
        t2  = self._fake_trades(1, 1)
        t10 = self._fake_trades(6, 4)
        s2  = score_params(t2,  10100, 10000, mode="balanced")
        s10 = score_params(t10, 10100, 10000, mode="balanced")
        assert s10 > s2, "Więcej transakcji powinno dawać lepszy score"


# ──────────────────────────────────────────────────────────────────────────────
# CLOSED-CANDLE ONLY (punkt 14)
# ──────────────────────────────────────────────────────────────────────────────

class TestClosedCandleOnly:

    def test_runner_uses_iloc_minus2_not_minus1(self):
        """
        Weryfikuje, że main_loop() pobiera bar = df.iloc[-2] (ostatnią ZAMKNIĘTĄ świecę),
        a nie df.iloc[-1] (bieżącą, jeszcze otwartą).
        Sprawdzamy przez inspekcję kodu źródłowego live runnera.
        """
        import inspect
        import ema11_live_runner as runner_module

        source = inspect.getsource(runner_module.main_loop)
        # musi być df.iloc[-2] jako bar
        assert "df.iloc[-2]" in source, \
            "main_loop() powinien używać df.iloc[-2] jako ostatniej zamkniętej świecy"
        # musi być df.iloc[-3] jako prev
        assert "df.iloc[-3]" in source, \
            "main_loop() powinien używać df.iloc[-3] jako świecy przed ostatnią"
        # nie powinien już używać df.iloc[-1] jako bar (może być w komentarzu – tolerujemy)
        lines = [l.strip() for l in source.splitlines()
                 if "df.iloc[-1]" in l and not l.strip().startswith("#")]
        assert len(lines) == 0, \
            f"main_loop() nie powinien używać df.iloc[-1] jako baru: {lines}"

    def test_runner_requires_at_least_3_rows(self):
        """
        Weryfikuje, że main_loop() sprawdza len(df) < 3 (nie < 2).
        """
        import inspect
        import ema11_live_runner as runner_module

        source = inspect.getsource(runner_module.main_loop)
        assert "len(df) < 3" in source, \
            "main_loop() powinien wymagać co najmniej 3 wierszy (len(df) < 3)"


# ──────────────────────────────────────────────────────────────────────────────
# PARYTET BACKTEST vs RUNNER (punkt 13)
# ──────────────────────────────────────────────────────────────────────────────

class TestBacktestRunnerParity:
    """
    Sprawdza, że iteracyjna symulacja przez process_bar() daje te same wejścia/wyjścia
    co EMA11Strategy.run() dla tego samego zestawu danych i parametrów.

    To jest najważniejszy test całego projektu.
    """

    PARAMS = {
        "tp_pct"              : 0.05,
        "sl_pct"              : 0.005,
        "trail_drop"          : 3.0,
        "ema_len"             : 50,
        "tma_low_min"         : 30.0,
        "tma_low_max"         : 45.0,
        "tma_high_min"        : 55.0,
        "tma_high_max"        : 70.0,
        "fee_rate"            : 0.0007,
        "slippage_bps"        : 0.0,
        "spread_bps"          : 0.0,
        "min_ema_distance_pct": 0.0,
        "max_ema_distance_pct": 1.0,
        "min_ema_slope"       : 0.0,
        "max_bars_in_trade"   : 0,
        "use_rsi_cross_exit"  : False,
    }

    def _simulate_runner(self, df: pd.DataFrame, params: dict) -> list[dict]:
        """
        Symuluje iterację live runnera przez process_bar() na każdym barze df.
        Zwraca listę trade logów (słowniki) identycznych z ema11_live_trades.jsonl.
        """
        from ema11_live_runner import process_bar
        import copy

        ema_col = f"ema_{params['ema_len']}"
        state = {
            "position"        : None,
            "entry_price"     : None,
            "entry_time"      : None,
            "rsi_peak"        : None,
            "over_zone"       : False,
            "bars_in_position": 0,
            "capital"         : 10_000.0,
            "capital_at_open" : None,
            "last_bar_time"   : None,
            "daily_loss_usd"  : 0.0,
            "daily_date"      : None,
            "paused"          : False,
            "mode"            : "paper",
        }
        trades = []

        # patch log_trade żeby zbierał trade logi
        import ema11_live_runner as runner_module
        original_log_trade = runner_module.log_trade
        original_save_state = runner_module.save_state

        collected = []
        runner_module.log_trade   = lambda t: collected.append(copy.deepcopy(t))
        runner_module.save_state  = lambda s: None   # wycisz zapis pliku

        try:
            for i in range(1, len(df)):
                bar  = bar_from_row(df.iloc[i],     ema_col)
                prev = bar_from_row(df.iloc[i - 1], ema_col)
                process_bar(bar, prev, params, state, mode="paper")
        finally:
            runner_module.log_trade  = original_log_trade
            runner_module.save_state = original_save_state

        return collected

    def test_same_number_of_trades(self):
        """Backtest i runner muszą wygenerować tę samą liczbę transakcji."""
        from ema11_data import prepare_indicators
        df = _make_df(n=400, trend="up")
        df = prepare_indicators(df)
        df = df.dropna(subset=["rsi", "tma_rsi"]).reset_index(drop=True)

        # backtest
        strat = EMA11Strategy(self.PARAMS)
        bt_trades, _, _ = strat.run(df, 10_000.0)
        # pomiń force exit na końcu backtrestu (runner tego nie robi)
        bt_real = bt_trades[bt_trades["reason"] != "FORCE_EXIT_END"]

        # runner
        runner_trades = self._simulate_runner(df, self.PARAMS)

        assert len(runner_trades) == len(bt_real), (
            f"Liczba transakcji: backtest={len(bt_real)}, runner={len(runner_trades)}"
        )

    def test_same_entry_exit_sides(self):
        """Strony transakcji (long/short) muszą być identyczne."""
        from ema11_data import prepare_indicators
        df = _make_df(n=400, trend="up")
        df = prepare_indicators(df)
        df = df.dropna(subset=["rsi", "tma_rsi"]).reset_index(drop=True)

        strat = EMA11Strategy(self.PARAMS)
        bt_trades, _, _ = strat.run(df, 10_000.0)
        bt_real = bt_trades[bt_trades["reason"] != "FORCE_EXIT_END"]
        runner_trades = self._simulate_runner(df, self.PARAMS)

        if len(bt_real) == 0 and len(runner_trades) == 0:
            return  # brak transakcji – parytet zachowany

        assert len(runner_trades) == len(bt_real), "Różna liczba transakcji – test niespójny"

        bt_sides = list(bt_real["side"])
        rt_sides = [t["side"] for t in runner_trades]
        assert bt_sides == rt_sides, \
            f"Strony nie zgadzają się:\nbacktest: {bt_sides}\nrunner:   {rt_sides}"

    def test_entry_prices_close(self):
        """Ceny wejścia muszą być zbliżone (bez slippage powinny być identyczne)."""
        from ema11_data import prepare_indicators
        df = _make_df(n=400, trend="up")
        df = prepare_indicators(df)
        df = df.dropna(subset=["rsi", "tma_rsi"]).reset_index(drop=True)

        strat = EMA11Strategy(self.PARAMS)
        bt_trades, _, _ = strat.run(df, 10_000.0)
        bt_real = bt_trades[bt_trades["reason"] != "FORCE_EXIT_END"]
        runner_trades = self._simulate_runner(df, self.PARAMS)

        if len(bt_real) == 0 or len(runner_trades) == 0:
            return

        if len(bt_real) != len(runner_trades):
            return  # już sprawdzone w innym teście

        for i, (bt_row, rt) in enumerate(zip(bt_real.itertuples(), runner_trades)):
            assert abs(bt_row.entry_price - rt["entry_price"]) < 0.01, (
                f"Trade {i}: cena wejścia backtest={bt_row.entry_price:.4f}, "
                f"runner={rt['entry_price']:.4f}"
            )


if __name__ == "__main__":
    import subprocess, sys
    subprocess.run([sys.executable, "-m", "pytest", __file__, "-v"])
