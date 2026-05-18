# BEE6

`BEE6` bazuje na projekcie `BEE4_4`, ale pracuje jako osobna strategia WaveTrend long-only.

## Logika

- Timeframe: tylko `1h`.
- Kierunek: tylko `long`.
- Wejscie: zielona kropka WT1/WT2 na H1, a pozycja otwierana jest na nastepnej swiecy.
- Strefa wejscia: oba WT (`wt1` i `wt2`) na swiecy sygnalowej musza byc na poziomie entry lub nizej.
- BE: po ruchu zgodnym z `wt_long_breakeven_trigger_pct` stop jest uzbrajany na `entry + wt_long_breakeven_offset_pct`.
- Wyjscie: czerwona kropka WT1/WT2 na H1, gdy oba WT sa na poziomie exit lub wyzej.

## Siatka WFO

- `wt_long_entry_max_above_zero`: `-25, -30, -35`
- `wt_long_close_min_level`: `30, 40, 50`
- `wt_long_breakeven_trigger_pct`: `0.01, 0.015, 0.02`
- `wt_long_breakeven_offset_pct`: `0.001, 0.0015`
- `wt_channel_len`: `10`
- `wt_avg_len`: `21`
- `wt_signal_len`: `3`

## Pliki

- [bee6_dashboard.py](bee6_dashboard.py)
- [bee6_engine.py](bee6_engine.py)
- [bee6_strategy.py](bee6_strategy.py)
- [bee6_wfo.py](bee6_wfo.py)
- [bee6_live_runner.py](bee6_live_runner.py)

## Uruchomienie

```bash
python bee6_dashboard.py --host 0.0.0.0 --port 8071
```

```bash
python bee6_main.py --mode backtest
```

```bash
python bee6_main.py --mode wfo
```
