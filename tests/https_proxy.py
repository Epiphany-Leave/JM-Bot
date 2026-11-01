"""Local TLS termination with preserved Host, matching a NAS HTTPS application gateway."""
import asyncio
from contextlib import asynccontextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
import shutil
import ssl
import subprocess
import threading
from urllib.error import HTTPError
from urllib.request import HTTPRedirectHandler, ProxyHandler, Request, build_opener


@asynccontextmanager
async def https_proxy(backend: str, directory: Path):
    openssl = shutil.which("openssl")
    if not openssl:
        raise RuntimeError("The HTTPS browser check requires OpenSSL on the development machine")
    key, certificate = directory / "proxy-key.pem", directory / "proxy-cert.pem"
    subprocess.run([openssl, "req", "-x509", "-newkey", "rsa:2048", "-nodes", "-days", "1",
                    "-keyout", str(key), "-out", str(certificate), "-subj", "/CN=localhost"],
                   check=True, capture_output=True)
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    context.load_cert_chain(certificate, key)
    class NoRedirect(HTTPRedirectHandler):
        def redirect_request(self, request, fp, code, msg, headers, newurl):
            return None

    client = build_opener(ProxyHandler({}), NoRedirect())

    class Handler(BaseHTTPRequestHandler):
        def forward(self):
            body = self.rfile.read(int(self.headers.get("Content-Length", 0)))
            headers = {key: value for key, value in self.headers.items()
                       if key.lower() not in ("connection", "transfer-encoding")}
            request = Request(backend + self.path, data=body or None, headers=headers, method=self.command)
            try:
                response = client.open(request, timeout=10)
            except HTTPError as error:
                response = error
            with response:
                body = response.read()
                self.send_response(response.status)
                for name, value in response.headers.items():
                    if name.lower() not in ("connection", "transfer-encoding"):
                        self.send_header(name, value)
                self.end_headers()
                if self.command != "HEAD":
                    self.wfile.write(body)

        def __getattr__(self, name):
            if name.startswith("do_"):
                return self.forward
            raise AttributeError(name)

        def log_message(self, format, *args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    server.socket = context.wrap_socket(server.socket, server_side=True)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"https://127.0.0.1:{server.server_port}"
    finally:
        await asyncio.to_thread(server.shutdown)
        await asyncio.to_thread(server.server_close)
        await asyncio.to_thread(thread.join)
