"""Market attractor calculations — price, options, and hybrid basins."""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any

import numpy as np
import pandas as pd

from .data_loader import load_option_chain, load_stock_history, option_chain_summary


@dataclass
class AttractorSnapshot:
    """Single attractor basin with core metrics."""

    id: str
    type: str
    position: float
    strength: float
    velocity: float
    stability: float
    source: str


@dataclass
class MarketAttractorState:
    symbol: str
    spot: float
    price_attractor: float
    options_attractor: float | None
    hybrid_attractor: float
    distance_from_attractor: float
    attractor_strength: float
    attractor_velocity: float
    attractor_stability: float
    phase: str
    pressure: float
    coherence: float
    criticality: float
    dissipation: float
    velocity: float
    acceleration: float
    rupture_risk: float
    attractors: list[AttractorSnapshot]
    raw: dict[str, Any]


def _clamp01(value: float) -> float:
    return max(0.0, min(1.0, float(value)))


def price_attractor(stock: pd.DataFrame, window: int = 20) -> tuple[float, float]:
    """Rolling mean close; optional VWAP when volume present."""
    tail = stock.tail(max(window, 2))
    closes = tail["close"].astype(float)
    if tail["volume"].sum() > 0:
        vol = tail["volume"].astype(float).clip(lower=0.0)
        vwap = float((closes * vol).sum() / max(vol.sum(), 1e-9))
        mean_close = float(closes.mean())
        return 0.6 * mean_close + 0.4 * vwap, float(closes.iloc[-1])
    return float(closes.mean()), float(closes.iloc[-1])


def price_attractor_velocity(stock: pd.DataFrame, window: int = 20, lookback: int = 5) -> float:
    """Signed change in rolling price attractor over recent sessions."""
    if len(stock) < window + lookback:
        return 0.0
    positions = []
    for end in range(len(stock) - lookback + 1, len(stock) + 1):
        pos, _ = price_attractor(stock.iloc[:end], window=window)
        positions.append(pos)
    if len(positions) < 2:
        return 0.0
    return float(positions[-1] - positions[0]) / max(lookback, 1)


def options_attractor(
    chain: pd.DataFrame,
    spot: float,
    *,
    spot_band_pct: float = 0.08,
) -> tuple[float | None, float, float]:
    """Strike with highest OI/gamma concentration near spot."""
    summary = option_chain_summary(chain)
    lo = spot * (1.0 - spot_band_pct)
    hi = spot * (1.0 + spot_band_pct)
    nearby = summary[(summary["Strike"] >= lo) & (summary["Strike"] <= hi)].copy()
    if nearby.empty:
        nearby = summary.copy()
    if nearby.empty:
        return None, 0.0, 0.0

    nearby["concentration"] = (
        nearby["call_oi"]
        + nearby["put_oi"]
        + np.abs(nearby["call_gamma"] * nearby["call_oi"])
        + np.abs(nearby["put_gamma"] * nearby["put_oi"])
    )
    total = float(nearby["concentration"].sum())
    if total <= 0:
        return float(nearby.iloc[nearby["Strike"].sub(spot).abs().argmin()]["Strike"]), 0.0, 0.0

    top = nearby.loc[nearby["concentration"].idxmax()]
    top_strike = float(top["Strike"])
    strength = float(top["concentration"] / total)
    # Stability proxy: dominance of top strike vs neighbors.
    sorted_conc = nearby["concentration"].sort_values(ascending=False)
    top_val = float(sorted_conc.iloc[0])
    second = float(sorted_conc.iloc[1]) if len(sorted_conc) > 1 else 0.0
    stability = _clamp01((top_val - second) / max(top_val, 1e-9))
    return top_strike, strength, stability


def hybrid_attractor(
    price_pos: float,
    options_pos: float | None,
    *,
    options_weight: float = 0.45,
) -> float:
    if options_pos is None:
        return price_pos
    w = _clamp01(options_weight)
    return (1.0 - w) * price_pos + w * options_pos


