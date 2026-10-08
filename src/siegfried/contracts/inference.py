"""Inference contract definitions, schemas, and validations for Siegfried."""

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, List, Optional


INFERENCE_CONTRACT_VERSION: int = 1

VALID_ROLES = frozenset({"system", "user", "assistant"})


@dataclass(frozen=True)
class InferenceMessage:
    """Immutable representation of a chat completion message."""
    role: str
    content: str

    def __post_init__(self) -> None:
        if not isinstance(self.role, str) or not self.role.strip():
            raise ValueError("InferenceMessage role must be a non-empty string")
        if not isinstance(self.content, str):
            raise ValueError("InferenceMessage content must be a string")

    def to_dict(self) -> Dict[str, str]:
        return {"role": self.role, "content": self.content}

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "InferenceMessage":
        if not isinstance(data, dict):
            raise ValueError("Message data must be a dictionary")
        role = data.get("role")
        content = data.get("content")
        if not isinstance(role, str) or not isinstance(content, str):
            raise ValueError("Message must contain 'role' and 'content' string fields")
        return cls(role=role, content=content)


@dataclass(frozen=True)
class InferenceUsage:
    """Token usage metrics reported by inference providers."""
    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0

    def to_dict(self) -> Dict[str, int]:
        return {
            "prompt_tokens": self.prompt_tokens,
            "completion_tokens": self.completion_tokens,
            "total_tokens": self.total_tokens,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any] | None) -> "InferenceUsage":
        if not data or not isinstance(data, dict):
            return cls()
        return cls(
            prompt_tokens=int(data.get("prompt_tokens", 0) or 0),
            completion_tokens=int(data.get("completion_tokens", 0) or 0),
            total_tokens=int(data.get("total_tokens", 0) or 0),
        )


@dataclass(frozen=True)
class InferenceRequest:
    """Normalized structured request for cloud or local inference."""
    messages: List[InferenceMessage]
    model: str = "deepseek-chat"
    max_tokens: Optional[int] = None
    temperature: Optional[float] = None
    timeout_seconds: float = 3.0
    deadline: Optional[float] = None
    extra_params: Dict[str, Any] = field(default_factory=dict)

    def validate(self) -> None:
        """Validate request invariants."""
        if not isinstance(self.messages, list) or len(self.messages) == 0:
            raise ValueError("InferenceRequest messages must be a non-empty list")
        for idx, msg in enumerate(self.messages):
            if not isinstance(msg, InferenceMessage):
                raise ValueError(f"Message at index {idx} must be an instance of InferenceMessage")
        if not isinstance(self.model, str) or not self.model.strip():
            raise ValueError("InferenceRequest model must be a non-empty string")
        if not isinstance(self.timeout_seconds, (int, float)) or self.timeout_seconds <= 0:
            raise ValueError("timeout_seconds must be a positive number")
        if self.max_tokens is not None:
            if not isinstance(self.max_tokens, int) or self.max_tokens <= 0:
                raise ValueError("max_tokens must be a positive integer")
        if self.temperature is not None:
            if not isinstance(self.temperature, (int, float)) or not (0.0 <= self.temperature <= 2.0):
                raise ValueError("temperature must be a float between 0.0 and 2.0")
        if self.deadline is not None:
            if not isinstance(self.deadline, (int, float)) or self.deadline <= 0:
                raise ValueError("deadline must be a positive monotonic timestamp")

    def to_payload(self) -> Dict[str, Any]:
        """Convert to OpenAI / DeepSeek compatible JSON dictionary payload."""
        payload: Dict[str, Any] = {
            "model": self.model,
            "messages": [m.to_dict() for m in self.messages],
        }
        if self.max_tokens is not None:
            payload["max_tokens"] = self.max_tokens
        if self.temperature is not None:
            payload["temperature"] = self.temperature
        if self.extra_params:
            for k, v in self.extra_params.items():
                if k not in payload:
                    payload[k] = v
        return payload


@dataclass(frozen=True)
class InferenceResponse:
    """Normalized structured response from inference providers."""
    content: str
    model: str
    usage: Optional[InferenceUsage] = None
    duration_ms: float = 0.0
    finish_reason: Optional[str] = None
    raw_status: int = 200

    def to_dict(self) -> Dict[str, Any]:
        return {
            "content": self.content,
            "model": self.model,
            "usage": self.usage.to_dict() if self.usage else None,
            "duration_ms": self.duration_ms,
            "finish_reason": self.finish_reason,
            "raw_status": self.raw_status,
        }


class InferencePolicy(str, Enum):
    """Inference engine selection and fallback policy."""
    CLOUD_PREFERRED = "CLOUD_PREFERRED"
    LOCAL_PREFERRED = "LOCAL_PREFERRED"
    LOCAL_ONLY = "LOCAL_ONLY"
    CLOUD_ONLY = "CLOUD_ONLY"


class InferenceRoute(str, Enum):
    """Target inference engine route."""
    CLOUD = "CLOUD"
    LOCAL = "LOCAL"


@dataclass(frozen=True)
class OrchestrationResult:
    """Normalized result returned by the hybrid inference orchestrator."""
    response: InferenceResponse
    route_selected: InferenceRoute
    route_used: InferenceRoute
    fallback_used: bool = False
    fallback_reason: Optional[str] = None
    attempts: int = 1
    elapsed_ms: float = 0.0
    remaining_deadline_ms: float = 0.0
    request_id: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        """Convert to safe metadata dictionary without leaking private content or secrets."""
        return {
            "route_selected": self.route_selected.value if isinstance(self.route_selected, Enum) else str(self.route_selected),
            "route_used": self.route_used.value if isinstance(self.route_used, Enum) else str(self.route_used),
            "fallback_used": self.fallback_used,
            "fallback_reason": self.fallback_reason,
            "attempts": self.attempts,
            "elapsed_ms": round(self.elapsed_ms, 3),
            "remaining_deadline_ms": round(self.remaining_deadline_ms, 3),
            "request_id": self.request_id,
            "model": self.response.model,
            "raw_status": self.response.raw_status,
        }

