"""Behavior tests for scripts/sapcc_fork_safety.py.

The module installs one os.register_at_fork(after_in_child=...) hook.

  test_1  TLS 1.3 stream preservation across os.fork() (no eventlet)
  test_2  eventlet GreenSSLSocket + Popen(preexec_fn) + after_in_child thread
  test_3  http + https pools reset across os.fork() (no eventlet)
  test_4  https pool under eventlet fork (hub reset via eventlet.hubs.use_hub)
  test_5  inherited parent greenlets must not run in a Popen(preexec_fn) child
  test_6  SQLAlchemy Engine + pymysql connection reset across os.fork()
  test_7  setup_fork_safety.py, .pth auto-activation, disable env, install.sh

Run:  python -B -m unittest discover -s tests -v
"""
import json
import os
import shutil
import socket
import ssl
import subprocess
import sys
import tempfile
import threading
import unittest

from fork_safety_helpers import (  # noqa: E402  (tests dir on discovery path)
    HOST,
    PYTHON,
    REPO_ROOT,
    SCRIPTS_DIR,
    TESTS_DIR,
    build_client_context,
    build_server_context,
    make_self_signed_cert,
    run_python_script,
    serve_pingpong,
)

sys.path.insert(0, SCRIPTS_DIR)
import sapcc_fork_safety  # noqa: E402


TEST2_SCRIPT = r'''
import os
import sys

sys.path.insert(0, os.environ["FORK_SAFETY_SCRIPTS_DIR"])
import sapcc_fork_safety

# The .pth installs the fork hook at interpreter startup, before the
# application imports eventlet.
sapcc_fork_safety.install_all()

import eventlet
eventlet.monkey_patch()

import json
import socket
import ssl
import subprocess
import threading

sys.path.insert(0, os.environ["FORK_SAFETY_TESTS_DIR"])
from fork_safety_helpers import HOST, build_client_context, build_server_context

CERT = os.environ["FORK_SAFETY_CERT"]
KEY = os.environ["FORK_SAFETY_KEY"]
server_ctx = build_server_context(CERT, KEY)
client_ctx = build_client_context(CERT)

listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
listener.bind((HOST, 0))
listener.listen(1)
port = listener.getsockname()[1]

errors = []

def server():
    conn, _ = listener.accept()
    tls = None
    try:
        tls = server_ctx.wrap_socket(conn, server_side=True)
        lines = tls.makefile("rb")
        while True:
            line = lines.readline()
            if not line:
                break
            tag = line.strip()
            if tag.startswith(b"PING:"):
                tls.sendall(b"PONG:" + tag[5:] + b"\n")
            elif tag.startswith(b"BGPING:"):
                tls.sendall(b"OK " + tag + b"\n")
            elif tag == b"BYE":
                break
    except ssl.SSLError as exc:
        errors.append("SSL: " + str(exc).splitlines()[0])
    except Exception as exc:  # noqa: BLE001
        errors.append("%s: %s" % (type(exc).__name__, str(exc).splitlines()[0]))
    finally:
        if tls is not None:
            try:
                status = b"STATUS:OK\n" if not errors else \
                    ("STATUS:ERR:" + errors[0][:120] + b"\n").encode("ascii")
                tls.sendall(status)
                tls.close()
            except OSError:
                pass

eventlet.spawn(server)

raw = socket.create_connection((HOST, port), timeout=15)
client = eventlet.wrap_ssl(raw, context=client_ctx, server_hostname="localhost")
assert any(c.__name__ == "GreenSSLSocket" for c in type(client).__mro__), type(client)
assert client.version() == "TLSv1.3", client.version()
client_file = client.makefile("rb")

stop_bg = threading.Event()

def bg_writer():
    i = 0
    while not stop_bg.is_set():
        i += 1
        try:
            client.sendall(("BGPING:%d\n" % i).encode("ascii"))
        except Exception:
            break
        eventlet.sleep(0.005)

bg = eventlet.spawn(bg_writer)

def wait_pong(n):
    while True:
        line = client_file.readline()
        if not line:
            raise EOFError("EOF while waiting for PONG:%d" % n)
        if line.rstrip(b"\n") == ("PONG:%d" % n).encode("ascii"):
            return

def otel_sim():
    # Simulate OpenTelemetry BatchSpanProcessor._at_fork_reinit: start a
    # thread, then briefly block on an Event.
    woken = threading.Event()
    threading.Thread(target=woken.set).start()
    woken.wait(0.05)

os.register_at_fork(after_in_child=otel_sim)

client.sendall(b"PING:1\n")
wait_pong(1)

p = subprocess.Popen(["/usr/bin/true"], preexec_fn=lambda: None)
popen_rc = p.wait(timeout=10)

completed = 0
for n in (2, 3, 4):
    client.sendall(("PING:%d\n" % n).encode("ascii"))
    wait_pong(n)
    completed += 1

client.sendall(b"BYE\n")
status_line = b""
# The background writer can still be sending BGPING lines while BYE is in
# flight, so skip the interleaved "OK BGPING:n" answers.
while True:
    line = client_file.readline()
    if not line or line.startswith(b"STATUS:"):
        status_line = line
        break
client.close()
stop_bg.set()
eventlet.sleep(0.05)

facts = {"popen_rc": popen_rc,
         "server_errors": errors,
         "server_status": status_line.decode("ascii", "replace").rstrip("\n"),
         "parent_rounds_completed": completed}
print(json.dumps(facts))
sys.exit(0)
'''

