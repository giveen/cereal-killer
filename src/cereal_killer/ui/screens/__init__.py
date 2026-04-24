"""Re-exports for backwards compatibility."""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

# Load the screens.py file (the monolith with constants only) directly using importlib
# This is necessary because the screens/ package shadows the screens.py file.
_screens_file = Path(__file__).parent.parent / "screens.py"
_screens_spec = importlib.util.spec_from_file_location(
    "cereal_killer.ui.screens_file",
    _screens_file,
)
_screens_module = importlib.util.module_from_spec(_screens_spec)  # type: ignore[union-attr]
_screens_spec.loader.exec_module(_screens_module)  # type: ignore[union-attr]
sys.modules["cereal_killer.ui.screens_file"] = _screens_module

# Export the constants
CODE_BLOCK_RE = _screens_module.CODE_BLOCK_RE
PROBABLE_COMMAND_RE = _screens_module.PROBABLE_COMMAND_RE

# Import from extracted modules
from .dashboard import MainDashboard
from .modals import SolutionModal, InfrastructureCriticalModal
from .ingest import IngestSelection, FilteredIngestTree, IngestModal
from .settings import SettingsScreen

__all__ = [
    "MainDashboard",
    "SolutionModal",
    "InfrastructureCriticalModal",
    "IngestSelection",
    "FilteredIngestTree",
    "IngestModal",
    "SettingsScreen",
    "CODE_BLOCK_RE",
    "PROBABLE_COMMAND_RE",
]
