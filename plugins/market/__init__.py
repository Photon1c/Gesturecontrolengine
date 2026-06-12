"""Market attractor plugin — SPY-style dynamic roost from local CSV data."""

from .market_packet import build_market_packet
from .attractors import MarketAttractorModel, compute_attractors

__all__ = ["MarketAttractorModel", "build_market_packet", "compute_attractors"]
