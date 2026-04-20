"""
ema11_data.py
=============
Ładowanie świec OHLCV i obliczanie wskaźników.
Logika oddzielona od strategii – wymień tylko ten plik gdy zmienisz źródło danych.
"""

import numpy as np
import pandas as pd

from ema11_params import RSI_LEN, TMA_LEN, EMA_LEN_GRID


# ──────────────────────────────────────────────────────────────────────────────
# WSKAŹNIKI
# ──────────────────────────────────────────────────────────────────────────────

def compute_rsi(series: pd.Series, length: int) -> pd.Series:
    """Klasyczny RSI Wildera (EWM alpha=1/length)."""
    delta = series.diff()
    gain  = pd.Series(np.where(delta > 0,  delta, 0.0), index=series.index)
    loss  = pd.Series(np.where(delta < 0, -delta, 0.0), index=series.index)

    avg_gain = gain.ewm(alpha=1.0 / length, adjust=False).mean()
    avg_loss = loss.ewm(alpha=1.0 / length, adjust=False).mean()

    rs  = avg_gain / avg_loss
    rsi = 100.0 - (100.0 / (1.0 + rs))
    return rsi.fillna(50.0)


def compute_tma(series: pd.Series, length: int) -> pd.Series:
    """TMA = SMA( SMA(series, n), n )."""
    return series.rolling(length).mean().rolling(length).mean()


def prepare_indicators(df: pd.DataFrame) -> pd.DataFrame:
    """
    Dodaje do ramki:
      - rsi, tma_rsi
      - ema_N  dla każdego N z EMA_LEN_GRID
      - ema_N_slope  (pct_change(3) EMA – nachylenie)
    """
    df = df.copy()
    df["rsi"]     = compute_rsi(df["close"], RSI_LEN)
    df["tma_rsi"] = compute_tma(df["rsi"],   TMA_LEN)

    for ema_len in EMA_LEN_GRID:
        ema_col = f"ema_{ema_len}"
        df[ema_col] = df["close"].ewm(span=ema_len, adjust=False).mean()
        df[f"{ema_col}_slope"] = df[ema_col].pct_change(3)

    return df


# ──────────────────────────────────────────────────────────────────────────────
# ŁADOWANIE CSV
# ──────────────────────────────────────────────────────────────────────────────

def load_klines(csv_path: str) -> pd.DataFrame:
    """
    Wczytuje plik CSV ze świecami 1H.
    Obsługuje open_time jako: ms, sekundy lub string z datą.
    Zwraca posortowany DataFrame z kolumną 'time' (tz=UTC).
    """
    df = pd.read_csv(csv_path)

    # normalizacja nazw kolumn
    df.rename(columns={c: c.lower() for c in df.columns}, inplace=True)

    # kolumna czasu
    if "open_time" in df.columns:
        col = df["open_time"]
        try:
            is_numeric = np.issubdtype(col.dtype, np.number)
        except TypeError:
            is_numeric = False
        if is_numeric:
            unit = "ms" if col.max() > 1e12 else "s"
            df["time"] = pd.to_datetime(col, unit=unit, utc=True)
        else:
            df["time"] = pd.to_datetime(col, utc=True, errors="coerce")
    elif "time" in df.columns:
        df["time"] = pd.to_datetime(df["time"], utc=True, errors="coerce")
    else:
        raise ValueError("Brak kolumny open_time / time w CSV.")

    # OHLCV → float
    for col in ["open", "high", "low", "close", "volume"]:
        if col not in df.columns:
            raise ValueError(f"Brak kolumny '{col}' w CSV.")
        df[col] = pd.to_numeric(df[col], errors="coerce")

    df = df.dropna(subset=["time"])
    df = df.sort_values("time").reset_index(drop=True)
    return df


def format_ts(ts) -> str:
    """Ładny string z timestamp (UTC)."""
    if pd.isna(ts):
        return "NaT"
    return pd.Timestamp(ts).tz_convert("UTC").strftime("%Y-%m-%d %H:%M")
