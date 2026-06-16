"""Data ingestion dari Invezgo API."""

from markup_radar.ingest.client import InvezgoClient, InvezgoError

__all__ = ["InvezgoClient", "InvezgoError"]
