from __future__ import annotations

import argparse
import http.client
import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlsplit


NAV_GET_PATHS = {"/points", "/nav/status", "/localization/current"}
NAV_POST_PATHS = {"/tts/speak"}
NAV_POST_PREFIXES = ("/nav/", "/task/", "/interaction/", "/motion/")


def uses_navigation_upstream(method: str, path: str) -> bool:
    normalized_method = str(method or "").strip().upper()
    normalized_path = urlsplit(str(path or "")).path
    if normalized_method == "GET":
        return normalized_path in NAV_GET_PATHS
    if normalized_method == "POST":
        return normalized_path in NAV_POST_PATHS or normalized_path.startswith(NAV_POST_PREFIXES)
    return False


class GatewayHandler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def do_GET(self) -> None:
        self._proxy("GET")

    def do_POST(self) -> None:
        self._proxy("POST")

    def do_DELETE(self) -> None:
        self._proxy("DELETE")

    def _proxy(self, method: str) -> None:
        path = urlsplit(self.path).path
        if method == "GET" and path == "/gateway/health":
            self._gateway_health()
            return
        use_navigation = uses_navigation_upstream(method, path)
        port = self.server.nav_port if use_navigation else self.server.voice_port
        body_length = int(self.headers.get("Content-Length", "0") or "0")
        body = self.rfile.read(body_length) if body_length > 0 else None
        connection = http.client.HTTPConnection("127.0.0.1", port, timeout=8)
        try:
            headers = {}
            content_type = self.headers.get("Content-Type")
            if content_type:
                headers["Content-Type"] = content_type
            connection.request(method, self.path, body=body, headers=headers)
            response = connection.getresponse()
            payload = response.read()
            self.send_response(response.status)
            self.send_header("Content-Type", response.getheader("Content-Type", "application/json; charset=utf-8"))
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)
        except Exception as exc:
            payload = json.dumps(
                {
                    "ok": False,
                    "status": "upstream_offline",
                    "message": f"服务连接失败：{exc}",
                },
                ensure_ascii=False,
            ).encode("utf-8")
            self.send_response(502)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)
        finally:
            connection.close()

    def _gateway_health(self) -> None:
        checks = {
            "navigation": self._check_upstream(self.server.nav_port, "/nav/status"),
            "voice": self._check_upstream(self.server.voice_port, "/health"),
        }
        payload = json.dumps(
            {
                "ok": all(item["ok"] for item in checks.values()),
                "status": "online" if all(item["ok"] for item in checks.values()) else "degraded",
                "services": checks,
            },
            ensure_ascii=False,
        ).encode("utf-8")
        self.send_response(200 if all(item["ok"] for item in checks.values()) else 503)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    @staticmethod
    def _check_upstream(port: int, path: str) -> dict[str, object]:
        connection = http.client.HTTPConnection("127.0.0.1", port, timeout=2)
        try:
            connection.request("GET", path)
            response = connection.getresponse()
            response.read()
            ok = 200 <= response.status < 300
            return {
                "ok": ok,
                "port": port,
                "http_status": response.status,
                "message": "服务在线" if ok else f"服务返回 HTTP {response.status}",
            }
        except Exception as exc:
            return {"ok": False, "port": port, "message": f"服务未连接：{exc}"}
        finally:
            connection.close()

    def log_message(self, fmt: str, *args: object) -> None:
        print(f"[{self.log_date_time_string()}] {self.address_string()} {fmt % args}", flush=True)


def main() -> None:
    parser = argparse.ArgumentParser(description="Lingxi voice and navigation compatibility gateway")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=18083)
    parser.add_argument("--voice-port", type=int, default=18080)
    parser.add_argument("--nav-port", type=int, default=18082)
    args = parser.parse_args()
    server = ThreadingHTTPServer((args.host, args.port), GatewayHandler)
    server.voice_port = args.voice_port
    server.nav_port = args.nav_port
    print(
        f"voice-nav gateway listening on {args.host}:{args.port}; voice={args.voice_port}; nav={args.nav_port}",
        flush=True,
    )
    server.serve_forever()


if __name__ == "__main__":
    main()
