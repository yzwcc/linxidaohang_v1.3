from __future__ import annotations

import hashlib
import hmac
import importlib.util
import json
import pathlib
import unittest
from unittest.mock import patch


ROOT = pathlib.Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("retriever_service", ROOT / "service" / "retriever_service.py")
assert SPEC and SPEC.loader
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


class RetrieverTest(unittest.TestCase):
    def test_signature_uses_method_path_timestamp_and_nonce(self) -> None:
        config = {"RETRIEVER_APP_ID": "app", "RETRIEVER_APP_KEY": "key", "RETRIEVER_APP_SECRET": "secret"}
        with patch.object(MODULE.time, "time", return_value=1700000000.123), patch.object(MODULE.secrets, "token_urlsafe", return_value="nonce"):
            headers = MODULE.signed_headers(config)
        expected = hmac.new(b"secret", b"POST\n/api/V1/open-portal/app/retriever/search/agent_kb\n1700000000123\nnonce", hashlib.sha256).hexdigest()
        assert headers["X-Signature"] == expected

    def test_search_forces_configured_agent_code(self) -> None:
        config = {
            "RETRIEVER_APP_ID": "app", "RETRIEVER_APP_KEY": "key", "RETRIEVER_APP_SECRET": "secret",
            "RETRIEVER_AGENT_CODE": "AI_AGENT_configured", "RETRIEVER_BASE_URL": "https://example.invalid", "RETRIEVER_TIMEOUT_SEC": "2",
        }
        captured = {}
        class Response:
            status = 200
            def read(self):
                return b'{"code":0,"data":[]}'
            def __enter__(self): return self
            def __exit__(self, *args): return False
        def fake_urlopen(request, timeout):
            captured["body"] = json.loads(request.data)
            captured["timeout"] = timeout
            return Response()
        with patch.object(MODULE.urllib.request, "urlopen", fake_urlopen):
            status, payload = MODULE.search(config, {"agent_code": "AI_AGENT_wrong", "query": "测试", "top_k": 3})
        assert status == 200 and payload["ok"]
        assert captured["body"]["agent_code"] == "AI_AGENT_configured"
        assert captured["body"]["top_k"] == 3

    def test_bad_request_is_rejected_before_network(self) -> None:
        config = {"RETRIEVER_AGENT_CODE": "AI_AGENT_x", "RETRIEVER_BASE_URL": "https://example.invalid", "RETRIEVER_TIMEOUT_SEC": "2"}
        status, payload = MODULE.search(config, {"query": "", "top_k": 1})
        assert status == 400 and "query" in payload["message"]


if __name__ == "__main__":
    unittest.main()
