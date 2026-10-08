"""Comprehensive unit tests for CloudInferenceClient, contracts, and security."""

from http.server import BaseHTTPRequestHandler, HTTPServer
import json
from pathlib import Path
import socket
import ssl
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import MagicMock, patch
import urllib.request


from siegfried.contracts.inference import (
    InferenceMessage,
    InferenceRequest,
    InferenceResponse,
    InferenceUsage,
)
from siegfried.core.errors import (
    InferenceAuthError,
    InferenceConfigError,
    InferenceDeadlineExceededError,
    InferenceHTTPError,
    InferenceRateLimitError,
    InferenceResponseError,
    InferenceResponseTooLargeError,
    InferenceTimeoutError,
    InferenceTransportError,
)
from siegfried.inference.cloud import (
    CloudInferenceClient,
    CloudInferenceStub,
    InferenceEngine,
    SafeRedirectHandler,
    validate_endpoint_url,
)
from siegfried.storage.paths import SiegfriedPaths


class MockHTTPHandler(BaseHTTPRequestHandler):
    """Local test HTTP handler responding with configured payloads."""

    # Configurable class-level response fixtures
    response_code = 200
    response_headers = {"Content-Type": "application/json; charset=utf-8"}
    response_bytes = b""
    delay_seconds = 0.0
    last_request_headers = {}
    last_request_body = b""

    def do_POST(self):
        MockHTTPHandler.last_request_headers = dict(self.headers)
        content_length = int(self.headers.get("Content-Length", 0))
        MockHTTPHandler.last_request_body = self.rfile.read(content_length)

        if self.delay_seconds > 0:
            time.sleep(self.delay_seconds)

        self.send_response(self.response_code)
        for k, v in self.response_headers.items():
            self.send_header(k, v)
        self.send_header("Content-Length", str(len(self.response_bytes)))
        self.end_headers()
        self.wfile.write(self.response_bytes)

    def log_message(self, format, *args):
        # Silence HTTP server logs during tests
        pass


