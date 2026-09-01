"""HTTP trust-boundary tests for the internal processing API.

These tests drive the real ASGI application object directly, without sockets or an
extra HTTP client dependency, so router wiring, the shared-credential guard, and the
absence of browser CORS grants are proven deterministically. Denied requests must be
rejected by the guard before any database or broker work is attempted.
"""

import asyncio
import json
import unittest
from unittest.mock import patch

from app.bootstrap.api import create_api_app
from app.config.settings import settings

TOKEN = "unit-test-internal-token"
TRANSCRIPT_PATH = "/videos/1/transcript"
ARTIFACT_PATH = "/internal/processing-requests/not-a-uuid/transcript-rows"
ASSISTANT_PATH = "/internal/assistant/answer"


def perform_request(app, method, path, headers=None, body=b""):
    header_items = [
        (name.lower().encode("latin-1"), value.encode("latin-1"))
        for name, value in (headers or {}).items()
    ]
    if body:
        header_items.append((b"content-type", b"application/json"))
        header_items.append((b"content-length", str(len(body)).encode("latin-1")))
    scope = {
        "type": "http",
        "asgi": {"version": "3.0", "spec_version": "2.3"},
        "http_version": "1.1",
        "method": method,
        "scheme": "http",
        "path": path,
        "raw_path": path.encode("latin-1"),
        "query_string": b"",
        "root_path": "",
        "headers": header_items,
        "client": ("testclient", 50000),
        "server": ("testserver", 80),
    }

    request_sent = False

    async def receive():
        nonlocal request_sent
        if request_sent:
            return {"type": "http.disconnect"}
        request_sent = True
        return {"type": "http.request", "body": body, "more_body": False}

    messages = []

    async def send(message):
        messages.append(message)

    asyncio.run(app(scope, receive, send))

    start = next(message for message in messages if message["type"] == "http.response.start")
    response_headers = {
        name.decode("latin-1").lower(): value.decode("latin-1")
        for name, value in start["headers"]
    }
    response_body = b"".join(
        message.get("body", b"")
        for message in messages
        if message["type"] == "http.response.body"
    )
    return start["status"], response_headers, response_body


class InternalApiAuthEnforcementTest(unittest.TestCase):
    def setUp(self):
        self.app = create_api_app()
        for target, value in (
            ("INTERNAL_API_AUTH_ENABLED", True),
            ("INTERNAL_API_TOKEN", TOKEN),
            ("ASSISTANT_LLM_ENABLED", False),
        ):
            patcher = patch.object(settings, target, value)
            patcher.start()
            self.addCleanup(patcher.stop)

    def test_transcript_reads_without_a_credential_are_denied(self):
        for path in (TRANSCRIPT_PATH, ARTIFACT_PATH):
            status, headers, body = perform_request(self.app, "GET", path)
            self.assertEqual(status, 401, path)
            self.assertEqual(headers.get("www-authenticate"), "Bearer", path)
            self.assertIn("credential", body.decode("utf-8"))

    def test_processing_invocations_without_a_credential_are_denied(self):
        for method, path in (("POST", "/videos/upload"), ("POST", ASSISTANT_PATH)):
            status, _headers, _body = perform_request(self.app, method, path)
            self.assertEqual(status, 401, path)

    def test_wrong_or_non_bearer_credentials_are_denied(self):
        status, _headers, _body = perform_request(
            self.app, "GET", TRANSCRIPT_PATH, headers={"Authorization": "Bearer wrong-token"}
        )
        self.assertEqual(status, 403)

        status, _headers, _body = perform_request(
            self.app, "GET", TRANSCRIPT_PATH, headers={"Authorization": "Basic d3Jvbmc="}
        )
        self.assertEqual(status, 401)

    def test_correct_credential_reaches_the_handlers(self):
        credential = {"Authorization": "Bearer " + TOKEN}

        status, _headers, body = perform_request(self.app, "GET", ARTIFACT_PATH, headers=credential)
        self.assertEqual(status, 400)
        self.assertIn("Invalid processing request id", body.decode("utf-8"))

        status, _headers, _body = perform_request(
            self.app,
            "POST",
            ASSISTANT_PATH,
            headers=credential,
            body=json.dumps({"question": "hello"}).encode("utf-8"),
        )
        self.assertEqual(status, 503)

    def test_health_and_root_stay_public(self):
        status, _headers, body = perform_request(self.app, "GET", "/health")
        self.assertEqual(status, 200)
        self.assertEqual(json.loads(body), {"status": "healthy"})

        status, _headers, _body = perform_request(self.app, "GET", "/")
        self.assertEqual(status, 200)

    def test_enabled_protection_without_a_token_fails_closed(self):
        with patch.object(settings, "INTERNAL_API_TOKEN", ""):
            with self.assertRaises(RuntimeError) as ctx:
                create_api_app()
            self.assertIn("INTERNAL_API_TOKEN", str(ctx.exception))

            status, _headers, _body = perform_request(self.app, "GET", TRANSCRIPT_PATH)
            self.assertEqual(status, 401)

    def test_credential_never_appears_in_responses_or_guard_logs(self):
        with self.assertLogs("app.core.internal_auth", level="INFO") as captured:
            for headers in (None, {"Authorization": "Bearer wrong-token"}):
                _status, response_headers, body = perform_request(
                    self.app, "GET", TRANSCRIPT_PATH, headers=headers
                )
                self.assertNotIn(TOKEN, body.decode("utf-8"))
                self.assertNotIn(TOKEN.encode("utf-8"), json.dumps(response_headers).encode("utf-8"))
        self.assertNotIn(TOKEN, "\n".join(captured.output))


class BrowserCorsRemovalTest(unittest.TestCase):
    def setUp(self):
        self.app = create_api_app()

    def test_browser_origin_receives_no_cors_grant(self):
        status, headers, _body = perform_request(
            self.app, "GET", "/health", headers={"Origin": "http://attacker.example"}
        )
        self.assertEqual(status, 200)
        self.assertNotIn("access-control-allow-origin", headers)
        self.assertNotIn("access-control-allow-credentials", headers)

    def test_preflight_receives_no_cors_grant(self):
        status, headers, _body = perform_request(
            self.app,
            "OPTIONS",
            TRANSCRIPT_PATH,
            headers={
                "Origin": "http://attacker.example",
                "Access-Control-Request-Method": "GET",
            },
        )
        self.assertEqual(status, 405)
        self.assertFalse(
            [name for name in headers if name.startswith("access-control-")],
            headers,
        )


class StandaloneCompatibilityTest(unittest.TestCase):
    def test_disabled_protection_keeps_the_standalone_surface_open(self):
        with patch.object(settings, "INTERNAL_API_AUTH_ENABLED", False):
            app = create_api_app()
            status, _headers, body = perform_request(app, "GET", ARTIFACT_PATH)
        self.assertEqual(status, 400)
        self.assertIn("Invalid processing request id", body.decode("utf-8"))


if __name__ == "__main__":
    unittest.main()
