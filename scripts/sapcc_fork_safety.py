"""Reset inherited Eventlet hubs, TLS sockets, HTTP pools, and DB pools after fork."""

import _socket
import gc
import os
import queue
import ssl
import sys

_SSLSOCKET_CLS = ssl.SSLSocket
_INSTALLED = False


def is_disabled():
    return os.environ.get("SAPCC_DISABLE_FORK_SAFETY", "").strip().lower() in (
        "1", "true", "yes", "on"
    )


def _neuter_sock(sock):
    if sock is not None:
        try:
            sock._sslobj = None
            sock._io_refs = 0
            sock._closed = True
        except AttributeError:
            pass
        try:
            _socket.socket.close(sock)
        except (OSError, TypeError):
            pass


def _after_fork_in_child():
    hubs = sys.modules.get("eventlet.hubs")
    if getattr(getattr(hubs, "_threadlocal", None), "hub", None) is not None:
        hubs.use_hub()

    pool_cls = getattr(
        sys.modules.get("urllib3.connectionpool"), "HTTPConnectionPool", ()
    )
    engine_cls = getattr(sys.modules.get("sqlalchemy.engine"), "Engine", ())
    pymysql_cls = getattr(
        sys.modules.get("pymysql.connections"), "Connection", ()
    )
    for obj in gc.get_objects():
        if isinstance(obj, _SSLSOCKET_CLS):
            _neuter_sock(obj)
        elif pool_cls and isinstance(obj, pool_cls) and obj.pool is not None:
            while True:
                try:
                    conn = obj.pool.get(block=False)
                except queue.Empty:
                    break
                _neuter_sock(getattr(conn, "sock", None))
            for _ in range(obj.pool.maxsize or 0):
                try:
                    obj.pool.put(None, block=False)
                except queue.Full:
                    break
        elif engine_cls and isinstance(obj, engine_cls):
            try:
                obj.dispose(close=False)
            except TypeError:
                pass
        elif pymysql_cls and isinstance(obj, pymysql_cls):
            _neuter_sock(getattr(obj, "_sock", None))
            obj._sock = None
            obj._rfile = None


def install_all():
    global _INSTALLED
    if is_disabled():
        return False
    if not _INSTALLED:
        os.register_at_fork(after_in_child=_after_fork_in_child)
        _INSTALLED = True
    return True


def is_installed():
    return _INSTALLED
