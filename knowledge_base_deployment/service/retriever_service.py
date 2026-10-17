#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import hmac
import json
import os
import secrets
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any


UPSTREAM_PATH = "/api/V1/open-portal/app/retriever/search/agent_kb"
DEFAULT_UPSTREAM = "https://open.agibot.com"


def load_env(path: str) -> dict[str, str]:
    values: dict[str, str] = {}
    with open(path, encoding="utf-8-sig") as handle:
        for raw in handle:
            line = raw.strip()
            if not line or line.startswith("#"):
                continue
            key, separator, value = line.partition("=")
            if separator:
                values[key.strip()] = value.strip()
    return values


def require_config(values: dict[str, str]) -> dict[str, str]:
    aliases = {
        "RETRIEVER_APP_ID": ("RETRIEVER_APP_ID", "LINGXI_APP_ID"),
        "RETRIEVER_APP_KEY": ("RETRIEVER_APP_KEY", "LINGXI_APP_KEY"),
        "RETRIEVER_APP_SECRET": ("RETRIEVER_APP_SECRET", "LINGXI_APP_SECRET"),
        "RETRIEVER_AGENT_CODE": ("RETRIEVER_AGENT_CODE",),
    }
    result: dict[str, str] = {}
    missing: list[str] = []
    for target, candidates in aliases.items():
        value = next((values.get(key, "") for key in candidates if values.get(key, "")), "")
        if not value:
            missing.append(target)
        result[target] = value
    if missing:
        raise ValueError("知识库配置缺少：" + ", ".join(missing))
    result["RETRIEVER_BASE_URL"] = values.get("RETRIEVER_BASE_URL", DEFAULT_UPSTREAM).rstrip("/")
    result["RETRIEVER_TIMEOUT_SEC"] = values.get("RETRIEVER_TIMEOUT_SEC", "20")
    return result


def signed_headers(config: dict[str, str]) -> dict[str, str]:
    timestamp = str(int(time.time() * 1000))
    nonce = secrets.token_urlsafe(12)
    message = f"POST\n{UPSTREAM_PATH}\n{timestamp}\n{nonce}"
    signature = hmac.new(
        config["RETRIEVER_APP_SECRET"].encode(), message.encode(), hashlib.sha256
    ).hexdigest()
    return {
        "Content-Type": "application/json; charset=utf-8",
        "X-App-Id": config["RETRIEVER_APP_ID"],
        "X-App-Key": config["RETRIEVER_APP_KEY"],
        "X-Timestamp": timestamp,
        "X-Nonce": nonce,
        "X-Signature": signature,
    }


def search(config: dict[str, str], request: dict[str, Any]) -> tuple[int, dict[str, Any]]:
    query = request.get("query")
    if not isinstance(query, str) or not query.strip():
        return 400, {"ok": False, "message": "query 必须是非空字符串"}
    top_k = request.get("top_k", 10)
    if isinstance(top_k, bool) or not isinstance(top_k, int) or not 1 <= top_k <= 50:
        return 400, {"ok": False, "message": "top_k 必须是 1 到 50 的整数"}
    score_threshold = request.get("score_threshold", 0)
    if isinstance(score_threshold, bool) or not isinstance(score_threshold, (int, float)):
        return 400, {"ok": False, "message": "score_threshold 必须是数字"}
    body = {
        "agent_code": config["RETRIEVER_AGENT_CODE"],
        "query": query.strip(),
        "top_k": top_k,
        "rerank": bool(request.get("rerank", True)),
        "check_qa_first": bool(request.get("check_qa_first", True)),
        "score_threshold": score_threshold,
    }
    url = config["RETRIEVER_BASE_URL"] + UPSTREAM_PATH
    encoded = json.dumps(body, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(url, data=encoded, headers=signed_headers(config), method="POST")
    try:
        with urllib.request.urlopen(req, timeout=float(config["RETRIEVER_TIMEOUT_SEC"])) as response:
            status = response.status
            raw = response.read().decode("utf-8", errors="replace")
    except urllib.error.HTTPError as exc:
        raw = exc.read().decode("utf-8", errors="replace")
        try:
            detail = json.loads(raw)
        except json.JSONDecodeError:
            detail = {"message": raw[:500]}
        return exc.code, {"ok": False, "message": "灵心知识库接口返回错误", "upstream": detail}
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        return 502, {"ok": False, "message": "无法连接灵心知识库接口", "detail": str(exc)[:300]}
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError:
        return 502, {"ok": False, "message": "灵心知识库接口返回了无效 JSON"}
    return status, {"ok": True, "data": payload}


class Handler(BaseHTTPRequestHandler):
    server: "KbServer"

    def log_message(self, format: str, *args: object) -> None:
        sys.stderr.write("知识库服务: " + (format % args) + "\n")

    def send_json(self, status: int, payload: dict[str, Any]) -> None:
        encoded = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(encoded)))
        self.end_headers()
        self.wfile.write(encoded)

    def do_GET(self) -> None:
        if self.path == "/health":
            self.send_json(200, {"ok": True, "service": "sparkrobot-lingxi-kb", "agent_code_configured": bool(self.server.config.get("RETRIEVER_AGENT_CODE"))})
            return
        self.send_json(404, {"ok": False, "message": "接口不存在"})

    def do_POST(self) -> None:
        if self.path != "/search":
            self.send_json(404, {"ok": False, "message": "接口不存在"})
            return
        try:
            length = int(self.headers.get("Content-Length", "0"))
            if length > 64 * 1024:
                raise ValueError("请求体过大")
            request = json.loads(self.rfile.read(length))
            if not isinstance(request, dict):
                raise ValueError("请求体必须是 JSON 对象")
        except (ValueError, json.JSONDecodeError) as exc:
            self.send_json(400, {"ok": False, "message": f"请求格式错误：{exc}"})
            return
        status, payload = search(self.server.config, request)
        self.send_json(status, payload)


class KbServer(ThreadingHTTPServer):
    def __init__(self, address: tuple[str, int], config: dict[str, str]):
        super().__init__(address, Handler)
        self.config = config


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", default="/etc/sparkrobot/lingxi-kb/retriever.env")
    parser.add_argument("--host", default=os.environ.get("KB_HOST", "127.0.0.1"))
    parser.add_argument("--port", type=int, default=int(os.environ.get("KB_PORT", "18084")))
    args = parser.parse_args()
    try:
        config = require_config(load_env(args.config))
    except (OSError, ValueError) as exc:
        print(f"知识库服务启动失败：{exc}", file=sys.stderr)
        return 1
    server = KbServer((args.host, args.port), config)
    print(f"灵心知识库检索服务已启动：{args.host}:{args.port}", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