TEST3_SCRIPT = r'''
import json
import os
import sys

sys.path.insert(0, os.environ["FORK_SAFETY_SCRIPTS_DIR"])
sys.path.insert(0, os.environ["FORK_SAFETY_TESTS_DIR"])
import sapcc_fork_safety

# The .pth installs the hook before urllib3 and requests are imported.
sapcc_fork_safety.install_all()

import requests  # noqa: E402  (late on purpose)
import urllib3  # noqa: E402,F401  (sanity: importable after install)
from fork_safety_helpers import HOST, make_keepalive_http_server, make_keepalive_https_server

CERT = os.environ["FORK_SAFETY_CERT"]
KEY = os.environ["FORK_SAFETY_KEY"]
http_server = make_keepalive_http_server(HOST)
https_server = make_keepalive_https_server(HOST, CERT, KEY)

http_session = requests.Session()
https_session = requests.Session()

def idle_connections(pool):
    # urllib3 1.26 prefills the pool queue with None placeholders, so qsize()
    # counts free slots as well.  Count the real connections instead.
    q = getattr(pool, "pool", None)
    if q is None:
        return 0
    return sum(1 for conn in q.queue if conn is not None)

pre_http = http_session.get("http://%s:%d/pre" % (HOST, http_server.port), timeout=15)
pre_https = https_session.get("https://%s:%d/pre" % (HOST, https_server.port),
                              verify=CERT, timeout=15)
assert pre_http.status_code == 200, pre_http
assert pre_https.status_code == 200, pre_https
assert pre_http.text.startswith("OK /pre")
assert pre_https.text.startswith("OK /pre")

# requests adds verify/cert derived values to the pool key, so a plain
# connection_from_host() call would build a different, empty pool.  Ask the
# response for the pool that actually holds the pre-fork connection.
http_pool = pre_http.raw._pool
https_pool = pre_https.raw._pool
assert http_pool is not None, "no pool recorded for the pre http request"
assert https_pool is not None, "no pool recorded for the pre https request"
http_qsize = idle_connections(http_pool)
https_qsize = idle_connections(https_pool)
assert http_qsize == 1, "pre http connection not idle in pool: %d" % http_qsize
assert https_qsize == 1, "pre https connection not idle in pool: %d" % https_qsize

child_r, child_w = os.pipe()
pid = os.fork()
if pid == 0:
    os.close(child_r)
    child = {}
    for name, session, port in (("http", http_session, http_server.port),
                                ("https", https_session, https_server.port)):
        try:
            r = session.get("%s://%s:%d/child" % (name, HOST, port),
                            verify=CERT if name == "https" else True,
                            timeout=15)
            child[name] = {"code": r.status_code}
        except Exception as exc:  # noqa: BLE001
            child[name] = {"error": "%s: %s" % (type(exc).__name__,
                                                str(exc).splitlines()[0])[:300]}
    os.write(child_w, json.dumps(child).encode("ascii"))
    os.close(child_w)
    os._exit(0)
os.close(child_w)
os.waitpid(pid, 0)
data = b""
while True:
    chunk = os.read(child_r, 65536)
    if not chunk:
        break
    data += chunk
os.close(child_r)
child = json.loads(data.decode("ascii"))

parent = {}
for name, session, port in (("http", http_session, http_server.port),
                            ("https", https_session, https_server.port)):
    try:
        r = session.get("%s://%s:%d/parent" % (name, HOST, port),
                         verify=CERT if name == "https" else True,
                         timeout=15)
        parent[name] = {"code": r.status_code}
    except Exception as exc:  # noqa: BLE001
        parent[name] = {"error": "%s: %s" % (type(exc).__name__,
                                             str(exc).splitlines()[0])[:300]}

def seen_port(server, path):
    for p, cport in server.seen:
        if p == path:
            return cport
    return None

facts = {
    "http": {"pre_port": seen_port(http_server, "/pre"),
             "child_port": seen_port(http_server, "/child"),
             "parent_port": seen_port(http_server, "/parent"),
             "child": child.get("http"), "parent": parent.get("http")},
    "https": {"pre_port": seen_port(https_server, "/pre"),
              "child_port": seen_port(https_server, "/child"),
              "parent_port": seen_port(https_server, "/parent"),
              "child": child.get("https"), "parent": parent.get("https")},
    "http_pool_qsize_pre": http_qsize,
    "https_pool_qsize_pre": https_qsize,
    "http_server_errors": http_server.errors,
    "https_server_errors": https_server.errors,
}
print(json.dumps(facts))
sys.exit(0)
'''

