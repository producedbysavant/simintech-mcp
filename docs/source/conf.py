# Configuration file for the Sphinx documentation builder.
#
# simintech-api — документация.

import os
import sys

sys.path.insert(0, os.path.abspath("../.."))

project = "simintech-api"
copyright = "2026, EVS360"
author = "EVS360"
release = "0.1.0"

extensions = [
    "sphinx.ext.autodoc",
    "sphinx.ext.napoleon",
    "sphinx.ext.viewcode",
    "sphinx.ext.intersphinx",
]

templates_path = ["_templates"]
exclude_patterns = []

html_theme = "alabaster"
html_static_path = ["_static"]

# autodoc
autodoc_default_options = {
    "members": True,
    "undoc-members": False,
    "show-inheritance": True,
}

# Игнорировать COM-зависимые модули при сборке вне Windows
autodoc_mock_imports = ["comtypes"]

# Без интерактивного выполнения
napoleon_google_docstring = True
napoleon_numpy_docstring = False
