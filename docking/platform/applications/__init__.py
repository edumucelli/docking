"""Application identity, discovery, launching, and matching.

The ``platform.applications`` package is the single home for everything
related to desktop applications.  Callers import directly from submodules;
this ``__init__`` must remain import-free to avoid the cycle documented in
``docking/platform/__init__.py``.
"""
