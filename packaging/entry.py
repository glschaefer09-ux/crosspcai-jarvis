#!/usr/bin/env python3
"""
entry.py — the frozen build's entry point.

PyInstaller compiles its entry script as a top-level module, so freezing
jarvas/__main__.py directly breaks every `from . import ...` inside it. This
shim imports the package properly instead, which keeps `python -m jarvas` and
the shipped binary running the exact same code path.
"""

import sys

if __name__ == "__main__":
    # Windows: the supervisor spawns children with this same executable, and a
    # frozen app needs freeze_support before any of that happens.
    import multiprocessing

    multiprocessing.freeze_support()

    from jarvas.__main__ import main

    sys.exit(main())
