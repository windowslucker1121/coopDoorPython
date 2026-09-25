"""End-to-end fixtures: a real server (mock hardware) + headless Chromium.

The server runs as a subprocess exactly like on the Pi (``src/app.py run``),
with ``COOP_ROOT`` pointing at a temp dir so config/logs are throw-away and a
fast simulated door (``simulator_travel_s``).  Skipped when Playwright or a
Chromium build is not available.
"""

from __future__ import annotations

import json
import os
import socket
import subprocess
import sys
import time
import urllib.request

import pytest

sync_api = pytest.importorskip("playwright.sync_api", reason="playwright not installed")

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
APP = os.path.join(ROOT, "src", "app.py")
CHROMIUM_CANDIDATES = [os.environ.get("COOP_E2E_CHROMIUM", ""), "/opt/pw-browsers/chromium"]

pytestmark = pytest.mark.e2e

BASE_CONFIG = """\
use_mock_hardware: true
simulate_door: true
simulator_travel_s: 2
enable_camera: true
auto_mode: false
timer_mode: false
"""

# Local requests must never go through an HTTP proxy.
_opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))


def http(base: str, path: str, data: dict | None = None, method: str | None = None):
    body = json.dumps(data).encode() if data is not None else None
    req = urllib.request.Request(base + path, data=body, method=method or ("POST" if body else "GET"),
                                 headers={"Content-Type": "application/json"} if body else {})
    try:
        with _opener.open(req, timeout=10) as r:
            return r.status, json.loads(r.read() or b"null")
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read() or b"null")


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


class Server:
    def __init__(self, root: str):
        self.root = root
        self.port = _free_port()
        self.base = f"http://127.0.0.1:{self.port}"
        self.proc: subprocess.Popen | None = None

    def start(self) -> "Server":
        env = dict(os.environ, COOP_ROOT=self.root, NO_PROXY="*", no_proxy="*")
        self.log = open(os.path.join(self.root, "server.out"), "ab")
        self.proc = subprocess.Popen([sys.executable, APP, "run", "--host", "127.0.0.1", "--port", str(self.port)],
                                     cwd=ROOT, env=env, stdout=self.log, stderr=subprocess.STDOUT)
        deadline = time.time() + 30
        while time.time() < deadline:
            if self.proc.poll() is not None:
                raise RuntimeError(f"server exited early - see {self.root}/server.out")
            try:
                if http(self.base, "/version")[0] == 200:
                    return self
            except OSError:
                pass
            time.sleep(0.2)
        raise RuntimeError("server did not start")

    def stop(self) -> None:
        if self.proc and self.proc.poll() is None:
            self.proc.terminate()
            try:
                self.proc.wait(10)
            except subprocess.TimeoutExpired:
                self.proc.kill()
        if getattr(self, "log", None):
            self.log.close()

    def get(self, path):
        return http(self.base, path)[1]

    def post(self, path, data=None):
        return http(self.base, path, data if data is not None else {}, "POST")


@pytest.fixture(scope="module")
def server(tmp_path_factory):
    root = str(tmp_path_factory.mktemp("coop"))
    with open(os.path.join(root, "config.yaml"), "w") as f:
        f.write(BASE_CONFIG)
    srv = Server(root).start()
    yield srv
    srv.stop()


@pytest.fixture(scope="session")
def browser():
    try:
        pw = sync_api.sync_playwright().start()
    except Exception as e:  # pragma: no cover - environment specific
        pytest.skip(f"playwright unavailable: {e}")
    kwargs = {"args": ["--no-proxy-server"]}
    exe = next((p for p in CHROMIUM_CANDIDATES if p and os.path.exists(p)), None)
    if exe:
        kwargs["executable_path"] = exe
    try:
        b = pw.chromium.launch(**kwargs)
    except Exception as e:  # pragma: no cover
        pw.stop()
        pytest.skip(f"chromium unavailable: {e}")
    yield b
    b.close()
    pw.stop()


class UI:
    """A page plus the console errors it produced."""

    def __init__(self, page, base):
        self.page = page
        self.base = base
        self.errors: list[str] = []
        page.on("console", self._console)
        page.on("pageerror", lambda e: self.errors.append(str(e)))

    def _console(self, msg):
        # Rejected form input is answered with HTTP 400 on purpose; the app
        # shows the message, but Chromium also logs the status.
        if msg.type == "error" and "status of 400" not in msg.text:
            self.errors.append(msg.text)

    def goto(self, route: str = "home"):
        self.page.goto(f"{self.base}/#/{route}")
        self.wait_connected()
        return self.page

    def wait_connected(self):
        self.page.wait_for_function("() => window.__coop && window.__coop.S.connected && window.__coop.S.data")

    def toast(self, text: str, timeout: float = 8000):
        loc = self.page.locator(".toast", has_text=text)
        loc.first.wait_for(timeout=timeout)
        return loc.first

    def confirm(self):
        self.page.locator("dialog[open] [data-confirm]").click()

    def data(self, key: str):
        return self.page.evaluate(f"() => window.__coop.S.data[{json.dumps(key)}]")


def _ui(browser, server, **ctx):
    context = browser.new_context(**ctx)
    page = context.new_page()
    page.set_default_timeout(15000)
    return context, UI(page, server.base)


@pytest.fixture
def ui(browser, server):
    context, u = _ui(browser, server, viewport={"width": 1366, "height": 900})
    yield u
    context.close()
    assert u.errors == [], f"console errors: {u.errors}"


@pytest.fixture
def phone(browser, server):
    context, u = _ui(browser, server, viewport={"width": 390, "height": 844}, is_mobile=True, has_touch=True)
    yield u
    context.close()
    assert u.errors == [], f"console errors: {u.errors}"