TEST4_SCRIPT = r'''
import os
import sys

sys.path.insert(0, os.environ["FORK_SAFETY_SCRIPTS_DIR"])
import sapcc_fork_safety

# The .pth installs the hook at interpreter startup, before eventlet, urllib3,
# and requests are imported.
sapcc_fork_safety.install_all()

import eventlet
eventlet.monkey_patch()

import json
import requests  # noqa: E402  (late on purpose)
from fork_safety_helpers import HOST, make_keepalive_https_server  # noqa: E402

CERT = os.environ["FORK_SAFETY_CERT"]
KEY = os.environ["FORK_SAFETY_KEY"]
server = make_keepalive_https_server(HOST, CERT, KEY)

session = requests.Session()
r = session.get("https://%s:%d/pre" % (HOST, server.port), verify=CERT, timeout=15)
assert r.status_code == 200, r

pool = r.raw._pool

def idle_connections(pool):
    # urllib3 1.26 prefills the pool queue with None placeholders and the
    # queue class of eventlet.green.queue has no mutex attribute.
    q = getattr(pool, "pool", None)
    if q is None:
        return 0
    return sum(1 for conn in q.queue if conn is not None)

assert pool is not None and idle_connections(pool) == 1, "pre request not pooled"
pre_port = server.seen[-1][1]

child_r, child_w = os.pipe()
pid = os.fork()
if pid == 0:
    os.close(child_r)
    try:
        # Reset the inherited eventlet hub, as oslo.service does for a worker.
        eventlet.hubs.use_hub()
        r = session.get("https://%s:%d/child" % (HOST, server.port),
                        verify=CERT, timeout=15)
        payload = {"code": r.status_code}
    except Exception as exc:  # noqa: BLE001
        payload = {"error": "%s: %s" % (type(exc).__name__,
                                        str(exc).splitlines()[0])[:300]}
    os.write(child_w, json.dumps(payload).encode("ascii"))
    os.close(child_w)
    os._exit(0)
os.close(child_w)
os.waitpid(pid, 0)
data = b""
while True:
    chunk = os.read(child_r, 65536)
    if not chunk:
        break
    data += chunk
os.close(child_r)
child = json.loads(data.decode("ascii"))

try:
    r = session.get("https://%s:%d/parent" % (HOST, server.port),
                    verify=CERT, timeout=15)
    parent = {"code": r.status_code}
except Exception as exc:  # noqa: BLE001
    parent = {"error": "%s: %s" % (type(exc).__name__,
                                   str(exc).splitlines()[0])[:300]}

def seen_port(path):
    for p, cport in server.seen:
        if p == path:
            return cport
    return None

facts = {"pre_port": pre_port,
         "child_port": seen_port("/child"),
         "parent_port": seen_port("/parent"),
         "child": child,
         "parent": parent,
         "server_errors": server.errors}
print(json.dumps(facts))
sys.exit(0)
'''

