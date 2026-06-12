"""Load local stock and option chain CSVs — no external APIs."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Any

import pandas as pd


def _close_column(df: pd.DataFrame) -> str:
    for name in ("Close", "Last", "close", "last", "Adj Close"):
        if name in df.columns:
            return name
    raise ValueError(f"No close column in stock CSV; columns={list(df.columns)}")


def load_stock_history(
    ticker: str,
    *,
    stock_root: str | Path = "F:/inputs/stocks",
    max_rows: int | None = None,
) -> pd.DataFrame:
    """Load `{stock_root}/{TICKER}.csv` sorted by date ascending."""
    path = Path(stock_root) / f"{ticker.upper()}.csv"
    if not path.is_file():
        raise FileNotFoundError(f"Stock CSV not found: {path}")

    df = pd.read_csv(path)
    if max_rows:
        df = df.tail(max_rows)

    date_col = "Date" if "Date" in df.columns else df.columns[0]
    df[date_col] = pd.to_datetime(df[date_col], errors="coerce")
    df = df.dropna(subset=[date_col]).sort_values(date_col)
    close_col = _close_column(df)
    df = df.rename(columns={close_col: "close"})
    if "Volume" in df.columns:
        df["volume"] = pd.to_numeric(df["Volume"], errors="coerce").fillna(0.0)
    else:
        df["volume"] = 0.0
    df["close"] = pd.to_numeric(df["close"], errors="coerce")
    df = df.dropna(subset=["close"])
    return df.reset_index(drop=True)


def _latest_option_chain_dir(options_root: Path, ticker_lower: str) -> Path | None:
    ticker_dir = options_root / ticker_lower
    if not ticker_dir.is_dir():
        return None
    dated_dirs = [p for p in ticker_dir.iterdir() if p.is_dir()]
    if not dated_dirs:
        return None
    # Folder names like mm_dd_yyyy — sort by parsed date when possible.
    def _sort_key(p: Path) -> tuple:
        try:
            return (datetime.strptime(p.name, "%m_%d_%Y"), p.name)
        except ValueError:
            return (datetime.min, p.name)

    return sorted(dated_dirs, key=_sort_key)[-1]


def load_option_chain(
    ticker: str,
    *,
    options_root: str | Path = "F:/inputs/options/log",
    chain_date: str | None = None,
) -> pd.DataFrame | None:
    """Load latest (or dated) option chain CSV; returns None if unavailable."""
    ticker_lower = ticker.lower()
    root = Path(options_root)
    if chain_date:
        chain_dir = root / ticker_lower / chain_date
    else:
        chain_dir = _latest_option_chain_dir(root, ticker_lower)
    if chain_dir is None or not chain_dir.is_dir():
        return None

    csv_path = chain_dir / f"{ticker_lower}_quotedata.csv"
    if not csv_path.is_file():
        candidates = list(chain_dir.glob("*quotedata*.csv"))
        if not candidates:
            return None
        csv_path = candidates[0]

    df = pd.read_csv(csv_path, skiprows=3)
    if "Strike" not in df.columns:
        return None
    df["Strike"] = pd.to_numeric(df["Strike"], errors="coerce")
    df = df.dropna(subset=["Strike"])
    return df.reset_index(drop=True)


def option_chain_summary(chain: pd.DataFrame) -> dict[str, Any]:
    """Extract numeric OI/gamma columns with flexible naming."""
    def _col(*names: str) -> str | None:
        for n in names:
            if n in chain.columns:
                return n
        return None

    call_oi = _col("Open Interest", "OpenInterest")
    put_oi = _col("Open Interest.1", "Open Interest.1")
    call_gamma = _col("Gamma", "Call Gamma")
    put_gamma = _col("Gamma.1", "Put Gamma")

    out = chain[["Strike"]].copy()
    out["call_oi"] = pd.to_numeric(chain[call_oi], errors="coerce").fillna(0.0) if call_oi else 0.0
    out["put_oi"] = pd.to_numeric(chain[put_oi], errors="coerce").fillna(0.0) if put_oi else 0.0
    out["call_gamma"] = (
        pd.to_numeric(chain[call_gamma], errors="coerce").fillna(0.0) if call_gamma else 0.0
    )
    out["put_gamma"] = (
        pd.to_numeric(chain[put_gamma], errors="coerce").fillna(0.0) if put_gamma else 0.0
    )
    return out
