"""Local inference client for llama-server HTTP interface.

RULES:
1. Python Standard Library only (urllib.request, json, time).
2. Loopback-only binding (127.0.0.1).
3. Monotonic deadline and granular timeouts.
4. Bounded stream reader against memory exhaustion.
5. Strict output validation. LLM only recommends; core determinism executes.
"""

import http.client
import json
import socket
import time
from typing import Any, Dict, List, Optional
import urllib.error
import urllib.parse
import urllib.request

from siegfried.contracts.inference import (
    InferenceMessage,
    InferenceRequest,
    InferenceResponse,
    InferenceUsage,
)
from siegfried.core.errors import (
    InferenceConfigError,
    InferenceDeadlineExceededError,
    InferenceHTTPError,
    InferenceRateLimitError,
    InferenceResponseError,
    InferenceResponseTooLargeError,
    InferenceSecurityError,
    InferenceTimeoutError,
    InferenceTransportError,
    LocalInferenceUnavailableError,
)
from siegfried.inference.cloud import InferenceEngine
from siegfried.inference.llama_manager import LlamaLifecycleManager, LlamaServerState


DEFAULT_LOCAL_ENDPOINT = "http://127.0.0.1:8080"
DEFAULT_LOCAL_TIMEOUT_SECONDS = 10.0
DEFAULT_MAX_RESPONSE_BYTES = 10 * 1024 * 1024  # 10 MiB


