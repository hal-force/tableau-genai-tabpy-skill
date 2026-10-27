"""Local mock of every LLM wire format the generated calc speaks.

Validates auth headers the way the real service would and records the last
request body so tests can assert on prompt assembly. Loopback only.
"""
from __future__ import annotations

import json
import re
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

TEST_KEY = "test-key-0123456789"
TEST_EMAIL = "analyst@example.mil"
ASKSAGE_ACCESS = "access-token-xyz"

LAST = {}


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *a):  # quiet
        pass

    def _send(self, code, obj):
        raw = json.dumps(obj).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def do_POST(self):
        n = int(self.headers.get("Content-Length") or 0)
        body = json.loads(self.rfile.read(n) or b"{}")
        h = {k.lower(): v for k, v in self.headers.items()}
        path = self.path
        LAST.clear()
        LAST.update(path=path, headers=h, body=body)
        bearer = h.get("authorization") == "Bearer " + TEST_KEY

        if path.endswith("/user/get-token-with-api-key"):
            if body.get("email") == TEST_EMAIL and body.get("api_key") == TEST_KEY:
                return self._send(200, {"response": {"access_token": ASKSAGE_ACCESS}, "status": 200})
            return self._send(401, {"message": "bad credentials"})
        if path.endswith("/server/query"):
            if h.get("x-access-tokens") != ASKSAGE_ACCESS:
                return self._send(401, {"message": "bad token"})
            return self._send(200, {"message": "MOCK-ASKSAGE-NATIVE model=%s" % body.get("model"),
                                    "response": "OK", "status": 200})
        if path.endswith("/chat/completions"):
            if not (bearer or h.get("api-key") == TEST_KEY):
                return self._send(401, {"error": {"message": "unauthorized"}})
            return self._send(200, {"choices": [{"message": {
                "role": "assistant",
                "content": "MOCK-OPENAI model=%s path=%s" % (body.get("model"), path)}}]})
        if path.endswith("/v1/messages"):
            if not (bearer or h.get("x-api-key") == TEST_KEY):
                return self._send(401, {"type": "error", "error": {"message": "unauthorized"}})
            if h.get("anthropic-version") != "2023-06-01" or not body.get("max_tokens"):
                return self._send(400, {"error": {"message": "missing version/max_tokens"}})
            return self._send(200, {"content": [{"type": "text", "text": "MOCK-ANTHROPIC model=%s path=%s" % (body.get("model"), path)}],
                                    "stop_reason": "end_turn"})
        m = re.search(r"/model/([^/]+)/converse$", path)
        if m:
            auth = h.get("authorization", "")
            if not (bearer or auth.startswith("AWS4-HMAC-SHA256")):
                return self._send(403, {"message": "forbidden"})
            mode = "sigv4" if auth.startswith("AWS4") else "bearer"
            return self._send(200, {"output": {"message": {"role": "assistant", "content": [
                {"text": "MOCK-BEDROCK-%s model=%s" % (mode, m.group(1))}]}}, "stopReason": "end_turn"})
        m = re.search(r"/publishers/google/models/([^/:]+):generateContent$", path)
        if m:
            if h.get("x-goog-api-key") != TEST_KEY:
                return self._send(403, {"error": {"message": "API key not valid"}})
            return self._send(200, {"candidates": [{"content": {"parts": [
                {"text": "MOCK-GEMINI model=%s" % m.group(1)}]}, "finishReason": "STOP"}]})
        return self._send(404, {"error": "no route " + path})


def start(port: int = 0):
    srv = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    t = threading.Thread(target=srv.serve_forever, daemon=True)
    t.start()
    return srv, "http://127.0.0.1:%d" % srv.server_address[1]
