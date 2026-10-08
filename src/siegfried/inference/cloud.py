"""Cloud inference client for Siegfried supporting DeepSeek and OpenAI-compatible providers.

RULES:
1. Strictly Python Standard Library (urllib.request, ssl, json, time).
2. NO global socket.setdefaulttimeout().
3. Individual request timeouts + monotonic deadline enforcement.
4. Mandatory HTTPS for external endpoints with TLS verification active (no CERT_NONE).
5. Responses validated and size-bounded (no memory exhaustion).
6. Secrets never leaked in logs, representations or exceptions.
7. LLM only interprets/recommends; deterministic core validates and executes.
"""

from dataclasses import dataclass
import http.client
import json
import os
from pathlib import Path
import socket
import ssl
import time
from typing import Any, Dict, List, Optional, Protocol, runtime_checkable
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
from siegfried.storage.secrets import get_secret


DEFAULT_DEEPSEEK_ENDPOINT = "https://api.deepseek.com/chat/completions"
DEFAULT_MODEL = "deepseek-chat"
DEFAULT_TIMEOUT_SECONDS = 3.0
DEFAULT_MAX_RESPONSE_BYTES = 10 * 1024 * 1024  # 10 MiB


@runtime_checkable
class InferenceEngine(Protocol):
    """Protocol for inference providers."""

    def generate(self, request: InferenceRequest) -> InferenceResponse:
        ...

    def generate_response(self, messages: List[Dict[str, str]], mode: str = "EJECUTIVO") -> str:
        ...



class SafeRedirectHandler(urllib.request.HTTPRedirectHandler):
    """Restricts HTTP redirects to prevent protocol downgrade or unverified endpoints."""

    def __init__(self, allow_http_localhost: bool = False) -> None:
        super().__init__()
        self.allow_http_localhost = allow_http_localhost

    def redirect_request(
        self,
        req: urllib.request.Request,
        fp: Any,
        code: int,
        msg: str,
        headers: Any,
        newurl: str,
    ) -> urllib.request.Request | None:
        orig_parsed = urllib.parse.urlparse(req.full_url)
        new_parsed = urllib.parse.urlparse(newurl)

        if new_parsed.scheme == "https":
            new_req = super().redirect_request(req, fp, code, msg, headers, newurl)
        elif new_parsed.scheme == "http" and self.allow_http_localhost:
            hostname = (new_parsed.hostname or "").lower()
            if hostname in ("127.0.0.1", "localhost", "::1"):
                new_req = super().redirect_request(req, fp, code, msg, headers, newurl)
            else:
                raise InferenceConfigError(
                    f"Insecure redirect to non-HTTPS scheme '{new_parsed.scheme}' rejected"
                )
        else:
            raise InferenceConfigError(
                f"Insecure redirect to non-HTTPS scheme '{new_parsed.scheme}' rejected"
            )

        # Cross-host credential stripping: never leak Authorization header to a different host
        if new_req is not None and (orig_parsed.netloc.lower() != new_parsed.netloc.lower()):
            for header_name in list(new_req.headers.keys()):
                if header_name.lower() == "authorization":
                    del new_req.headers[header_name]
            if hasattr(new_req, "unredirected_hdrs"):
                for header_name in list(new_req.unredirected_hdrs.keys()):
                    if header_name.lower() == "authorization":
                        del new_req.unredirected_hdrs[header_name]

        return new_req


def validate_endpoint_url(url: str, allow_http_localhost: bool = True) -> urllib.parse.ParseResult:
    """Validate endpoint URL scheme and destination.

    Args:
        url: Target HTTP/HTTPS URL.
        allow_http_localhost: If True, allows unencrypted HTTP solely for loopback/localhost.

    Returns:
        urllib.parse.ParseResult if valid.

    Raises:
        InferenceConfigError: If scheme is invalid or external endpoint is not HTTPS.
    """
    if not isinstance(url, str) or not url.strip():
        raise InferenceConfigError("Endpoint URL must be a non-empty string")

    parsed = urllib.parse.urlparse(url)
    if not parsed.scheme or not parsed.netloc:
        raise InferenceConfigError(f"Malformed endpoint URL: '{url}'")

    if parsed.scheme == "https":
        return parsed

    if parsed.scheme == "http":
        hostname = (parsed.hostname or "").lower()
        if allow_http_localhost and hostname in ("127.0.0.1", "localhost", "::1"):
            return parsed
        raise InferenceConfigError(
            f"External unencrypted HTTP endpoints are forbidden: '{url}'. HTTPS is required."
        )

    raise InferenceConfigError(
        f"Unsupported endpoint scheme '{parsed.scheme}'. Only HTTPS is permitted."
    )