class TestCloudInference(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        # Start ephemeral local HTTP server for mock responses
        cls.server = HTTPServer(("127.0.0.1", 0), MockHTTPHandler)
        cls.port = cls.server.server_port
        cls.endpoint = f"http://127.0.0.1:{cls.port}/v1/chat/completions"
        cls.server_thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.server_thread.start()

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()

    def setUp(self):
        MockHTTPHandler.response_code = 200
        MockHTTPHandler.response_headers = {"Content-Type": "application/json; charset=utf-8"}
        MockHTTPHandler.delay_seconds = 0.0
        MockHTTPHandler.response_bytes = json.dumps({
            "id": "chatcmpl-test-01",
            "model": "deepseek-chat",
            "choices": [
                {
                    "index": 0,
                    "message": {
                        "role": "assistant",
                        "content": "A la orden, Señor. Los sistemas están operativos.",
                    },
                    "finish_reason": "stop"
                }
            ],
            "usage": {
                "prompt_tokens": 15,
                "completion_tokens": 12,
                "total_tokens": 27
            }
        }).encode("utf-8")

        self.api_key = "sk-test-secret-key-abcdef123456"
        self.client = CloudInferenceClient(
            api_key=self.api_key,
            endpoint_url=self.endpoint,
            allow_http_localhost=True,
            timeout_seconds=2.0,
        )

    # 1. Solicitud válida
    def test_01_valid_request(self):
        req = InferenceRequest(
            messages=[InferenceMessage(role="user", content="Reporte de estado")],
            model="deepseek-chat",
        )
        res = self.client.generate(req)
        self.assertIsInstance(res, InferenceResponse)
        self.assertEqual(res.content, "A la orden, Señor. Los sistemas están operativos.")
        self.assertEqual(res.model, "deepseek-chat")

        # Verify headers sent
        auth_header = MockHTTPHandler.last_request_headers.get("Authorization")
        self.assertEqual(auth_header, f"Bearer {self.api_key}")

    # 2. Respuesta válida
    def test_02_valid_response_structure(self):
        req = InferenceRequest(
            messages=[InferenceMessage(role="user", content="Hola")],
            model="deepseek-chat",
        )
        res = self.client.generate(req)
        self.assertEqual(res.finish_reason, "stop")
        self.assertGreater(res.duration_ms, 0.0)
        self.assertEqual(res.raw_status, 200)

    # 3. Normalización de resultado
    def test_03_normalized_usage_metrics(self):
        req = InferenceRequest(
            messages=[InferenceMessage(role="user", content="Hola")],
            model="deepseek-chat",
        )
        res = self.client.generate(req)
        self.assertIsNotNone(res.usage)
        self.assertEqual(res.usage.prompt_tokens, 15)
        self.assertEqual(res.usage.completion_tokens, 12)
        self.assertEqual(res.usage.total_tokens, 27)

    # 4. Credenciales ausentes
    def test_04_missing_credentials_raises_auth_error(self):
        client = CloudInferenceClient(
            api_key="",
            endpoint_url=self.endpoint,
            allow_http_localhost=True,
            secrets_file=Path("/tmp/non_existent_secrets.env"),
        )
        # Clear env fallback if set
        with patch.dict("os.environ", {}, clear=True):
            client._api_key = ""
            req = InferenceRequest(messages=[InferenceMessage(role="user", content="Hola")])
            with self.assertRaises(InferenceAuthError) as ctx:
                client.generate(req)
            self.assertIn("Missing API key", str(ctx.exception))

    # 5. Configuración inválida
    def test_05_invalid_configuration(self):
        # Empty model
        with self.assertRaises(InferenceConfigError):
            req = InferenceRequest(messages=[InferenceMessage(role="user", content="Hola")], model="")
            self.client.generate(req)

        # Negative timeout
        with self.assertRaises(InferenceConfigError):
            req = InferenceRequest(messages=[InferenceMessage(role="user", content="Hola")], timeout_seconds=-5.0)
            self.client.generate(req)

        # Invalid temperature
        with self.assertRaises(InferenceConfigError):
            req = InferenceRequest(messages=[InferenceMessage(role="user", content="Hola")], temperature=3.0)
            self.client.generate(req)

        # Malformed endpoint URL
        with self.assertRaises(InferenceConfigError):
            validate_endpoint_url("not-a-valid-url")

    # 6. Endpoint HTTP externo rechazado
    def test_06_external_unencrypted_http_rejected(self):
        with self.assertRaises(InferenceConfigError) as ctx:
            validate_endpoint_url("http://api.deepseek.com/chat/completions")
        self.assertIn("External unencrypted HTTP endpoints are forbidden", str(ctx.exception))

    # 7. Certificados TLS no deshabilitados
    def test_07_tls_verification_cannot_be_disabled(self):
        insecure_ctx = ssl.create_default_context()
        insecure_ctx.check_hostname = False
        insecure_ctx.verify_mode = ssl.CERT_NONE

        with self.assertRaises(InferenceConfigError) as ctx:
            CloudInferenceClient(
                api_key="sk-test",
                endpoint_url="https://api.deepseek.com/chat/completions",
                ssl_context=insecure_ctx,
            )
        self.assertIn("strictly forbidden", str(ctx.exception))

    # 8. Timeout de conexión
    def test_08_connection_timeout(self):
        MockHTTPHandler.delay_seconds = 0.5
        client = CloudInferenceClient(
            api_key=self.api_key,
            endpoint_url=self.endpoint,
            allow_http_localhost=True,
            timeout_seconds=0.1,  # Strict short timeout
        )
        req = InferenceRequest(
            messages=[InferenceMessage(role="user", content="Hola")],
            timeout_seconds=0.1,
        )
        with self.assertRaises((InferenceTimeoutError, InferenceDeadlineExceededError)):
            client.generate(req)

    # 9. Deadline agotado antes de enviar
    def test_09_deadline_exhausted_before_initiation(self):
        past_deadline = time.monotonic() - 1.0  # Already expired
        req = InferenceRequest(
            messages=[InferenceMessage(role="user", content="Hola")],
            deadline=past_deadline,
        )
        with self.assertRaises(InferenceDeadlineExceededError) as ctx:
            self.client.generate(req)
        self.assertIn("deadline already expired", str(ctx.exception))

    # 10. Error HTTP 401/403
    def test_10_http_401_403_authentication_failure(self):
        for code in [401, 403]:
            MockHTTPHandler.response_code = code
            MockHTTPHandler.response_bytes = b'{"error": {"message": "Invalid API key"}}'
            req = InferenceRequest(messages=[InferenceMessage(role="user", content="Hola")])
            with self.assertRaises(InferenceAuthError) as ctx:
                self.client.generate(req)
            self.assertIn(str(code), str(ctx.exception))

    # 11. Error HTTP 429
    def test_11_http_429_rate_limit(self):
        MockHTTPHandler.response_code = 429
        MockHTTPHandler.response_bytes = b'{"error": {"message": "Rate limit exceeded"}}'
        req = InferenceRequest(messages=[InferenceMessage(role="user", content="Hola")])
        with self.assertRaises(InferenceRateLimitError) as ctx:
            self.client.generate(req)
        self.assertIn("429", str(ctx.exception))

    # 12. Error HTTP 500/503
    def test_12_http_500_503_server_errors(self):
        for code in [500, 503]:
            MockHTTPHandler.response_code = code
            MockHTTPHandler.response_bytes = b'{"error": "Internal server error"}'
            req = InferenceRequest(messages=[InferenceMessage(role="user", content="Hola")])
            with self.assertRaises(InferenceHTTPError) as ctx:
                self.client.generate(req)
            self.assertEqual(ctx.exception.status_code, code)

    # 13. Respuesta JSON malformada
    def test_13_malformed_json_response(self):
        MockHTTPHandler.response_bytes = b"<html><head><title>502 Bad Gateway</title></head><body>Bad Gateway</body></html>"
        req = InferenceRequest(messages=[InferenceMessage(role="user", content="Hola")])
        with self.assertRaises(InferenceResponseError) as ctx:
            self.client.generate(req)
        self.assertIn("not valid JSON", str(ctx.exception))

    # 14. Respuesta UTF-8 inválida
    def test_14_invalid_utf8_response(self):
        MockHTTPHandler.response_bytes = b'{"model": "deepseek", "choices": [{"message": {"content": "\xff\xfe\xfd"}}]}'
        req = InferenceRequest(messages=[InferenceMessage(role="user", content="Hola")])
        with self.assertRaises(InferenceResponseError) as ctx:
            self.client.generate(req)
        self.assertIn("UTF-8", str(ctx.exception))

    # 15. Respuesta demasiado grande
    def test_15_response_too_large_rejected(self):
        # Create client with 1 KiB max response size
        client = CloudInferenceClient(
            api_key=self.api_key,
            endpoint_url=self.endpoint,
            allow_http_localhost=True,
            max_response_bytes=1024,
        )
        # Prepare 10 KiB response
        large_content = "X" * 10240
        MockHTTPHandler.response_bytes = json.dumps({
            "model": "deepseek-chat",
            "choices": [{"message": {"role": "assistant", "content": large_content}}]
        }).encode("utf-8")

        req = InferenceRequest(messages=[InferenceMessage(role="user", content="Hola")])
        with self.assertRaises(InferenceResponseTooLargeError) as ctx:
            client.generate(req)
        self.assertIn("exceeded limit", str(ctx.exception))

    # 16. Campos requeridos ausentes
    def test_16_missing_required_fields(self):
        # Missing choices
        MockHTTPHandler.response_bytes = b'{"model": "deepseek-chat"}'
        req = InferenceRequest(messages=[InferenceMessage(role="user", content="Hola")])
        with self.assertRaises(InferenceResponseError) as ctx:
            self.client.generate(req)
        self.assertIn("choices", str(ctx.exception))

        # Missing message content
        MockHTTPHandler.response_bytes = b'{"model": "deepseek-chat", "choices": [{"index": 0, "message": {}}]}'
        with self.assertRaises(InferenceResponseError) as ctx:
            self.client.generate(req)
        self.assertIn("content", str(ctx.exception))

    # 17. Ausencia de secretos en mensajes de error y representación
    def test_17_secrets_privacy_in_errors_and_repr(self):
        # Client repr does not leak secret
        client_repr = repr(self.client)
        self.assertNotIn(self.api_key, client_repr)
        self.assertIn("[SET]", client_repr)

        # HTTP error does not contain secret
        MockHTTPHandler.response_code = 401
        MockHTTPHandler.response_bytes = b'{"error": "Unauthorized"}'
        req = InferenceRequest(messages=[InferenceMessage(role="user", content="Hola")])
        try:
            self.client.generate(req)
        except InferenceAuthError as e:
            err_str = str(e)
            self.assertNotIn(self.api_key, err_str)

    # 18. Ninguna modificación de agenda o Vault
    def test_18_no_vault_or_agenda_mutation(self):
        temp_dir = tempfile.TemporaryDirectory()
        base = Path(temp_dir.name)
        paths = SiegfriedPaths(base_dir=base, runtime_dir=base / "run")
        paths.ensure_directories()

        vault_file = paths.vault_file
        vault_file.write_text('{"v":1,"ts":1000.0,"type":"pomodoro_started","data":{}}\n', encoding="utf-8")
        agenda_file = paths.active_agenda_file
        agenda_file.write_text('{"v":1,"critical_task":null,"secondary_tasks":[],"backlog":[]}\n', encoding="utf-8")

        vault_before = vault_file.read_text(encoding="utf-8")
        agenda_before = agenda_file.read_text(encoding="utf-8")

        req = InferenceRequest(messages=[InferenceMessage(role="user", content="Hola")])
        self.client.generate(req)

        self.assertEqual(vault_file.read_text(encoding="utf-8"), vault_before)
        self.assertEqual(agenda_file.read_text(encoding="utf-8"), agenda_before)
        temp_dir.cleanup()

    # 19. Ninguna ejecución shell
    def test_19_no_shell_execution(self):
        with patch("subprocess.Popen") as mock_popen, patch("subprocess.run") as mock_run:
            req = InferenceRequest(messages=[InferenceMessage(role="user", content="Hola")])
            self.client.generate(req)
            mock_popen.assert_not_called()
            mock_run.assert_not_called()

    # 20. Compatibilidad con contratos del repositorio
    def test_20_protocol_and_stub_compatibility(self):
        # Verify CloudInferenceClient implements InferenceEngine protocol
        self.assertTrue(isinstance(self.client, InferenceEngine))

        # Test high-level generate_response
        res_text = self.client.generate_response([{"role": "user", "content": "Hola"}], mode="EJECUTIVO")
        self.assertIn("A la orden", res_text)

        # Test CloudInferenceStub
        stub = CloudInferenceStub()
        self.assertTrue(isinstance(stub, InferenceEngine))
        stub_res = stub.generate(InferenceRequest(messages=[InferenceMessage(role="user", content="Test")]))
        self.assertIn("CloudInferenceStub", stub_res.content)
        self.assertEqual(stub.generate_response([], mode="EJECUTIVO"), "[CloudInferenceStub] Modo EJECUTIVO: Entendido, Señor.")

    # 21. Redirecciones inseguras rechazadas
    def test_21_insecure_redirect_rejected(self):
        handler = SafeRedirectHandler(allow_http_localhost=False)
        req = urllib.request.Request("https://api.deepseek.com/chat/completions")
        with self.assertRaises(InferenceConfigError):
            handler.redirect_request(req, None, 302, "Found", {}, "http://evil.com/redirect")


if __name__ == "__main__":
    unittest.main()
