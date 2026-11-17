import os
import urllib.error
import urllib.parse
import urllib.request
from http.server import BaseHTTPRequestHandler, HTTPServer


def probe():
    token = os.environ.get("RIOT_API_KEY")
    riot_id = os.environ.get("RIOT_ID", "")
    if not token or "#" not in riot_id:
        return "missing configuration"
    name, tag = map(urllib.parse.quote, riot_id.split("#", 1))
    url = f"https://europe.api.riotgames.com/riot/account/v1/accounts/by-riot-id/{name}/{tag}"
    request = urllib.request.Request(url, headers={"X-Riot-Token": token})
    try:
        with urllib.request.urlopen(request, timeout=15) as response:
            return f"Riot HTTP {response.status}"
    except urllib.error.HTTPError as error:
        body = error.read(2048)
        cloudflare = " (Cloudflare 1010)" if b"1010" in body else ""
        return f"Riot HTTP {error.code}{cloudflare}"
    except (urllib.error.URLError, TimeoutError):
        return "Riot connection failed"


class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200 if self.path == "/" else 404)
        self.end_headers()
        if self.path == "/":
            self.wfile.write(b"OK\n")


if __name__ == "__main__":
    print(probe(), flush=True)
    HTTPServer(("0.0.0.0", int(os.environ.get("PORT", "10000"))), Handler).serve_forever()
