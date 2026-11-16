"""Shared fixtures for tests/test_fork_safety.py (fork-safety TDD track).

Helpers and fixtures only; no test logic lives here. Subprocess test scripts
import this module via the PYTHONPATH set by :func:`child_env`.
"""
import os
import shutil
import socket
import ssl
import subprocess
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from types import SimpleNamespace

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCRIPTS_DIR = os.path.join(REPO_ROOT, "scripts")
TESTS_DIR = os.path.dirname(os.path.abspath(__file__))
PYTHON = sys.executable
HOST = "127.0.0.1"


def make_self_signed_cert(directory):
    """Generate a self-signed cert+key valid for localhost / 127.0.0.1.

    Returns (cert_path, key_path).
    """
    key = os.path.join(directory, "fork-safety-key.pem")
    cert = os.path.join(directory, "fork-safety-cert.pem")
    openssl = shutil.which("openssl")
    if openssl is None:
        raise RuntimeError("openssl not found in PATH")
    subprocess.run(
        [openssl, "req", "-x509", "-newkey", "rsa:2048", "-nodes",
         "-keyout", key, "-out", cert, "-days", "2",
         "-subj", "/CN=localhost",
         "-addext", "subjectAltName=DNS:localhost,IP:127.0.0.1"],
        check=True, capture_output=True)
    return cert, key


def build_server_context(cert, key):
    ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    ctx.load_cert_chain(cert, key)
    ctx.minimum_version = ssl.TLSVersion.TLSv1_3
    return ctx


def build_client_context(cert):
    ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    ctx.load_verify_locations(cert)
    ctx.minimum_version = ssl.TLSVersion.TLSv1_3
    return ctx


def child_env(extra=None):
    env = dict(os.environ)
    env["PYTHONPATH"] = os.pathsep.join([SCRIPTS_DIR, TESTS_DIR])
    env["FORK_SAFETY_SCRIPTS_DIR"] = SCRIPTS_DIR
    env["FORK_SAFETY_TESTS_DIR"] = TESTS_DIR
    if extra:
        env.update(extra)
    return env


def run_python_script(script, env_extra=None, timeout=180):
    """Run an inline python script in the tox python; return CompletedProcess."""
    return subprocess.run([PYTHON, "-B", "-c", script], capture_output=True,
                          text=True, timeout=timeout, env=child_env(env_extra),
                          cwd=REPO_ROOT)


def serve_pingpong(listener, server_ctx, rounds):
    """Accept one TLS connection, do ``rounds`` PING:n/PONG:n exchanges, then
    send a ``STATUS:OK`` or ``STATUS:ERR:<detail>`` line.

    Returns (ok, detail). Any TLS error (e.g. a bad_record_mac caused by a
    child writing to the inherited stream) is reported in the STATUS line.
    """
    conn, _ = listener.accept()
    tls = server_ctx.wrap_socket(conn, server_side=True)
    tls.settimeout(30)
    try:
        errors = []
        lines = tls.makefile("rb")
        for n in range(1, rounds + 1):
            line = lines.readline()
            expected = ("PING:%d" % n).encode("ascii")
            if line.rstrip(b"\n") != expected:
                errors.append("round %d: got %r" % (n, line[:80]))
                break
            tls.sendall(("PONG:%d\n" % n).encode("ascii"))
        if errors:
            _safe_send(tls, ("STATUS:ERR:%s\n" % errors[0])[:200].encode("ascii"))
            return False, errors[0]
        _safe_send(tls, b"STATUS:OK\n")
        return True, "ok"
    except ssl.SSLError as exc:
        first = str(exc).splitlines()[0]
        _safe_send(tls, ("STATUS:ERR:%s\n" % first)[:200].encode("ascii"))
        return False, "SSL: %s" % first
    except (OSError, ValueError) as exc:
        _safe_send(tls, ("STATUS:ERR:%s: %s\n" % (type(exc).__name__, exc))[:200]
                  .encode("ascii"))
        return False, "%s: %s" % (type(exc).__name__, exc)
    finally:
        try:
            tls.close()
        except OSError:
            pass


def _safe_send(tls, data):
    try:
        tls.sendall(data)
    except OSError:
        pass


def _do_get(handler, state):
    body = ("OK %s\n" % handler.path).encode("ascii")
    handler.send_response(200)
    handler.send_header("Content-Type", "text/plain")
    handler.send_header("Content-Length", str(len(body)))
    handler.end_headers()
    handler.wfile.write(body)
    state["seen"].append((handler.path, handler.client_address[1]))


def make_keepalive_http_server(host=HOST):
    """Threaded HTTP/1.1 keep-alive server. Returns a namespace with:
    server, thread, port, seen (list of (path, client_port)), errors
    (list of 'ExcType: first line' recorded via handle_error)."""
    state = {"seen": [], "errors": []}
    return _make_keepalive_server(host, state, ssl_ctx=None)


def make_keepalive_https_server(host, cert, key):
    state = {"seen": [], "errors": []}
    return _make_keepalive_server(host, state,
                                  ssl_ctx=build_server_context(cert, key))


def _make_keepalive_server(host, state, ssl_ctx=None):
    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def do_GET(self):
            _do_get(self, state)

        def log_message(self, *args):
            pass

    class Server(ThreadingHTTPServer):
        daemon_threads = True
        allow_reuse_address = True

        def handle_error(self, request, client_address):
            exc = sys.exc_info()[1]
            text = str(exc)
            first = text.splitlines()[0] if text else type(exc).__name__
            state["errors"].append("%s: %s" % (type(exc).__name__, first))

    server = Server((host, 0), Handler)
    if ssl_ctx is not None:
        server.socket = ssl_ctx.wrap_socket(server.socket, server_side=True)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return SimpleNamespace(server=server, thread=thread,
                           port=server.server_address[1],
                           seen=state["seen"], errors=state["errors"])
