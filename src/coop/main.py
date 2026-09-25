"""Server start-up."""

from __future__ import annotations


def run_server(host: str = "0.0.0.0", port: int = 5000) -> None:
    from gevent import monkey
    if not monkey.is_module_patched("threading"):
        monkey.patch_all()

    from .application import Application
    from .logging_setup import LogBuffer, configure_logging
    from .paths import Paths

    # Configure logging *before* building the application so start-up
    # warnings (config fallbacks, missing hardware drivers) are recorded.
    import os
    # COOP_ROOT relocates config/log/secrets (used by the end-to-end tests).
    root = os.environ.get("COOP_ROOT")
    paths = Paths(root, src_dir=Paths.default().src) if root else Paths.default()
    buffer = LogBuffer()
    configure_logging(paths.log_dir, "INFO", buffer)
    Application(paths, log_buffer=buffer).run(host=host, port=port)
