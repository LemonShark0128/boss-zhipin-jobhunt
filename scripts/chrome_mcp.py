"""Minimal stdio MCP client for huashu-chrome, plus a thin browser wrapper.

Only dependency: node + npx (the huashu-chrome npm package is fetched by npx).
Config keys (config.json -> "transport"):
  kind        "huashu" (default)
  bridge_port 8899
  label       tab label used to recognise our own tabs
  chrome_app  macOS app name used to wake the extension ("Google Chrome")
"""
import json
import re
import socket
import subprocess
import sys
import threading
import time

CMD = ["npx", "-y", "huashu-chrome", "mcp"]


class Client:
    def __init__(self, label="boss-hunt", timeout=300):
        self.cmd = CMD + ["--client", label]
        self.p = subprocess.Popen(self.cmd, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                  stderr=subprocess.DEVNULL, text=True, bufsize=1)
        self.id = 0
        self.lines = []
        self.timeout = timeout
        threading.Thread(target=self._pump, daemon=True).start()

    def _pump(self):
        for line in self.p.stdout:
            self.lines.append(line)

    def call(self, name, arguments, timeout=180):
        self.id += 1
        mid = self.id
        msg = {"jsonrpc": "2.0", "id": mid, "method": "tools/call",
               "params": {"name": name, "arguments": arguments}}
        try:
            self.p.stdin.write(json.dumps(msg) + "\n")
            self.p.stdin.flush()
        except Exception as e:
            return {"text": "STDIN_ERR:%s" % e, "images": [], "isError": True, "raw": {}}
        t0 = time.time()
        while time.time() - t0 < timeout:
            for i, line in enumerate(list(self.lines)):
                line = line.strip()
                if not line:
                    continue
                try:
                    obj = json.loads(line)
                except Exception:
                    continue
                if obj.get("id") == mid:
                    try:
                        del self.lines[i]
                    except IndexError:
                        pass
                    res = obj.get("result", {}) or {}
                    texts, imgs = [], []
                    for part in res.get("content", []) or []:
                        if part.get("type") == "text":
                            texts.append(part.get("text", ""))
                        elif part.get("type") == "image":
                            imgs.append(part.get("data"))
                    return {"text": "\n".join(texts), "images": imgs,
                            "isError": bool(res.get("isError")), "raw": obj}
            time.sleep(0.08)
        return {"text": "", "images": [], "isError": True, "raw": {"error": "timeout"}}

    def start(self):
        self.id += 1
        init = {"jsonrpc": "2.0", "id": self.id, "method": "initialize",
                "params": {"protocolVersion": "2024-11-05", "capabilities": {},
                           "clientInfo": {"name": "boss-hunt", "version": "1"}}}
        try:
            self.p.stdin.write(json.dumps(init) + "\n")
            self.p.stdin.flush()
            time.sleep(1.2)
            self.p.stdin.write(json.dumps({"jsonrpc": "2.0",
                                           "method": "notifications/initialized",
                                           "params": {}}) + "\n")
            self.p.stdin.flush()
        except Exception:
            pass

    def close(self):
        try:
            self.p.terminate()
        except Exception:
            pass


def port_up(port=8899, timeout=1.0):
    sk = socket.socket()
    sk.settimeout(timeout)
    try:
        sk.connect(("127.0.0.1", int(port)))
        sk.close()
        return True
    except Exception:
        try:
            sk.close()
        except Exception:
            pass
        return False


def ensure_bridge(port=8899, wait_seconds=60):
    """Start the local bridge if it is not listening. The extension reconnects itself."""
    if port_up(port):
        return "bridge_ok"
    try:
        subprocess.Popen(["npx", "-y", "huashu-chrome", "bridge"], stdout=subprocess.DEVNULL,
                         stderr=subprocess.DEVNULL, start_new_session=True)
    except Exception as e:
        return "bridge_spawn_err:%s" % e
    waited = 0.0
    while waited < wait_seconds:
        time.sleep(1.5)
        waited += 1.5
        if port_up(port):
            time.sleep(6)  # give the extension time to reconnect
            return "bridge_restarted"
    return "bridge_fail"


def open_chrome(chrome="Google Chrome"):
    """Launch Chrome without navigating anywhere (never touches the user's own tabs).

    Platform-aware: macOS uses `open -a`; Linux tries the usual binary names;
    Windows uses `start`. Returns the command it attempted, or "" if none worked.
    Failure is non-fatal — the caller just probes the bridge again.
    """
    devnull = subprocess.DEVNULL
    try:
        if sys.platform == "darwin":
            subprocess.run(["open", "-a", chrome], stdout=devnull, stderr=devnull)
            return "open -a " + chrome
        if sys.platform.startswith("linux"):
            for exe in ("google-chrome", "google-chrome-stable", "chromium", "chromium-browser"):
                try:
                    subprocess.Popen([exe], stdout=devnull, stderr=devnull, start_new_session=True)
                    return exe
                except FileNotFoundError:
                    continue
            return ""
        if sys.platform.startswith("win"):
            subprocess.run(["cmd", "/c", "start", "", "chrome"], stdout=devnull, stderr=devnull, shell=False)
            return "start chrome"
    except Exception:
        return ""
    return ""


