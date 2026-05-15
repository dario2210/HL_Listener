# BEE6

`BEE6` bazuje na projekcie `BEE4_4`, ale pracuje jako osobna strategia WaveTrend long-only.

## Logika

- Timeframe: tylko `1h`.
- Kierunek: tylko `long`.
- Wejście: zielona kropka WT1/WT2 na H1, a pozycja otwierana jest na następnej świecy.
- Strefa wejścia: oba WT (`wt1` i `wt2`) na świecy sygnałowej muszą być na poziomie entry lub niżej.
- BE: po ruchu +1% stop jest uzbrajany na cenie wejścia.
- Wyjście: czerwona kropka WT1/WT2 na H1, gdy oba WT są na poziomie exit lub wyżej.

## Siatka WFO

- `wt_long_entry_max_above_zero`: `-10, -15, -20, -25`
- `wt_long_close_min_level`: `30, 40, 45, 50`
- `wt_channel_len`: `10`
- `wt_avg_len`: `21`
- `wt_signal_len`: `3`
- `wt_long_breakeven_trigger_pct`: `0.01`

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
