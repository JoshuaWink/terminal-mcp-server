#!/usr/bin/env python3
"""Wrapper module that imports the project script so setuptools entry point can call main().

This file is intentionally a thin shim that reuses the existing script under scripts/terminal_feed.py
so the repository retains a single source of truth for that logic.
"""

from importlib import util
import os
import sys

SCRIPT_PATH = os.path.join(os.path.dirname(__file__), '..', 'scripts', 'terminal_feed.py')
SCRIPT_PATH = os.path.abspath(SCRIPT_PATH)

# execute the script as a module by running its code in a fresh globals dict
def main(argv=None):
    if argv is not None:
        sys.argv = [sys.argv[0]] + list(argv)
    spec = util.spec_from_file_location('terminal_feed_script', SCRIPT_PATH)
    mod = util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    # If the script defines a main(), call it to follow the same behavior
    if hasattr(mod, 'main'):
        return mod.main()

if __name__ == '__main__':
    main()
