"""Basic indicators (no TA-Lib dependency)."""
from __future__ import annotations
import numpy as np
import pandas as pd


def ema(s: pd.Series, n: int) -> pd.Series:
    return s.ewm(span=n, adjust=False).mean()


def rsi(close: pd.Series, n: int = 14) -> pd.Series:
    d = close.diff()
    up = d.clip(lower=0).ewm(alpha=1 / n, adjust=False).mean()
    dn = (-d.clip(upper=0)).ewm(alpha=1 / n, adjust=False).mean()
    rs = up / dn.replace(0, np.nan)
    return (100 - 100 / (1 + rs)).fillna(50)


def true_range(df: pd.DataFrame) -> pd.Series:
    pc = df["close"].shift(1)
    return pd.concat([df["high"] - df["low"], (df["high"] - pc).abs(), (df["low"] - pc).abs()], axis=1).max(axis=1)


def atr(df: pd.DataFrame, n: int = 14) -> pd.Series:
    return true_range(df).ewm(alpha=1 / n, adjust=False).mean()


def rsi_divergence(rsi_s: pd.Series, sh: list, sl: list) -> list[str]:
    """sh/sl: lists of (bar_index, price). Returns ['bullish'] / ['bearish'] / both / []."""
    out = []
    if len(sl) >= 2:
        (i1, p1), (i2, p2) = sl[-2], sl[-1]
        if p2 < p1 and rsi_s.iloc[i2] > rsi_s.iloc[i1]:
            out.append("bullish")
    if len(sh) >= 2:
        (i1, p1), (i2, p2) = sh[-2], sh[-1]
        if p2 > p1 and rsi_s.iloc[i2] < rsi_s.iloc[i1]:
            out.append("bearish")
    return out
