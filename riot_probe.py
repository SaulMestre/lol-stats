"""Comprobación de conectividad de Riot desde Render.

No publica la API key ni permite peticiones arbitrarias desde el exterior.
Compara urllib con curl para identificar errores de cabeceras o de red.
"""
import os
import subprocess
import tempfile
import urllib.error
import urllib.parse
import urllib.request
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

USER_AGENT = "lol-stats-research/0.1 (personal EUW SoloQ statistics)"


def target():
    riot_id = os.environ.get("RIOT_ID", "")
    if "#" not in riot_id:
        return None
    name, tag = riot_id.split("#", 1)
    return (
        "https://europe.api.riotgames.com/riot/account/v1/"
        "accounts/by-riot-id/"
        + urllib.parse.quote(name, safe="")
        + "/"
        + urllib.parse.quote(tag, safe="")
    )


def classify(code, body):
    cf = " (Cloudflare 1010)" if b"1010" in body else ""
    return f"HTTP {code}{cf}"


def urllib_probe(url, token):
    req = urllib.request.Request(url, headers={
        "X-Riot-Token": token,
        "User-Agent": USER_AGENT,
        "Accept": "application/json",
    })
    try:
        with urllib.request.urlopen(req, timeout=15) as res:
            return classify(res.status, res.read(1024))
    except urllib.error.HTTPError as exc:
        return classify(exc.code, exc.read(2048))
    except (urllib.error.URLError, TimeoutError) as exc:
        return f"connection error ({type(exc).__name__})"


def curl_probe(url, token):
    # curl configuration supplied via stdin so the token is not a command argument.
    config = (
        f'header = "X-Riot-Token: {token}"\n'
        'header = "Accept: application/json"\n'
        f'user-agent = "{USER_AGENT}"\n'
    )
    try:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "response"
            res = subprocess.run([
                "curl", "-q", "--silent", "--show-error", "--location",
                "--max-time", "20", "--output", str(path),
                "--write-out", "%{http_code}", "--config", "-", url,
            ], input=config, capture_output=True, text=True, timeout=25)
            code = res.stdout.strip()
            body = path.read_bytes()[:2048] if path.exists() else b""
            if code.isdigit():
                return classify(code, body)
            return f"curl exit {res.returncode} (no HTTP response)"
    except FileNotFoundError:
        return "curl unavailable"
    except subprocess.TimeoutExpired:
        return "curl timeout"


def probe():
    token = os.environ.get("RIOT_API_KEY")
    url = target()
    if not token or not url:
        return "missing RIOT_API_KEY or RIOT_ID"
    return "urllib=" + urllib_probe(url, token) + "; curl=" + curl_probe(url, token)


class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path not in ("/", "/health"):
            self.send_error(404)
            return
        self.send_response(200)
        self.send_header("Content-Type", "text/plain; charset=utf-8")
        self.end_headers()
        self.wfile.write(b"OK\n")

    def do_HEAD(self):
        if self.path not in ("/", "/health"):
            self.send_error(404)
            return
        self.send_response(200)
        self.end_headers()


if __name__ == "__main__":
    print("Riot connection probe: " + probe(), flush=True)
    HTTPServer(("0.0.0.0", int(os.environ.get("PORT", "10000"))), Handler).serve_forever()
