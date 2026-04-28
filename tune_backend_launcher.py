"""PyInstaller-friendly entrypoint for the Tune backend daemon.

PyInstaller can't run a package's `__main__.py` directly (relative imports
break — there's no parent package context at runtime). This launcher imports
the package as a normal module and hands off, so all the relative imports
inside `backend/` keep working.
"""

from __future__ import annotations

import sys

from backend.__main__ import main


if __name__ == "__main__":
    sys.exit(main())