def compute_attractor_strength(
    spot: float,
    attractor_pos: float,
    concentration: float,
    *,
    ref_distance_pct: float = 0.02,
) -> float:
    dist = abs(spot - attractor_pos)
    ref = max(abs(attractor_pos) * ref_distance_pct, 0.5)
    proximity = _clamp01(1.0 - dist / ref)
    return _clamp01(0.55 * concentration + 0.45 * proximity)


def compute_attractor_stability(
    price_history: list[float],
    options_stability: float,
    *,
    tolerance_pct: float = 0.004,
) -> float:
    if len(price_history) < 2:
        return _clamp01(options_stability)
    ref = max(abs(price_history[-1]), 1.0)
    drift = abs(price_history[-1] - price_history[0]) / ref
    persistence = _clamp01(1.0 - drift / max(tolerance_pct * len(price_history), 1e-6))
    return _clamp01(0.5 * persistence + 0.5 * options_stability)


def infer_market_phase(
    *,
    distance: float,
    spot: float,
    strength: float,
    stability: float,
    velocity: float,
    pressure: float,
) -> str:
    """Map basin geometry to patrol/compression/convergence/rupture/reorganization."""
    rel_dist = abs(distance) / max(abs(spot), 1.0)
    if strength < 0.35 and stability < 0.35:
        return "reorganization"
    if rel_dist > 0.015 and velocity * distance > 0 and strength > 0.45:
        return "rupture"
    if rel_dist > 0.008 and distance * velocity < 0:
        return "convergence"
    if strength > 0.55 and stability > 0.55 and rel_dist < 0.006:
        return "compression"
    if pressure < 0.35 and rel_dist < 0.01:
        return "patrol"
    if rel_dist > 0.012:
        return "rupture"
    return "patrol"


def compute_market_metrics(
    *,
    spot: float,
    attractor_pos: float,
    strength: float,
    stability: float,
    attractor_velocity: float,
    price_velocity: float,
    options_strength: float,
    options_pos: float | None,
) -> dict[str, float]:
    distance = spot - attractor_pos
    rel_dist = abs(distance) / max(abs(spot), 1.0)

    # Pressure rises with distance, weak stability, and escape from strong options basin.
    pressure = _clamp01(
        0.45 * _clamp01(rel_dist / 0.025)
        + 0.25 * (1.0 - stability)
        + 0.15 * options_strength * _clamp01(rel_dist / 0.015)
        + 0.15 * _clamp01(abs(price_velocity) / max(abs(spot) * 0.01, 0.05))
    )

    # Coherence high when price, trend, and options attractor agree.
    options_agree = 1.0
    if options_pos is not None:
        options_agree = _clamp01(1.0 - abs(spot - options_pos) / max(abs(spot) * 0.02, 0.5))
    trend_agree = _clamp01(1.0 - abs(spot - attractor_pos) / max(abs(spot) * 0.03, 0.75))
    coherence = _clamp01(0.4 * options_agree + 0.35 * trend_agree + 0.25 * stability)

    velocity = _clamp01(abs(price_velocity) / max(abs(spot) * 0.015, 0.05))
    acceleration = _clamp01(abs(attractor_velocity) / max(abs(attractor_pos) * 0.004, 0.25))
    criticality = _clamp01(
        0.4 * pressure + 0.25 * (1.0 - stability) + 0.2 * velocity + 0.15 * acceleration
    )
    if pressure > 0.55 and stability < 0.4 and rel_dist > 0.01:
        criticality = max(criticality, 0.65)

    dissipation = _clamp01(
        0.45 * (1.0 - pressure) + 0.35 * stability + 0.2 * _clamp01(-distance * price_velocity)
    )
    rupture_risk = _clamp01(
        0.5 * criticality + 0.3 * pressure * (1.0 - stability) + 0.2 * velocity
    )

    return {
        "pressure": pressure,
        "coherence": coherence,
        "criticality": criticality,
        "dissipation": dissipation,
        "velocity": velocity,
        "acceleration": acceleration,
        "rupture_risk": rupture_risk,
        "distance_from_attractor": distance,
    }