def wake_extension(cfg, attempts=3):
    """Chrome/extension asleep: open Chrome (blank page, never the user's own URLs)."""
    chrome = (cfg.get("transport") or {}).get("chrome_app") or "Google Chrome"
    label = (cfg.get("transport") or {}).get("label") or "boss-hunt"
    for _ in range(attempts):
        open_chrome(chrome)
        time.sleep(6)
        probe = Client(label=label)
        probe.start()
        r = probe.call("tabs", {"action": "list"}, timeout=40)
        probe.close()
        if "NO_EXTENSION" not in (r.get("text") or ""):
            return "extension_online"
    return "extension_offline"


class Browser:
    """Thin helper around the MCP tool surface used by this skill."""

    def __init__(self, cfg):
        self.cfg = cfg
        self.label = (cfg.get("transport") or {}).get("label") or "boss-hunt"
        self.opened = []

    def connect(self):
        port = (self.cfg.get("transport") or {}).get("bridge_port") or 8899
        self.bridge = ensure_bridge(port)
        self.c = Client(label=self.label)
        self.c.start()
        listing = self.c.call("tabs", {"action": "list"}, timeout=40)
        if "NO_EXTENSION" in (listing.get("text") or ""):
            self.wake = wake_extension(self.cfg)
            self.c.close()
            self.c = Client(label=self.label)
            self.c.start()
        else:
            self.wake = "extension_ok"
        return self.bridge

    # --- primitives -------------------------------------------------------
    def new_tab(self, url):
        r = self.c.call("tabs", {"action": "new", "url": url, "label": self.label})
        m = re.search(r"\[(\d{6,})\]", r.get("text") or "")
        tid = int(m.group(1)) if m else None
        if tid:
            self.opened.append(tid)
        return tid

    def nav(self, tab, url, wait=4.0):
        r = self.c.call("navigate", {"tabId": tab, "url": url}, timeout=120)
        time.sleep(wait)
        return r

    def js(self, tab, expr, timeout=60):
        return self.c.call("eval", {"tabId": tab, "expr": expr}, timeout=timeout)

    def json(self, tab, expr, default=None, timeout=60):
        """eval 并按 huashu 的 <page-content> 包装还原成 Python 对象"""
        r = self.js(tab, expr, timeout=timeout)
        return parse_json_payload(r.get("text") or "", default)

    def click(self, tab, selector, timeout=90):
        return self.c.call("click", {"tabId": tab, "selector": selector}, timeout=timeout)

    def fill(self, tab, selector, text, timeout=90):
        return self.c.call("fill", {"tabId": tab,
                                    "fields": [{"selector": selector, "text": text}]},
                           timeout=timeout)

    def network(self, tab, body, index=0, max_body=200000, timeout=90):
        return self.c.call("network", {"tabId": tab, "body": body, "index": index,
                                       "maxBody": max_body}, timeout=timeout)

    def scroll(self, tab, times=10, wait=800):
        return self.c.call("scroll", {"tabId": tab, "to": "bottom", "times": times,
                                      "wait": wait}, timeout=200)

    def close_own_tabs(self):
        """Only tabs this process opened; never the user's pages."""
        closed = 0
        for tid in sorted(set(self.opened)):
            try:
                self.c.call("tabs", {"action": "close", "tabId": tid}, timeout=30)
                closed += 1
            except Exception:
                pass
        self.opened = []
        return closed

    def close(self):
        try:
            self.c.close()
        except Exception:
            pass


PAGE_RE = re.compile(r"<page-content[^>]*>\s*(.*?)\s*</page-content>", re.S)


def unwrap(text):
    """huashu 的 eval/network 返回会被 <page-content> 包一层，值是带引号的字符串。

    这里剥掉包装并解出内层字符串，失败时原样返回。
    """
    if not text:
        return ""
    t = text
    m = PAGE_RE.search(t)
    if m:
        t = m.group(1)
    t = t.strip()
    if t.startswith('"'):
        try:
            val, _end = json.JSONDecoder().raw_decode(t)
            if isinstance(val, str):
                return val.strip()
        except Exception:
            pass
    return t


def parse_json_payload(text, default=None):
    t = unwrap(text)
    if not t:
        return default
    try:
        return json.loads(t)
    except Exception:
        pass
    # 容忍前后夹带的提示文字：取出现位置最靠前的 JSON 起始符
    cands = []
    for opener, closer in (("{", "}"), ("[", "]")):
        a, b = t.find(opener), t.rfind(closer)
        if a >= 0 and b > a:
            cands.append((a, b))
    for a, b in sorted(cands):
        try:
            return json.loads(t[a:b + 1])
        except Exception:
            continue
    return default


def parse_network_json(text, max_scan=400000):
    """Bridge returns the raw body, possibly wrapped; grab the outermost JSON object."""
    return parse_json_payload(text, None)