class LocalInferenceClient:
    """Client for local llama.cpp / llama-server HTTP API."""

    def __init__(
        self,
        manager: Optional[LlamaLifecycleManager] = None,
        endpoint_url: str = DEFAULT_LOCAL_ENDPOINT,
        timeout_seconds: float = DEFAULT_LOCAL_TIMEOUT_SECONDS,
        max_response_bytes: int = DEFAULT_MAX_RESPONSE_BYTES,
    ) -> None:
        self.manager = manager
        self.endpoint_url = endpoint_url.rstrip("/")
        self.timeout_seconds = float(timeout_seconds)
        self.max_response_bytes = int(max_response_bytes)

        # Validate loopback restriction
        parsed = urllib.parse.urlparse(self.endpoint_url)
        if parsed.scheme != "http":
            raise InferenceConfigError(
                f"Invalid scheme '{parsed.scheme}' for local inference. Expected 'http'."
            )
        hostname = (parsed.hostname or "").lower()
        if hostname not in ("127.0.0.1", "localhost", "::1"):
            raise InferenceSecurityError(
                f"Local inference endpoint must be loopback only. Rejected: '{self.endpoint_url}'"
            )

    def generate(self, request: InferenceRequest) -> InferenceResponse:
        """Execute inference request against local llama-server."""
        # 1. Validate request contract
        try:
            request.validate()
        except ValueError as e:
            raise InferenceConfigError(f"Invalid local inference request: {e}") from e

        # 2. Monotonic deadline check
        start_monotonic = time.monotonic()
        deadline = request.deadline
        if deadline is not None:
            remaining_before = deadline - start_monotonic
            if remaining_before <= 0:
                raise InferenceDeadlineExceededError(
                    f"Inference deadline already expired before initiation ({remaining_before:.3f}s remaining)"
                )
            effective_timeout = min(request.timeout_seconds, remaining_before)
        else:
            effective_timeout = request.timeout_seconds

        if effective_timeout <= 0:
            raise InferenceDeadlineExceededError("Inference timeout budget is zero or negative")

        # 3. Coordinate with lifecycle manager if attached
        if self.manager is not None:
            with self.manager.active_inference(deadline=deadline):
                return self._perform_http_request(request, effective_timeout, deadline, start_monotonic)
        else:
            return self._perform_http_request(request, effective_timeout, deadline, start_monotonic)

    def _perform_http_request(
        self,
        request: InferenceRequest,
        effective_timeout: float,
        deadline: Optional[float],
        start_monotonic: float,
    ) -> InferenceResponse:
        """Send HTTP POST payload to llama-server and parse response."""
        # Determine target endpoint URL
        target_url = self.endpoint_url
        if not target_url.endswith("/v1/chat/completions") and not target_url.endswith("/completion"):
            target_url = f"{self.endpoint_url}/v1/chat/completions"

        payload_data = request.to_payload()
        try:
            payload_bytes = json.dumps(payload_data, ensure_ascii=False).encode("utf-8")
        except Exception as e:
            raise InferenceConfigError(f"Failed to serialize request payload to JSON: {e}") from e

        headers = {
            "Content-Type": "application/json; charset=utf-8",
            "User-Agent": "Siegfried-LocalClient/1.0",
            "Accept": "application/json",
        }

        req = urllib.request.Request(
            url=target_url,
            data=payload_bytes,
            headers=headers,
            method="POST",
        )

        raw_response_bytes = b""
        http_status = 200

        try:
            with urllib.request.urlopen(req, timeout=effective_timeout) as response:
                http_status = getattr(response, "status", 200)
                raw_response_bytes = self._read_bounded_response(
                    response=response,
                    max_bytes=self.max_response_bytes,
                    deadline=deadline,
                )
        except urllib.error.HTTPError as e:
            code = e.code
            if code == 429:
                raise InferenceRateLimitError(f"Local server busy/rate limited (HTTP {code})") from e
            elif 500 <= code <= 599:
                raise InferenceHTTPError(status_code=code, message=f"Local server error (HTTP {code})") from e
            else:
                raise InferenceHTTPError(status_code=code, message=f"Unexpected local HTTP {code}") from e
        except (socket.timeout, TimeoutError) as e:
            if deadline is not None and time.monotonic() >= deadline:
                raise InferenceDeadlineExceededError("Monotonic deadline exceeded during local communication") from e
            raise InferenceTimeoutError(f"Local request socket timed out after {effective_timeout:.2f}s") from e
        except urllib.error.URLError as e:
            if isinstance(e.reason, (socket.timeout, TimeoutError)):
                if deadline is not None and time.monotonic() >= deadline:
                    raise InferenceDeadlineExceededError("Monotonic deadline exceeded during local transmission") from e
                raise InferenceTimeoutError(f"Local request socket timed out after {effective_timeout:.2f}s") from e
            raise InferenceTransportError(f"Local network transport failure: {e.reason}") from e
        except (http.client.HTTPException, ConnectionError, OSError) as e:
            if deadline is not None and time.monotonic() >= deadline:
                raise InferenceDeadlineExceededError("Monotonic deadline exceeded during local connection") from e
            raise InferenceTransportError(f"Local connection transport failure: {type(e).__name__}") from e

        duration_ms = (time.monotonic() - start_monotonic) * 1000.0

        if not raw_response_bytes:
            raise InferenceResponseError("Received empty response body from local llama-server")

        try:
            response_text = raw_response_bytes.decode("utf-8")
        except UnicodeDecodeError as e:
            raise InferenceResponseError(f"Local response body is not valid UTF-8: {e}") from e

        try:
            data = json.loads(response_text)
        except json.JSONDecodeError as e:
            raise InferenceResponseError(f"Local response is not valid JSON: {e}") from e

        if not isinstance(data, dict):
            raise InferenceResponseError("Local response JSON root must be an object")

        # Parse OpenAI-compatible chat completion response format
        content = ""
        finish_reason = None
        if "choices" in data and isinstance(data["choices"], list) and len(data["choices"]) > 0:
            choice = data["choices"][0]
            if isinstance(choice, dict):
                msg = choice.get("message", {})
                if isinstance(msg, dict) and "content" in msg:
                    content = str(msg["content"])
                elif "text" in choice:
                    content = str(choice["text"])
                finish_reason = choice.get("finish_reason")
        elif "content" in data and isinstance(data["content"], str):
            # Fallback for plain llama.cpp completion format
            content = data["content"]
        else:
            raise InferenceResponseError("Local response JSON missing expected choices or content")

        reported_model = str(data.get("model", request.model))
        usage = InferenceUsage.from_dict(data.get("usage"))

        return InferenceResponse(
            content=content,
            model=reported_model,
            usage=usage,
            duration_ms=round(duration_ms, 3),
            finish_reason=finish_reason,
            raw_status=http_status,
        )

    def generate_response(self, messages: List[Dict[str, str]], mode: str = "EJECUTIVO") -> str:
        """High-level helper conforming to the InferenceEngine protocol."""
        parsed_msgs: List[InferenceMessage] = []
        for m in messages:
            if isinstance(m, dict):
                parsed_msgs.append(InferenceMessage.from_dict(m))
            elif isinstance(m, InferenceMessage):
                parsed_msgs.append(m)
            else:
                raise InferenceConfigError(f"Invalid message format: {m}")

        req = InferenceRequest(
            messages=parsed_msgs,
            model="local-model",
            timeout_seconds=self.timeout_seconds,
        )
        res = self.generate(req)
        return res.content

    def _read_bounded_response(
        self,
        response: Any,
        max_bytes: int,
        deadline: Optional[float] = None,
    ) -> bytes:
        chunks: List[bytes] = []
        total_bytes = 0
        chunk_size = 65536

        while True:
            if deadline is not None and time.monotonic() > deadline:
                raise InferenceDeadlineExceededError(
                    "Monotonic deadline exceeded while receiving local response body"
                )

            chunk = response.read(min(chunk_size, max_bytes - total_bytes + 1))
            if not chunk:
                break

            total_bytes += len(chunk)
            if total_bytes > max_bytes:
                raise InferenceResponseTooLargeError(
                    f"Local response size exceeded limit of {max_bytes} bytes"
                )

            chunks.append(chunk)

        return b"".join(chunks)


class LocalInferenceStub:
    """Deterministic stub placeholder for local inference during testing/development."""

    def __init__(self, endpoint_url: str = "http://127.0.0.1:8080") -> None:
        self.endpoint_url = endpoint_url

    def generate(self, request: InferenceRequest) -> InferenceResponse:
        return InferenceResponse(
            content="[LocalInferenceStub] Entendido, Señor. Respuesta generada localmente.",
            model="local-stub",
            usage=InferenceUsage(prompt_tokens=8, completion_tokens=8, total_tokens=16),
            duration_ms=0.1,
            finish_reason="stop",
        )

    def generate_response(self, messages: List[Dict[str, str]], mode: str = "EJECUTIVO") -> str:
        return f"[LocalInferenceStub] Modo {mode}: Entendido, Señor. Respuesta local."