TEST5_SCRIPT = r'''
import os
import subprocess
import sys

sys.path.insert(0, os.environ["FORK_SAFETY_SCRIPTS_DIR"])
sys.path.insert(0, os.environ["FORK_SAFETY_TESTS_DIR"])
import sapcc_fork_safety

# Exactly like sapcc_fork_safety.pth does at interpreter startup.
sapcc_fork_safety.install_all()

import eventlet
eventlet.monkey_patch()

import eventlet.tpool  # noqa: E402
import json
import threading

def run_tpool():
    # A stuck parent tpool must fail the test instead of hanging the suite.
    with eventlet.Timeout(5):
        return eventlet.tpool.execute(lambda: 42)

parent_pid = os.getpid()
child_pipe_r, child_pipe_w = os.pipe()
parent_runs = [0]

def loop_greenlet():
    while True:
        if os.getpid() == parent_pid:
            parent_runs[0] += 1
        else:
            try:
                os.write(child_pipe_w, b"c")
            except OSError:
                pass
        eventlet.sleep(0.001)

g = eventlet.spawn(loop_greenlet)
eventlet.sleep(0.01)  # let it run in the parent: queued on the parent hub timers
runs_before = parent_runs[0]
assert runs_before > 0, "parent greenlet never ran before fork"
tpool_before = run_tpool()
assert tpool_before == 42, tpool_before

def otel_sim():
    # Simulate OpenTelemetry BatchSpanProcessor._at_fork_reinit: start a
    # thread, then briefly block on an Event.
    woken = threading.Event()
    threading.Thread(target=woken.set).start()
    woken.wait(0.01)

os.register_at_fork(after_in_child=otel_sim)

p = subprocess.Popen(["/usr/bin/true"], preexec_fn=lambda: None)
popen_rc = p.wait(timeout=5)
tpool_after = run_tpool()
assert tpool_after == 42, tpool_after

eventlet.sleep(0.1)
runs_after = parent_runs[0]

os.set_blocking(child_pipe_r, False)
# Close the write end first.  With eventlet monkey patching os.read is green
# and retries on EAGAIN, so an inherited write end would block this process
# forever instead of reporting that no child greenlet ran.
os.close(child_pipe_w)
child_runs = 0
try:
    while True:
        chunk = os.read(child_pipe_r, 65536)
        if not chunk:
            break
        child_runs += chunk.count(b"c")
except (BlockingIOError, InterruptedError):
    pass
except OSError:
    pass
os.close(child_pipe_r)

facts = {"popen_rc": popen_rc,
         "child_greenlet_runs": child_runs,
         "parent_runs_before": runs_before,
         "parent_runs_after_popen": runs_after - runs_before,
         "tpool_before": tpool_before,
         "tpool_after": tpool_after}
print(json.dumps(facts))
sys.exit(0)
'''

