"""GPU-hosted ASR service for the Sentinel voice pipeline."""

from .app import create_app
from .config import Settings

__all__ = ["Settings", "create_app"]
