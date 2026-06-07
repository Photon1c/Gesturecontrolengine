"""TRER visualization layer — adapter-based real-time metrics dashboard."""

from .adapters import adapt_packet, demo_packet
from .schema import VizPacket, VIZ_SCHEMA_VERSION
from .state_client import fetch_plugin_packet, load_config

__all__ = [
    "VIZ_SCHEMA_VERSION",
    "VizPacket",
    "adapt_packet",
    "demo_packet",
    "fetch_plugin_packet",
    "load_config",
]
