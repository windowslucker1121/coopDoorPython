"""Dinky Coop — Raspberry Pi chicken-coop door controller.

Package layout
--------------
``config``      typed, validated, persisted settings (``config.yaml``)
``hardware``    GPIO / sensor / camera backends (real and mock) + door simulator
``door``        door driver (GPIO level), schedules and the control loop
``services``    environment monitoring, notifications, data logging, Wi-Fi,
                system operations, sun calculations
``web``         Flask + Socket.IO interface (HTTP API, events, dashboard payload)
``application`` wires everything together and runs the background workers
"""

__version__ = "2.0.0"