class CloudInferenceClient:
    """Production-grade, secure Cloud inference client for DeepSeek/OpenAI-compatible APIs."""

    def __init__(
        self,
        api_key: Optional[str] = None,
        endpoint_url: str = DEFAULT_DEEPSEEK_ENDPOINT,
        model: str = DEFAULT_MODEL,
        timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS,
        max_response_bytes: int = DEFAULT_MAX_RESPONSE_BYTES,
        ssl_context: Optional[ssl.SSLContext] = None,
        allow_http_localhost: bool = True,
        secrets_file: Optional[Path] = None,
    ) -> None:
        self.endpoint_url = endpoint_url
        self.model = model
        self.timeout_seconds = float(timeout_seconds)
        self.max_response_bytes = int(max_response_bytes)
        self.allow_http_localhost = allow_http_localhost
        self.secrets_file = secrets_file

        # 1. Validate endpoint
        validate_endpoint_url(self.endpoint_url, allow_http_localhost=self.allow_http_localhost)

        # 2. Resolve API key
        resolved_key = api_key
        if not resolved_key:
            resolved_key = get_secret("DEEPSEEK_API_KEY", secrets_file=self.secrets_file)
        if not resolved_key:
            resolved_key = get_secret("SIEGFRIED_LLM_API_KEY", secrets_file=self.secrets_file)
        self._api_key = resolved_key.strip() if resolved_key else ""

        # 3. Configure TLS Context
        if ssl_context is None:
            self._ssl_context = ssl.create_default_context()
            self._ssl_context.verify_mode = ssl.CERT_REQUIRED
            self._ssl_context.check_hostname = True
        else:
            if ssl_context.verify_mode == ssl.CERT_NONE:
                raise InferenceConfigError(
                    "Disabling TLS certificate verification (CERT_NONE) is strictly forbidden for security"
                )
            self._ssl_context = ssl_context

        # 4. Build custom opener with safe redirect handler
        redirect_handler = SafeRedirectHandler(allow_http_localhost=self.allow_http_localhost)
        https_handler = urllib.request.HTTPSHandler(context=self._ssl_context)
        http_handler = urllib.request.HTTPHandler()
        self._opener = urllib.request.build_opener(redirect_handler, https_handler, http_handler)

    def __repr__(self) -> str:
        key_repr = "[SET]" if self._api_key else "[NOT SET]"
        return (
            f"CloudInferenceClient(endpoint='{self.endpoint_url}', "
            f"model='{self.model}', api_key={key_repr}, timeout={self.timeout_seconds}s)"
        )

    def generate(self, request: InferenceRequest) -> InferenceResponse:
        """Execute structured inference request with monotonic deadline enforcement.

        Args:
            request: Validated InferenceRequest object.

        Returns:
            InferenceResponse containing generated content and metrics.

        Raises:
            InferenceAuthError: Missing or invalid API key, 401/403.
            InferenceConfigError: Invalid request configuration.
            InferenceDeadlineExceededError: Monotonic deadline expired before or during call.
            InferenceTimeoutError: Request socket timed out.
            InferenceRateLimitError: HTTP 429 received.
            InferenceHTTPError: 5xx or unhandled HTTP error codes.
            InferenceTransportError: Network, socket, or TLS failures.
            InferenceResponseError: Malformed JSON, non-UTF8, or invalid payload schema.
            InferenceResponseTooLargeError: Response exceeded max_response_bytes.
        """
        # Validate request contract
        try:
            request.validate()
        except ValueError as e:
            raise InferenceConfigError(f"Invalid inference request: {e}") from e

        # Validate credentials
        if not self._api_key:
            raise InferenceAuthError(
                "Missing API key for cloud inference. Configure DEEPSEEK_API_KEY in ~/.siegfried/config/secrets.env"
            )

        # Monotonic deadline budget calculation
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

        # Prepare HTTP payload
        payload_data = request.to_payload()
        try:
            payload_bytes = json.dumps(payload_data, ensure_ascii=False).encode("utf-8")
        except Exception as e:
            raise InferenceConfigError(f"Failed to serialize request payload to JSON: {e}") from e

        headers = {
            "Content-Type": "application/json; charset=utf-8",
            "Authorization": f"Bearer {self._api_key}",
            "User-Agent": "Siegfried-CloudClient/1.0",
            "Accept": "application/json",
        }

        req = urllib.request.Request(
            url=self.endpoint_url,
            data=payload_bytes,
            headers=headers,
            method="POST",
        )

        # Perform network request with controlled timeout and monotonic deadline
        raw_response_bytes = b""
        http_status = 200

        try:
            response = self._opener.open(req, timeout=effective_timeout)
            try:
                http_status = getattr(response, "status", 200)
                raw_response_bytes = self._read_bounded_response(
                    response=response,
                    max_bytes=self.max_response_bytes,
                    deadline=deadline,
                )
            finally:
                response.close()

        except urllib.error.HTTPError as e:
            # Safely extract error code without leaking authorization headers
            code = e.code
            try:
                error_body = e.read(1024).decode("utf-8", errors="replace")
            except Exception:
                error_body = ""

            if code in (401, 403):
                raise InferenceAuthError(
                    f"Authentication failed with provider (HTTP {code})"
                ) from e
            elif code == 429:
                raise InferenceRateLimitError(
                    "Provider rate limit exceeded (HTTP 429)"
                ) from e
            elif 500 <= code <= 599:
                raise InferenceHTTPError(
                    status_code=code,
                    message=f"Provider server error (HTTP {code})"
                ) from e
            else:
                raise InferenceHTTPError(
                    status_code=code,
                    message=f"Unexpected HTTP {code}"
                ) from e

        except (socket.timeout, TimeoutError) as e:
            if deadline is not None and time.monotonic() >= deadline:
                raise InferenceDeadlineExceededError(
                    "Monotonic deadline exceeded during socket communication"
                ) from e
            raise InferenceTimeoutError(
                f"Request socket timed out after {effective_timeout:.2f}s"
            ) from e

        except urllib.error.URLError as e:
            # Check if inner reason is timeout
            if isinstance(e.reason, (socket.timeout, TimeoutError)):
                if deadline is not None and time.monotonic() >= deadline:
                    raise InferenceDeadlineExceededError(
                        "Monotonic deadline exceeded during network transmission"
                    ) from e
                raise InferenceTimeoutError(
                    f"Request socket timed out after {effective_timeout:.2f}s"
                ) from e

            if isinstance(e.reason, ssl.SSLError):
                raise InferenceTransportError(
                    f"TLS/SSL connection failure: {type(e.reason).__name__}"
                ) from e

            raise InferenceTransportError(
                f"Network transport failure: {e.reason}"
            ) from e

        except (http.client.HTTPException, ConnectionError, OSError) as e:
            if deadline is not None and time.monotonic() >= deadline:
                raise InferenceDeadlineExceededError(
                    "Monotonic deadline exceeded during connection"
                ) from e
            raise InferenceTransportError(
                f"Connection transport failure: {type(e).__name__}"
            ) from e

        duration_ms = (time.monotonic() - start_monotonic) * 1000.0

        # Validate response content
        if not raw_response_bytes:
            raise InferenceResponseError("Received empty response body from provider")

        try:
            response_text = raw_response_bytes.decode("utf-8")
        except UnicodeDecodeError as e:
            raise InferenceResponseError(f"Response body is not valid UTF-8: {e}") from e

        try:
            data = json.loads(response_text)
        except json.JSONDecodeError as e:
            raise InferenceResponseError(f"Response is not valid JSON: {e}") from e

        if not isinstance(data, dict):
            raise InferenceResponseError("Response JSON root must be an object")

        # Validate choices and message
        choices = data.get("choices")
        if not isinstance(choices, list) or len(choices) == 0:
            raise InferenceResponseError("Response JSON missing or empty 'choices' list")

        first_choice = choices[0]
        if not isinstance(first_choice, dict):
            raise InferenceResponseError("Response choices[0] must be an object")

        msg_obj = first_choice.get("message")
        if not isinstance(msg_obj, dict):
            raise InferenceResponseError("Response choices[0] missing 'message' object")

        content = msg_obj.get("content")
        if not isinstance(content, str):
            raise InferenceResponseError("Response message content must be a string")

        reported_model = data.get("model")
        if not isinstance(reported_model, str) or not reported_model.strip():
            reported_model = request.model

        finish_reason = first_choice.get("finish_reason")
        if finish_reason is not None and not isinstance(finish_reason, str):
            finish_reason = str(finish_reason)

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
            model=self.model,
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
        """Read response body in chunks enforcing max byte limit and monotonic deadline."""
        chunks: List[bytes] = []
        total_bytes = 0
        chunk_size = 65536  # 64 KiB chunks

        while True:
            if deadline is not None and time.monotonic() > deadline:
                raise InferenceDeadlineExceededError(
                    "Monotonic deadline exceeded while receiving response body"
                )

            chunk = response.read(min(chunk_size, max_bytes - total_bytes + 1))
            if not chunk:
                break

            total_bytes += len(chunk)
            if total_bytes > max_bytes:
                raise InferenceResponseTooLargeError(
                    f"Response size exceeded limit of {max_bytes} bytes"
                )

            chunks.append(chunk)

        return b"".join(chunks)


class CloudInferenceStub:
    """Stub placeholder for testing and deterministic offline fallback."""

    def __init__(self, api_key: str = "", timeout_seconds: float = 3.0) -> None:
        self.api_key = api_key
        self.timeout_seconds = timeout_seconds

    def generate(self, request: InferenceRequest) -> InferenceResponse:
        return InferenceResponse(
            content="[CloudInferenceStub] Entendido, Señor.",
            model=request.model,
            usage=InferenceUsage(prompt_tokens=10, completion_tokens=5, total_tokens=15),
            duration_ms=0.1,
            finish_reason="stop",
        )

    def generate_response(self, messages: List[Dict[str, str]], mode: str = "EJECUTIVO") -> str:
        return f"[CloudInferenceStub] Modo {mode}: Entendido, Señor."
