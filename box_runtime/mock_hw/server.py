import os
import threading
from http.server import ThreadingHTTPServer

from box_runtime.mock_hw.operator_controller import OperatorRunController
from box_runtime.mock_hw.registry import REGISTRY
from box_runtime.mock_hw.web import make_handler


_SERVER = None
_SERVER_THREAD = None
_SERVER_URL = None
_OPERATOR_CONTROLLER = None
_LOCK = threading.Lock()


def _read_host_port(host=None, port=None):
    if host is None:
        host = os.environ.get("BEHAVBOX_MOCK_UI_HOST", "127.0.0.1")
    if port is None:
        raw_port = os.environ.get("BEHAVBOX_MOCK_UI_PORT", "8765")
        try:
            port = int(raw_port)
        except ValueError:
            port = 8765
    return host, port


def ensure_server_running(host=None, port=None):
    global _SERVER, _SERVER_THREAD, _SERVER_URL, _OPERATOR_CONTROLLER
    with _LOCK:
        if _SERVER is not None:
            return _SERVER_URL

        host, port = _read_host_port(host=host, port=port)
        static_dir = os.path.join(os.path.dirname(__file__), "static")
        _OPERATOR_CONTROLLER = OperatorRunController()
        handler = make_handler(
            registry=REGISTRY,
            static_dir=static_dir,
            operator_controller=_OPERATOR_CONTROLLER,
        )
        server = ThreadingHTTPServer((host, port), handler)
        bound_host, bound_port = server.server_address
        server_url = f"http://{bound_host}:{bound_port}"

        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()

        _SERVER = server
        _SERVER_THREAD = thread
        _SERVER_URL = server_url
        print(f"Mock hardware web UI available at {server_url}")
        return _SERVER_URL


def stop_server():
    global _SERVER, _SERVER_THREAD, _SERVER_URL, _OPERATOR_CONTROLLER
    with _LOCK:
        if _SERVER is None:
            return
        if _OPERATOR_CONTROLLER is not None:
            _OPERATOR_CONTROLLER.shutdown(timeout_s=1.0)
        _SERVER.shutdown()
        _SERVER.server_close()
        if _SERVER_THREAD and _SERVER_THREAD.is_alive():
            _SERVER_THREAD.join(timeout=1)
        _SERVER = None
        _SERVER_THREAD = None
        _SERVER_URL = None
        _OPERATOR_CONTROLLER = None
