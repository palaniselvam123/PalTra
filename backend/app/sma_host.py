"""Serve the SMA terminal on the same host as the desk.

The page was calling http://127.0.0.1:8001, which is the visitor's own
computer. These helpers mount that API at /sma and point the exported
JavaScript at this site.
"""
from __future__ import annotations

import importlib
from pathlib import Path


def load_terminal():
    """Load the terminal only when it was copied in as its own module.

    Falling back to ``main`` would mount this package's SMA app onto the desk
    during local imports, because that file is also named ``main``.
    """
    try:
        return importlib.import_module("sma_terminal_main")
    except ImportError:
        return None


def mount_terminal(parent) -> bool:
    mod = load_terminal()
    if mod is None or not hasattr(mod, "app"):
        return False
    parent.mount("/sma", mod.app)
    return True


def boot_terminal():
    mod = load_terminal()
    if mod is None or not hasattr(mod, "boot_engine"):
        return None
    return mod.boot_engine()


def stop_terminal() -> None:
    mod = load_terminal()
    if mod is not None and hasattr(mod, "stop_engine"):
        mod.stop_engine()


def rewrite_terminal_bundle(static_root: str = "/app/static") -> int:
    """Replace the baked localhost SMA host in the exported page."""
    root = Path(static_root)
    if not root.is_dir():
        return 0
    needle = "http://127.0.0.1:8001"
    replacement = "https://paltra.fly.dev/sma"
    changed = 0
    for path in root.rglob("*.js"):
        try:
            text = path.read_text(errors="ignore")
        except OSError:
            continue
        if needle not in text:
            continue
        path.write_text(text.replace(needle, replacement))
        changed += 1
    return changed
