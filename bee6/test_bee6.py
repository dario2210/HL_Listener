from __future__ import annotations

import numpy as np
import pandas as pd

from bee6_engine import BarData, PositionState, generate_entry_signal, generate_exit_signal
from bee6_strategy import Bee6Strategy
from bee6_dashboard import _pct_value


BASE_PARAMS = {
    "wt_channel_len": 10,
    "wt_avg_len": 21,
    "wt_signal_len": 3,
    "wt_min_signal_level": 0.0,
    "wt_zero_line": 0.0,
    "trade_direction": "long",
    "allow_longs": True,
    "allow_shorts": False,
    "short_trading_enabled": False,
    "wt_long_entry_window_bars": 1,
    "wt_long_entry_max_above_zero": -25.0,
    "wt_long_close_min_level": 40.0,
    "wt_long_exit_min_level": 40.0,
    "wt_long_breakeven_enabled": True,
    "wt_long_breakeven_trigger_pct": 0.01,
    "wt_long_breakeven_offset_pct": 0.001,
    "wt_long_tp1_enabled": False,
    "wt_long_tp1_fraction": 0.0,
    "wt_long_tp1_breakeven_enabled": False,
    "wt_long_tp2_enabled": False,
    "wt_long_tp2_fraction": 0.0,
    "wt_long_emergency_sl_enabled": False,
    "wt_long_emergency_sl_capital_pct": 0.0,
    "wt_disable_partial_exits": True,
    "fee_rate": 0.0,
    "slippage_bps": 0.0,
    "spread_bps": 0.0,
    "atr_stop_enabled": False,
    "max_bars_in_trade": 0,
}


def test_dashboard_percent_input_converts_one_percent_to_fraction():
    assert _pct_value(1) == 0.01
    assert _pct_value(0.5) == 0.005
    assert _pct_value(0.10) == 0.001


def _bar(
    *,
    hour: int = 0,
    open_: float = 100.0,
    high: float = 101.0,
    low: float = 99.0,
    close: float = 100.0,
    wt1: float = -20.0,
    wt2: float = -25.0,
    green: bool = False,
    red: bool = False,
    bars_since_green: float = np.nan,
) -> BarData:
    return BarData(
        time=pd.Timestamp("2024-01-01", tz="UTC") + pd.Timedelta(hours=hour),
        open=open_,
        high=high,
        low=low,
        close=close,
        wt1=wt1,
        wt2=wt2,
        wt_delta=wt1 - wt2,
        wt_green_dot=green,
        wt_red_dot=red,
        bars_since_wt_green_dot=bars_since_green,
    )


def test_enters_long_on_candle_after_green_dot_below_entry_level():
    prev = _bar(hour=1, wt1=-28.0, wt2=-30.0, green=True)
    bar = _bar(hour=2, close=101.0, wt1=-8.0, wt2=-12.0, bars_since_green=1.0)

    sig = generate_entry_signal(bar, prev, BASE_PARAMS, None)

    assert sig.action == "open_long"
    assert sig.reason == "WT_H1_GREEN_DOT_NEXT_CANDLE_LONG"
    assert sig.meta["long_entry_level_h1"] == -25.0


def test_does_not_enter_when_green_dot_setup_is_above_entry_level():
    prev = _bar(hour=1, wt1=-18.0, wt2=-24.0, green=True)
    bar = _bar(hour=2, close=101.0, wt1=-8.0, wt2=-10.0, bars_since_green=1.0)

    sig = generate_entry_signal(bar, prev, BASE_PARAMS, None)

    assert sig.action == "none"


def test_red_dot_exits_long_only_above_exit_level():
    prev = _bar(hour=1, wt1=52.0, wt2=45.0)
    bar = _bar(hour=2, wt1=42.0, wt2=48.0, red=True)
    pos = PositionState(side="long", entry_price=100.0, entry_time=prev.time)

    sig = generate_exit_signal(bar, prev, BASE_PARAMS, pos)

    assert sig.action == "close_force"
    assert sig.reason == "WT_H1_RED_DOT_LEVEL_EXIT_LONG"
    assert sig.meta["long_close_level_h1"] == 40.0


def test_red_dot_below_exit_level_does_not_close_long():
    prev = _bar(hour=1, wt1=36.0, wt2=32.0)
    bar = _bar(hour=2, wt1=31.0, wt2=35.0, red=True)
    pos = PositionState(side="long", entry_price=100.0, entry_time=prev.time)

    sig = generate_exit_signal(bar, prev, BASE_PARAMS, pos)

    assert sig.action == "none"


def test_one_percent_profit_arms_break_even_without_partial_close():
    prev = _bar(hour=1, wt1=-8.0, wt2=-12.0)
    bar = _bar(hour=2, high=101.2, low=100.2, close=100.8, wt1=5.0, wt2=3.0)
    pos = PositionState(side="long", entry_price=100.0, entry_time=prev.time)

    sig = generate_exit_signal(bar, prev, BASE_PARAMS, pos)

    assert sig.action == "none"
    assert pos.tp1_taken is True
    assert np.isclose(pos.stop_price, 100.1)


def test_break_even_stop_closes_after_it_has_been_armed():
    prev = _bar(hour=2, wt1=5.0, wt2=3.0)
    bar = _bar(hour=3, high=100.5, low=99.8, close=100.1, wt1=4.0, wt2=3.5)
    pos = PositionState(
        side="long",
        entry_price=100.0,
        entry_time=prev.time,
        bars_in_position=2,
        stop_price=100.1,
        tp1_taken=True,
        tp1_protection_after_bars=2,
    )

    sig = generate_exit_signal(bar, prev, BASE_PARAMS, pos)

    assert sig.action == "close_force"
    assert sig.reason == "LONG_BREAKEVEN_STOP"
    assert np.isclose(sig.exit_price, 100.1)


def test_strategy_runs_full_long_without_partial_tp_rows():
    times = pd.date_range("2024-01-01", periods=5, freq="1h", tz="UTC")
    df = pd.DataFrame(
        {
            "time": times,
            "open": [100.0, 100.0, 101.0, 102.0, 102.0],
            "high": [100.5, 100.5, 101.2, 102.5, 102.2],
            "low": [99.5, 99.5, 100.8, 101.5, 101.6],
            "close": [100.0, 100.0, 101.0, 102.0, 101.8],
            "wt1": [-30.0, -28.0, -8.0, 55.0, 42.0],
            "wt2": [-32.0, -30.0, -12.0, 45.0, 48.0],
            "wt_green_dot": [False, True, False, False, False],
            "wt_red_dot": [False, False, False, False, True],
            "bars_since_wt_green_dot": [np.nan, 0.0, 1.0, 2.0, 3.0],
        }
    )
    df["wt_delta"] = df["wt1"] - df["wt2"]

    trades, _equity, _final_cap = Bee6Strategy(BASE_PARAMS).run(df, 10_000.0)

    assert len(trades) == 1
    assert trades.iloc[0]["side"] == "long"
    assert trades.iloc[0]["reason"] == "WT_H1_RED_DOT_LEVEL_EXIT_LONG"
    assert trades.iloc[0]["close_fraction"] == 1.0
