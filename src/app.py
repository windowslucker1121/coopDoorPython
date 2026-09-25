"""Dinky Coop entry point (``python src/app.py``).

gevent must patch the standard library before anything else is imported,
so this file stays tiny: patch, then hand over to the ``coop`` package.
"""

from gevent import monkey

monkey.patch_all()

import os  # noqa: E402
import sys  # noqa: E402

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from coop.__main__ import main  # noqa: E402

if __name__ == "__main__":
    sys.exit(main(sys.argv[1:] or ["run"]))