class MarketAttractorModel:
    """Compute attractor state from local CSV inputs."""

    def __init__(self, cfg: dict[str, Any] | None = None) -> None:
        cfg = cfg or {}
        self.ticker = str(cfg.get("ticker", "SPY")).upper()
        self.stock_root = cfg.get("stock_root", "F:/inputs/stocks")
        self.options_root = cfg.get("options_root", "F:/inputs/options/log")
        self.price_window = int(cfg.get("price_window", 20))
        self.spot_band_pct = float(cfg.get("spot_band_pct", 0.08))
        self.options_weight = float(cfg.get("options_weight", 0.45))
        self.velocity_lookback = int(cfg.get("velocity_lookback", 5))

    def compute(self) -> MarketAttractorState:
        stock = load_stock_history(
            self.ticker, stock_root=self.stock_root, max_rows=self.price_window + self.velocity_lookback + 5
        )
        price_pos, spot = price_attractor(stock, window=self.price_window)
        price_vel = price_attractor_velocity(
            stock, window=self.price_window, lookback=self.velocity_lookback
        )

        chain = load_option_chain(self.ticker, options_root=self.options_root)
        opt_pos, opt_strength, opt_stability = (None, 0.0, 0.0)
        if chain is not None:
            opt_pos, opt_strength, opt_stability = options_attractor(
                chain, spot, spot_band_pct=self.spot_band_pct
            )

        hybrid = hybrid_attractor(price_pos, opt_pos, options_weight=self.options_weight)
        strength = compute_attractor_strength(spot, hybrid, max(opt_strength, 0.35))
        hist = []
        for end in range(max(2, len(stock) - self.velocity_lookback), len(stock) + 1):
            p, _ = price_attractor(stock.iloc[:end], window=self.price_window)
            hist.append(p)
        stability = compute_attractor_stability(hist, opt_stability)
        att_velocity = price_vel if opt_pos is None else 0.6 * price_vel + 0.4 * (hybrid - price_pos)

        metrics = compute_market_metrics(
            spot=spot,
            attractor_pos=hybrid,
            strength=strength,
            stability=stability,
            attractor_velocity=att_velocity,
            price_velocity=float(stock["close"].diff().iloc[-1] or 0.0),
            options_strength=opt_strength,
            options_pos=opt_pos,
        )
        phase = infer_market_phase(
            distance=metrics["distance_from_attractor"],
            spot=spot,
            strength=strength,
            stability=stability,
            velocity=att_velocity,
            pressure=metrics["pressure"],
        )

        attractors = [
            AttractorSnapshot(
                id=f"{self.ticker.lower()}_price_attractor",
                type="static",
                position=price_pos,
                strength=_clamp01(0.5 + 0.5 * (1.0 - abs(spot - price_pos) / max(abs(spot) * 0.03, 1.0))),
                velocity=price_vel,
                stability=stability,
                source="price",
            ),
        ]
        if opt_pos is not None:
            attractors.append(
                AttractorSnapshot(
                    id=f"{self.ticker.lower()}_options_attractor",
                    type="dynamic",
                    position=opt_pos,
                    strength=opt_strength,
                    velocity=0.0,
                    stability=opt_stability,
                    source="options",
                )
            )
        attractors.append(
            AttractorSnapshot(
                id=f"{self.ticker.lower()}_hybrid_attractor",
                type="dynamic",
                position=hybrid,
                strength=strength,
                velocity=att_velocity,
                stability=stability,
                source="hybrid",
            )
        )

        return MarketAttractorState(
            symbol=self.ticker,
            spot=spot,
            price_attractor=price_pos,
            options_attractor=opt_pos,
            hybrid_attractor=hybrid,
            distance_from_attractor=metrics["distance_from_attractor"],
            attractor_strength=strength,
            attractor_velocity=att_velocity,
            attractor_stability=stability,
            phase=phase,
            pressure=metrics["pressure"],
            coherence=metrics["coherence"],
            criticality=metrics["criticality"],
            dissipation=metrics["dissipation"],
            velocity=metrics["velocity"],
            acceleration=metrics["acceleration"],
            rupture_risk=metrics["rupture_risk"],
            attractors=attractors,
            raw={
                "price_window": self.price_window,
                "options_available": opt_pos is not None,
                "spot_band_pct": self.spot_band_pct,
            },
        )


def compute_attractors(cfg: dict[str, Any] | None = None) -> MarketAttractorState:
    return MarketAttractorModel(cfg).compute()