TEST6_SCRIPT = r'''
import json
import os
import socket
import sys

sys.path.insert(0, os.environ["FORK_SAFETY_SCRIPTS_DIR"])
import sapcc_fork_safety

# The .pth installs the hook before SQLAlchemy and pymysql are imported.
sapcc_fork_safety.install_all()

import pymysql.connections  # noqa: E402
from sqlalchemy import create_engine  # noqa: E402
from sqlalchemy.pool import QueuePool  # noqa: E402

# The default pool for the in-memory SQLite URL is a SingletonThreadPool,
# which has no checkedin() method.  Use a QueuePool to observe the checked-in
# connection.
engine = create_engine("sqlite://", poolclass=QueuePool)
checked_out = engine.connect()
checked_out.close()
checkedin_before = engine.pool.checkedin()
assert checkedin_before == 1, checkedin_before

parent_side, server_side = socket.socketpair()
conn = pymysql.connections.Connection.__new__(pymysql.connections.Connection)
conn._sock = parent_side
conn._rfile = parent_side.makefile("rb")
conn._closed = False

status_r, status_w = os.pipe()
pid = os.fork()
if pid == 0:
    os.close(status_r)
    facts = {}
    try:
        facts["checkedin"] = engine.pool.checkedin()
    except Exception as exc:  # noqa: BLE001
        facts["checkedin_error"] = "%s: %s" % (type(exc).__name__, exc)
    facts["sock_is_none"] = conn._sock is None
    facts["rfile_is_none"] = conn._rfile is None
    try:
        # conn._sock is None after the fork hook, so close() sends no
        # COM_QUIT packet on the socket of the parent.
        conn.close()
    except Exception as exc:  # noqa: BLE001
        facts["close_error"] = "%s: %s" % (type(exc).__name__, exc)
    else:
        facts["close"] = "ok"
    os.write(status_w, json.dumps(facts).encode("ascii"))
    os.close(status_w)
    os._exit(0)
os.close(status_w)
os.waitpid(pid, 0)
data = b""
while True:
    chunk = os.read(status_r, 65536)
    if not chunk:
        break
    data += chunk
os.close(status_r)
child = json.loads(data.decode("ascii"))

server_side.settimeout(0.3)
try:
    received = server_side.recv(1024)
except socket.timeout:
    received = b""

parent_side.sendall(b"ping")
server_side.settimeout(5)
reply = server_side.recv(1024)

facts = {"checkedin_before": checkedin_before,
         "child": child,
         "child_sent_bytes": len(received),
         "child_sent": received.decode("latin-1"),
         "parent_reply": reply.decode("ascii", "replace"),
         "parent_sock_open": parent_side.fileno() != -1}
print(json.dumps(facts))
ok = (child.get("checkedin") == 0
      and child.get("close") == "ok"
      and child.get("sock_is_none")
      and child.get("rfile_is_none")
      and len(received) == 0
      and reply == b"ping"
      and parent_side.fileno() != -1)
sys.exit(0 if ok else 1)
'''


class ForkSafetyIterationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls._tmpdir = tempfile.TemporaryDirectory()
        cls.cert, cls.key = make_self_signed_cert(cls._tmpdir.name)

    @classmethod
    def tearDownClass(cls):
        cls._tmpdir.cleanup()

    def _subprocess_env(self):
        return {"FORK_SAFETY_CERT": self.cert, "FORK_SAFETY_KEY": self.key}

    def _run_scenario(self, script):
        proc = run_python_script(script, self._subprocess_env())
        self.assertEqual(
            proc.returncode, 0,
            "scenario aborted (rc=%d)\nSTDOUT:\n%s\nSTDERR:\n%s"
            % (proc.returncode, proc.stdout, proc.stderr))
        lines = [l for l in proc.stdout.splitlines() if l.strip()]
        self.assertTrue(lines, "script printed nothing:\n%s\n%s"
                        % (proc.stdout, proc.stderr))
        return json.loads(lines[-1])

    def test_1_tls13_stream_preserved_across_os_fork_no_eventlet(self):
        """Parent-child TLS 1.3 stream survives os.fork(): child I/O on the
        inherited SSLSocket raises without transmitting bytes; the parent's
        session keeps working with 0 server-side bad_record_mac/EOF errors."""
        sapcc_fork_safety.install_all()
        server_ctx = build_server_context(self.cert, self.key)
        client_ctx = build_client_context(self.cert)
        listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        listener.bind((HOST, 0))
        listener.listen(1)
        port = listener.getsockname()[1]
        server_result = []
        thread = threading.Thread(
            target=lambda: server_result.append(
                serve_pingpong(listener, server_ctx, 2)),
            daemon=True)
        thread.start()
        try:
            raw = socket.create_connection((HOST, port), timeout=15)
            client = client_ctx.wrap_socket(raw, server_hostname="localhost")
            self.assertEqual(client.version(), "TLSv1.3")
            client.sendall(b"PING:1\n")
            client_file = client.makefile("rb")
            self.assertEqual(client_file.readline(), b"PONG:1\n")

            r, w = os.pipe()
            pid = os.fork()
            if pid == 0:
                os.close(r)
                status = {}
                try:
                    client.send(b"child-corrupt")
                except (OSError, ssl.SSLError, ValueError) as exc:
                    status["send"] = type(exc).__name__
                else:
                    status["send"] = "no-exception"
                try:
                    client.unwrap()
                except (OSError, ssl.SSLError, ValueError) as exc:
                    status["unwrap"] = type(exc).__name__
                else:
                    status["unwrap"] = "no-exception"
                try:
                    client.close()
                except Exception as exc:  # noqa: BLE001
                    status["close"] = type(exc).__name__
                else:
                    status["close"] = "no-exception"
                try:
                    os.write(w, json.dumps(status).encode("ascii"))
                except OSError:
                    pass
                os._exit(0)
            os.close(w)
            os.waitpid(pid, 0)
            chunks = []
            while True:
                chunk = os.read(r, 65536)
                if not chunk:
                    break
                chunks.append(chunk)
            os.close(r)
            child_status = json.loads(b"".join(chunks).decode("ascii"))
            self.assertIn(child_status["send"],
                          {"OSError", "SSLError", "ValueError"},
                          "child send() must raise; got %r" % child_status)
            self.assertIn(child_status["unwrap"],
                          {"OSError", "SSLError", "ValueError"},
                          "child unwrap() must raise; got %r" % child_status)

            # The parent's TLS 1.3 session must be undisturbed.
            client.sendall(b"PING:2\n")
            self.assertEqual(client_file.readline(), b"PONG:2\n",
                           "parent lost its TLS stream across the fork")
            self.assertEqual(client_file.readline(), b"STATUS:OK\n",
                           "server saw TLS errors (bad_record_mac/EOF)")
            self.assertTrue(server_result[0][0],
                            "server reported: %s" % server_result[0][1])
        finally:
            try:
                client.close()
            except OSError:
                pass
            listener.close()
            thread.join(timeout=10)

    def test_2_eventlet_greenssl_socket_popen_preexec_after_in_child_thread_wakeup(self):
        """Under eventlet, an after_in_child callback that starts a thread
        (BatchSpanProcessor._at_fork_reinit simulation) around
        Popen(preexec_fn=...) must not let the child write to the inherited
        GreenSSLSocket; the parent's TLS 1.3 stream stays clean."""
        facts = self._run_scenario(TEST2_SCRIPT)
        self.assertEqual(facts["popen_rc"], 0, facts)
        self.assertEqual(facts["server_errors"], [],
                         "server TLS errors (child bytes on the wire?): %r"
                         % facts["server_errors"])
        self.assertEqual(facts["server_status"], "STATUS:OK", facts)
        self.assertEqual(facts["parent_rounds_completed"], 3,
                         "parent stream did not continue cleanly: %r" % facts)

    def test_3_urllib3_http_and_https_pools_reset_across_os_fork_no_eventlet(self):
        """Pre-fork keep-alive requests on requests.Session(); the child's
        request must open a NEW connection (child_port != pre_port) while the
        parent reuses its original one (parent_port == pre_port); 0 server
        errors. The hook is installed before `import urllib3`/`requests`."""
        facts = self._run_scenario(TEST3_SCRIPT)
        self.assertEqual(facts["http_pool_qsize_pre"], 1,
                         "pre http connection not returned to pool")
        self.assertEqual(facts["https_pool_qsize_pre"], 1,
                         "pre https connection not returned to pool")
        for scheme in ("http", "https"):
            info = facts[scheme]
            with self.subTest(scheme=scheme):
                self.assertEqual(info["child"].get("code"), 200,
                               "child %s request failed: %r" % (scheme, info["child"]))
                self.assertEqual(info["parent"].get("code"), 200,
                               "parent %s request failed: %r" % (scheme, info["parent"]))
                self.assertIsNotNone(info["child_port"],
                                     "server never saw a /child %s request" % scheme)
                self.assertNotEqual(info["child_port"], info["pre_port"],
                                    "child reused the parent's keep-alive "
                                    "connection (port %r): child and parent race "
                                    "on one socket" % info["pre_port"])
                self.assertEqual(info["parent_port"], info["pre_port"],
                                 "parent lost its keep-alive connection: "
                                 "pre=%r parent=%r"
                                 % (info["pre_port"], info["parent_port"]))
                self.assertEqual(facts["%s_server_errors" % scheme], [],
                                   "server errors on %s: %r"
                                   % (scheme, facts["%s_server_errors" % scheme]))

    def test_4_urllib3_https_pool_under_eventlet_fork(self):
        """Same invariants as test_3 for the https pool under
        eventlet.monkey_patch() with the eventlet hub reset via
        eventlet.hubs.use_hub() in the child."""
        facts = self._run_scenario(TEST4_SCRIPT)
        self.assertEqual(facts["child"].get("code"), 200,
                         "child request failed: %r" % facts["child"])
        self.assertEqual(facts["parent"].get("code"), 200,
                         "parent request failed: %r" % facts["parent"])
        self.assertIsNotNone(facts["child_port"], "no /child request reached server")
        self.assertNotEqual(facts["child_port"], facts["pre_port"],
                            "child reused the parent's keep-alive TLS connection")
        self.assertEqual(facts["parent_port"], facts["pre_port"],
                         "parent lost its keep-alive connection: %r" % facts)
        self.assertEqual(facts["server_errors"], [],
                         "server TLS errors: %r" % facts["server_errors"])

    def test_5_eventlet_hub_reset_prevents_inherited_greenlet_wakeup_on_popen_preexec(self):
        """install_all() (via the .pth) must reset the inherited eventlet hub
        in after_in_child so no inherited parent greenlet - TLS or not, e.g.
        AMQP heartbeat/_consume - runs in a Popen(preexec_fn) child while a
        later after_in_child callback (BatchSpanProcessor._at_fork_reinit
        simulation) starts a thread and waits on an Event."""
        facts = self._run_scenario(TEST5_SCRIPT)
        self.assertEqual(facts["popen_rc"], 0, facts)
        self.assertEqual(facts["tpool_before"], 42,
                         "parent tpool did not run before fork: %r" % facts)
        self.assertEqual(facts["tpool_after"], 42,
                         "parent tpool deadlocked after fork/Popen: %r" % facts)
        self.assertEqual(facts["child_greenlet_runs"], 0,
                         "inherited parent greenlet executed %d time(s) in the "
                         "child pid - the child resumed parent work"
                         % facts["child_greenlet_runs"])
        self.assertGreaterEqual(facts["parent_runs_after_popen"], 1,
                                "parent greenlet stopped after Popen: %r" % facts)

    def test_6_sqlalchemy_engine_and_pymysql_connection_neutered_across_os_fork(self):
        """The after_in_child hook disposes SQLAlchemy engines without closing
        the inherited DB connections and clears the pymysql socket, so a
        child conn.close() sends no COM_QUIT to the server of the parent."""
        facts = self._run_scenario(TEST6_SCRIPT)
        self.assertEqual(facts["checkedin_before"], 1, facts)
        child = facts["child"]
        self.assertEqual(child.get("checkedin"), 0,
                         "child engine pool still has a checked-in connection: %r"
                         % facts)
        self.assertTrue(child.get("sock_is_none"),
                        "child pymysql socket was not cleared: %r" % facts)
        self.assertTrue(child.get("rfile_is_none"),
                        "child pymysql file object was not cleared: %r" % facts)
        self.assertEqual(child.get("close"), "ok",
                         "child conn.close() failed: %r" % facts)
        self.assertEqual(facts["child_sent_bytes"], 0,
                         "child sent data on the parent socket (COM_QUIT?): %r"
                         % facts)
        self.assertEqual(facts["parent_reply"], "ping",
                         "parent socket is not usable after the fork: %r" % facts)
        self.assertTrue(facts["parent_sock_open"],
                        "parent socket was closed by the child: %r" % facts)

    def test_7_setup_fork_safety_installs_pth_and_respects_disable_env(self):
        """setup_fork_safety.py <target> writes sapcc_fork_safety.py and
        sapcc_fork_safety.pth into <target>; site.addsitedir(<target>) in a
        fresh interpreter then installs the fork hook; the
        SAPCC_DISABLE_FORK_SAFETY=1 opt-out keeps is_installed() False;
        install.sh invokes the setup script before cleanup.sh."""
        target = os.path.join(TESTS_DIR, "_site_packages_fixture")
        shutil.rmtree(target, ignore_errors=True)
        self.addCleanup(shutil.rmtree, target, ignore_errors=True)
        os.makedirs(target)

        setup_script = os.path.join(SCRIPTS_DIR, "setup_fork_safety.py")
        with self.subTest("setup_fork_safety.py writes module and .pth into target"):
            self.assertTrue(os.path.isfile(setup_script),
                            "scripts/setup_fork_safety.py is missing")
            proc = subprocess.run([PYTHON, "-B", setup_script, target],
                                  capture_output=True, text=True, timeout=60,
                                  cwd=REPO_ROOT)
            self.assertEqual(proc.returncode, 0,
                             "setup_fork_safety.py failed:\n%s\n%s"
                             % (proc.stdout, proc.stderr))
            self.assertTrue(os.path.isfile(os.path.join(target, "sapcc_fork_safety.py")),
                            "sapcc_fork_safety.py was not written into the target")
            self.assertTrue(os.path.isfile(os.path.join(target, "sapcc_fork_safety.pth")),
                            "sapcc_fork_safety.pth was not written into the target")

        probe = ("import site\n"
                 "site.addsitedir(%r)\n"
                 "import sapcc_fork_safety\n"
                 "print(sapcc_fork_safety.is_installed())\n" % target)
        with self.subTest("site.addsitedir installs the fork hook"):
            proc = subprocess.run([PYTHON, "-B", "-c", probe],
                                  capture_output=True, text=True, timeout=60)
            self.assertEqual(proc.returncode, 0,
                             "probe failed:\n%s\n%s" % (proc.stdout, proc.stderr))
            self.assertEqual(proc.stdout.strip(), "True",
                             "auto-activation via the .pth did not install "
                             "the fork hook:\n%s\n%s" % (proc.stdout, proc.stderr))
        with self.subTest("SAPCC_DISABLE_FORK_SAFETY=1 keeps is_installed() False"):
            env = dict(os.environ)
            env["SAPCC_DISABLE_FORK_SAFETY"] = "1"
            proc = subprocess.run([PYTHON, "-B", "-c", probe],
                                  capture_output=True, text=True, timeout=60, env=env)
            self.assertEqual(proc.returncode, 0,
                             "probe failed:\n%s\n%s" % (proc.stdout, proc.stderr))
            self.assertEqual(proc.stdout.strip(), "False",
                             "opt-out did not disable activation:\n%s\n%s"
                             % (proc.stdout, proc.stderr))
        with self.subTest("install.sh runs setup_fork_safety.py before cleanup.sh"):
            install_sh = os.path.join(SCRIPTS_DIR, "install.sh")
            self.assertTrue(os.path.isfile(install_sh), "scripts/install.sh missing")
            with open(install_sh) as fh:
                lines = fh.read().splitlines()
            setup_idx = None
            cleanup_after_setup = None
            for i, line in enumerate(lines):
                stripped = line.strip()
                if (setup_idx is None
                        and "setup_fork_safety.py" in stripped
                        and stripped.startswith("$(dirname $0)/setup_fork_safety.py")):
                    setup_idx = i
            if setup_idx is not None:
                for i in range(setup_idx + 1, len(lines)):
                    if lines[i].strip().startswith("$(dirname $0)/cleanup.sh"):
                        cleanup_after_setup = i
                        break
            self.assertIsNotNone(setup_idx,
                                 "install.sh does not invoke "
                                 "$(dirname $0)/setup_fork_safety.py")
            self.assertIsNotNone(cleanup_after_setup,
                                 "install.sh does not invoke "
                                 "$(dirname $0)/cleanup.sh after "
                                 "setup_fork_safety.py")


if __name__ == "__main__":
    unittest.main()
