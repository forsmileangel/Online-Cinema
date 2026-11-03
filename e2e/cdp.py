"""Tiny CDP helper: headless Edge with a throwaway profile, every non-GET request to the
cinema blocked, so tests never write history, favourites, offline jobs or casts."""
import asyncio
import json
import os
import shutil
import subprocess
import tempfile
import time
import urllib.request

EDGE = os.environ.get("CINEMA_EDGE") or r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe"


def free_port():
    import socket
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


class Browser:
    def __init__(self, base, port=None, width=1600, height=900):
        self.base, self.port, self.width, self.height = base, port or free_port(), width, height
        self.blocked: list[str] = []
        self.events: list[dict] = []
        self.listeners = []
        self.proc = None
        self.profile = None

    async def __aenter__(self):
        import websockets  # Only the headless checks need it (requirements-dev.txt).

        if not os.path.isfile(EDGE):
            raise RuntimeError(f"Edge not found at {EDGE}; set CINEMA_EDGE")
        self.profile = tempfile.mkdtemp(prefix="cdp-prof-")
        self.proc = subprocess.Popen([EDGE, "--headless=new", f"--remote-debugging-port={self.port}",
                                      f"--user-data-dir={self.profile}", "--autoplay-policy=no-user-gesture-required",
                                      "--mute-audio", "--no-first-run", f"--window-size={self.width},{self.height}",
                                      # Headless pages turn "hidden" after a while, and hidden pages pause muted video.
                                      "--disable-backgrounding-occluded-windows", "--disable-renderer-backgrounding",
                                      "--disable-background-timer-throttling", "--disable-background-media-suspend",
                                      "--disable-features=CalculateNativeWinOcclusion",
                                      "about:blank"],
                                     # Edge must not hold the test's output pipe open after the test ends.
                                     stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        page = None
        for _ in range(100):
            try:
                targets = json.load(urllib.request.urlopen(f"http://127.0.0.1:{self.port}/json/list"))
                page = next(t for t in targets if t["type"] == "page")
                break
            except Exception:
                await asyncio.sleep(0.2)
        if page is None:
            await self.__aexit__(None, None, None)
            raise RuntimeError("Edge did not open its debugging port within 20 s")
        self.ws = await websockets.connect(page["webSocketDebuggerUrl"], max_size=None)
        self.seq, self.pending = 0, {}
        self.reader = asyncio.create_task(self._read())
        host = self.base.split("//", 1)[1]
        await self.send("Fetch.enable", {"patterns": [{"urlPattern": f"*{host}/*", "requestStage": "Request"}]})
        await self.send("Page.enable")
        await self.send("Network.enable")
        await self.send("Emulation.setDeviceMetricsOverride",
                        {"width": self.width, "height": self.height, "deviceScaleFactor": 1, "mobile": False})
        await self.send("Emulation.setFocusEmulationEnabled", {"enabled": True})
        return self

    async def __aexit__(self, *exc):
        reader = getattr(self, "reader", None)
        if reader:
            reader.cancel()
        try:
            await self.ws.close()
        except Exception:
            pass
        # Kill the whole Edge process tree started for this test (never the user's own Edge).
        if self.proc:
            subprocess.run(["taskkill", "/PID", str(self.proc.pid), "/T", "/F"], capture_output=True)
            time.sleep(1.0)
        if self.profile:
            shutil.rmtree(self.profile, ignore_errors=True)

    async def send(self, method, params=None, timeout=20.0):
        self.seq += 1
        fut = asyncio.get_running_loop().create_future()
        self.pending[self.seq] = fut
        await self.ws.send(json.dumps({"id": self.seq, "method": method, "params": params or {}}))
        try:
            return await asyncio.wait_for(fut, timeout)
        except asyncio.TimeoutError:
            raise TimeoutError(f"CDP {method} did not answer in {timeout}s") from None

    async def _read(self):
        async for raw in self.ws:
            msg = json.loads(raw)
            if "id" in msg and msg["id"] in self.pending:
                self.pending.pop(msg["id"]).set_result(msg.get("result", msg.get("error")))
                continue
            method, p = msg.get("method"), msg.get("params", {})
            if method == "Fetch.requestPaused":
                req = p["request"]
                handled = False
                for listener in self.listeners:
                    if await listener(self, p):
                        handled = True
                        break
                if handled:
                    continue
                if req["method"] not in ("GET", "HEAD"):
                    self.blocked.append(f"{req['method']} {req['url'].split(self.base, 1)[-1][:60]}")
                    asyncio.create_task(self.send("Fetch.failRequest", {"requestId": p["requestId"], "errorReason": "BlockedByClient"}))
                else:
                    asyncio.create_task(self.send("Fetch.continueRequest", {"requestId": p["requestId"]}))
            elif method:
                self.events.append(msg)

    async def ev(self, expr):
        r = await self.send("Runtime.evaluate", {"expression": expr, "returnByValue": True, "awaitPromise": True})
        if "exceptionDetails" in r:
            raise RuntimeError(r["exceptionDetails"].get("exception", {}).get("description", r["exceptionDetails"]))
        return r.get("result", {}).get("value")

    async def goto(self, path, settle=1.5):
        await self.send("Page.navigate", {"url": self.base + path})
        await asyncio.sleep(settle)

    async def wait_for(self, expr, timeout=20.0, step=0.2):
        end = time.monotonic() + timeout
        while time.monotonic() < end:
            value = await self.ev(expr)
            if value:
                return value
            await asyncio.sleep(step)
        raise TimeoutError(expr)

    async def key(self, name, wait=0.25):
        codes = {"ArrowDown": 40, "ArrowUp": 38, "ArrowLeft": 37, "ArrowRight": 39, "Backspace": 8, "Enter": 13, "Escape": 27}
        for kind in ("keyDown", "keyUp"):
            await self.send("Input.dispatchKeyEvent", {"type": kind, "key": name, "code": name, "windowsVirtualKeyCode": codes[name]})
        await asyncio.sleep(wait)

    async def mouse(self, kind, x, y, button="left", clicks=1):
        params = {"type": kind, "x": x, "y": y}
        if kind != "mouseMoved":
            params.update(button=button, clickCount=clicks)
        else:
            params.update(button="none")
        await self.send("Input.dispatchMouseEvent", params)

    async def click(self, x, y):
        await self.mouse("mouseMoved", x, y)
        await self.mouse("mousePressed", x, y)
        await self.mouse("mouseReleased", x, y)

    async def touch(self, kind, x, y):
        points = [] if kind == "touchEnd" else [{"x": x, "y": y, "id": 1}]
        await self.send("Input.dispatchTouchEvent", {"type": kind, "touchPoints": points})
