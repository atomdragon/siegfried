"""Domain exception classes for Siegfried."""


class SiegfriedError(Exception):
    """Base exception for all Siegfried domain errors."""
    pass


class InvalidStateTransitionError(SiegfriedError):
    """Raised when an illegal transition is attempted in the state machine."""
    def __init__(self, from_state: str, to_state: str, reason: str = ""):
        self.from_state = from_state
        self.to_state = to_state
        self.reason = reason
        msg = f"Invalid state transition from '{from_state}' to '{to_state}'"
        if reason:
            msg += f": {reason}"
        super().__init__(msg)


class PostureLimitReachedError(SiegfriedError):
    """Raised when posture hard limit of 60 minutes is reached and cannot be postponed."""
    pass


class StorageError(SiegfriedError):
    """Raised when atomic persistence or locking encounters an unrecoverable failure."""
    pass


class RuntimeNotInitializedError(StorageError):
    """Raised when runtime directory structure is missing or incomplete."""
    pass


class InvalidConfigError(StorageError):
    """Raised when configuration documents fail schema or integrity validation."""
    pass


class InsecurePermissionsError(StorageError):
    """Raised when runtime files or directories have unsafe POSIX permissions."""
    pass


class UnsafePathError(StorageError):
    """Raised when an insecure symlink or non-regular special file is detected."""
    pass


class IPCCommunicationError(SiegfriedError):
    """Raised on socket framing or communication failure."""
    pass


class InferenceError(SiegfriedError):
    """Base exception for all inference subsystem errors."""
    pass


class InferenceConfigError(InferenceError):
    """Raised when inference configuration, parameters, or endpoint URLs are invalid."""
    pass


class InferenceSecurityError(InferenceConfigError):
    """Raised when an insecure transport, public binding, or certificate disablement is attempted."""
    pass


class InferenceAuthError(InferenceError):

    """Raised when authentication credentials are missing, invalid, or rejected (401/403)."""
    pass


class InferenceTimeoutError(InferenceError):
    """Raised when an individual request socket timeout or monotonic deadline is exceeded."""
    pass


class InferenceDeadlineExceededError(InferenceTimeoutError):
    """Raised when the cumulative monotonic deadline expires before or during request execution."""
    pass


class InferenceTransportError(InferenceError):
    """Raised on network transport, DNS resolution, socket reset, or TLS handshake errors."""
    pass


class InferenceRateLimitError(InferenceError):
    """Raised when the provider returns HTTP 429 Too Many Requests."""
    pass


class InferenceHTTPError(InferenceError):
    """Raised when the provider returns an unexpected or server HTTP error (4xx/5xx)."""

    def __init__(self, status_code: int, message: str = "") -> None:
        self.status_code = status_code
        self.message = message
        err_msg = f"HTTP {status_code}"
        if message:
            err_msg += f": {message}"
        super().__init__(err_msg)


class InferenceResponseError(InferenceError):
    """Raised when the provider response is malformed, not valid UTF-8, or missing required keys."""
    pass


class InferenceResponseTooLargeError(InferenceResponseError):
    """Raised when the provider response body exceeds the maximum permitted byte size."""
    pass


class LocalInferenceError(InferenceError):
    """Base exception for local inference and llama-server supervisor failures."""
    pass


class LocalInferenceUnavailableError(LocalInferenceError):
    """Raised when local inference cannot run due to missing prerequisites."""
    pass


class LlamaBinaryNotFoundError(LocalInferenceUnavailableError):
    """Raised when the llama-server executable binary is not found or not executable."""
    pass


class LlamaModelNotFoundError(LocalInferenceUnavailableError):
    """Raised when the local GGUF model file is missing, unreadable, or invalid."""
    pass


class InsufficientResourcesError(LocalInferenceError):
    """Raised when available system RAM or VRAM is insufficient for model requirements."""
    pass


class LlamaStartupError(LocalInferenceError):
    """Raised when llama-server process fails to start or pass health checks."""
    pass


class LlamaHealthCheckError(LlamaStartupError):
    """Raised when llama-server fails to respond successfully to health checks within deadline."""
    pass


class LlamaProcessTerminatedError(LlamaStartupError):
    """Raised when llama-server process exits prematurely during startup or operation."""

    def __init__(self, exit_code: int | None = None, message: str = "") -> None:
        self.exit_code = exit_code
        msg = f"llama-server terminated unexpectedly (exit code {exit_code})"
        if message:
            msg += f": {message}"
        super().__init__(msg)


class PortInUseError(LocalInferenceError):
    """Raised when the configured local port is already bound by an external process."""
    pass


class InferenceConcurrencyExceededError(LocalInferenceError):
    """Raised when concurrent local inference requests exceed the supported concurrency limit."""
    pass


class LlamaShutdownError(LocalInferenceError):
    """Raised when llama-server process cannot be cleanly terminated."""
    pass


class OrchestrationError(InferenceError):
    """Base exception for inference orchestration failures."""
    pass


class NoAvailableEngineError(OrchestrationError):
    """Raised when no configured or operational inference engine is available to fulfill the request."""
    pass


class PrivacyViolationError(InferenceSecurityError, OrchestrationError):
    """Raised when an operation would violate privacy policy constraints (e.g. routing private data externally)."""
    pass


class InferenceBusyError(InferenceError):
    """Raised when cognitive inference capacity is saturated and backpressure is applied."""
    pass


