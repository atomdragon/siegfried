"""Inference package exposing cloud, local and lifecycle managers."""

from siegfried.inference.cloud import (
    InferenceEngine,
    CloudInferenceClient,
    CloudInferenceStub,
    SafeRedirectHandler,
    validate_endpoint_url,
)
from siegfried.inference.local import LocalInferenceClient, LocalInferenceStub
from siegfried.inference.llama_manager import LlamaLifecycleManager, LlamaServerState
from siegfried.inference.resources import (
    ResourceBudget,
    ResourceDetector,
    SystemResources,
    DEFAULT_IDLE_TIMEOUT_SECONDS,
    DEFAULT_MAX_GPU_BUDGET_BYTES,
    DEFAULT_MIN_OS_RAM_RESERVATION_BYTES,
)

from siegfried.inference.orchestrator import (
    InferenceOrchestrator,
    DEFAULT_GLOBAL_DEADLINE_SECONDS,
    DEFAULT_MIN_FALLBACK_MARGIN_SECONDS,
    DEFAULT_ESTIMATED_LOCAL_STARTUP_SECONDS,
)

__all__ = [
    "InferenceEngine",
    "CloudInferenceClient",
    "CloudInferenceStub",
    "SafeRedirectHandler",
    "validate_endpoint_url",
    "LocalInferenceClient",
    "LocalInferenceStub",
    "LlamaLifecycleManager",
    "LlamaServerState",
    "ResourceBudget",
    "ResourceDetector",
    "SystemResources",
    "DEFAULT_IDLE_TIMEOUT_SECONDS",
    "DEFAULT_MAX_GPU_BUDGET_BYTES",
    "DEFAULT_MIN_OS_RAM_RESERVATION_BYTES",
    "InferenceOrchestrator",
    "DEFAULT_GLOBAL_DEADLINE_SECONDS",
    "DEFAULT_MIN_FALLBACK_MARGIN_SECONDS",
    "DEFAULT_ESTIMATED_LOCAL_STARTUP_SECONDS",
]


