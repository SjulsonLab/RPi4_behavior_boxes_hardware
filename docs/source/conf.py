"""Sphinx configuration for BehavBox hardware documentation."""

from __future__ import annotations

from pathlib import Path
import sys


PROJECT_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(PROJECT_ROOT))

project = "RPi4_behavior_boxes_hardware"
author = "Sjulson Lab"
extensions = [
    "sphinx.ext.autodoc",
    "sphinx.ext.napoleon",
]
templates_path = ["_templates"]
exclude_patterns: list[str] = []
html_static_path = ["_static"]

try:
    import sphinx_rtd_theme

    html_theme = "sphinx_rtd_theme"
except Exception:
    html_theme = "alabaster"

autodoc_member_order = "bysource"
napoleon_google_docstring = True
napoleon_numpy_docstring = False
