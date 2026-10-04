"""Optional localhost-only static preview; never serves legacy account data."""
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlsplit

ROOT = Path(__file__).resolve().parent
ALLOWED = {'/', '/index.html', '/public/index.html', '/public/style.css',
           '/public/pastel.css', '/public/engine.js', '/public/storage.js', '/public/app.js'}

class Preview(SimpleHTTPRequestHandler):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=str(ROOT), **kwargs)

    def do_GET(self):
        if urlsplit(self.path).path not in ALLOWED:
            self.send_error(404)
            return
        super().do_GET()

    def do_HEAD(self):
        if urlsplit(self.path).path not in ALLOWED:
            self.send_error(404)
            return
        super().do_HEAD()

if __name__ == '__main__':
    print('ORBIT browser edition: http://localhost:8088', flush=True)
    ThreadingHTTPServer(('127.0.0.1', 8088), Preview).serve_forever()
