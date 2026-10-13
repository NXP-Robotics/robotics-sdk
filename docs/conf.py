# Configuration file for the Sphinx documentation builder.
# Build with:  sphinx-build -b html docs docs/_build/html

project = "NXP Robotic Platforms SDK"
copyright = "2026, NXP"
author = "NXP"
release = "2026.07.0"

extensions = [
    "sphinx.ext.todo",
    "sphinx.ext.githubpages",
]

templates_path = ["_templates"]
exclude_patterns = ["_build", "Thumbs.db", ".DS_Store"]

# Use the Read the Docs theme if available, else fall back to alabaster.
try:
    import sphinx_rtd_theme  # noqa: F401
    html_theme = "sphinx_rtd_theme"
except ImportError:
    html_theme = "alabaster"

html_static_path = ["_static"]
todo_include_todos = True
