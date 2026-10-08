"""Hybrid inference orchestrator, deterministic routing, and safe fallback.

PRINCIPLES:
1. LLM interprets and recommends; deterministic core validates, computes, and executes.
2. Privacy precedence: LOCAL_ONLY never leaves the machine; no silent leakages.
3. Shared monotonic deadline across all attempts (never restarted on fallback).
4. Strictly bounded retry: at most 1 primary + 1 fallback attempt.
5. Zero secrets or sensitive conversation prompts in logs or exceptions.
6. Pure Python Standard Library (time, uuid, logging, typing).
"""

import logging
import time
from typing import Any, Callable, Dict, Optional, Union
import uuid

from siegfried.contracts.inference import (
    InferenceMessage,
    InferencePolicy,
    InferenceRequest,
    InferenceResponse,
    InferenceRoute,
    InferenceUsage,
    OrchestrationResult,
)
from siegfried.core.errors import (
    InferenceAuthError,
    InferenceConcurrencyExceededError,
    InferenceConfigError,
    InferenceDeadlineExceededError,
    InferenceError,
    InferenceHTTPError,
    InferenceRateLimitError,
    InferenceResponseError,
    InferenceResponseTooLargeError,
    InferenceSecurityError,
    InferenceTimeoutError,
    InferenceTransportError,
    InsufficientResourcesError,
    LlamaHealthCheckError,
    LlamaProcessTerminatedError,
    LlamaStartupError,
    LocalInferenceUnavailableError,
    NoAvailableEngineError,
    OrchestrationError,
    PrivacyViolationError,
)
from siegfried.inference.cloud import InferenceEngine
from siegfried.inference.llama_manager import LlamaLifecycleManager, LlamaServerState


DEFAULT_GLOBAL_DEADLINE_SECONDS = 10.0
DEFAULT_MIN_FALLBACK_MARGIN_SECONDS = 0.5
DEFAULT_ESTIMATED_LOCAL_STARTUP_SECONDS = 2.0


class InferenceOrchestrator:
    """Orchestrates hybrid inference across Cloud and Local providers."""

    def __init__(
        self,
        cloud_client: Optional[InferenceEngine] = None,
        local_client: Optional[InferenceEngine] = None,
        local_manager: Optional[LlamaLifecycleManager] = None,
        default_policy: InferencePolicy = InferencePolicy.CLOUD_PREFERRED,
        global_deadline_seconds: float = DEFAULT_GLOBAL_DEADLINE_SECONDS,
        min_fallback_margin_seconds: float = DEFAULT_MIN_FALLBACK_MARGIN_SECONDS,
        estimated_local_startup_seconds: float = DEFAULT_ESTIMATED_LOCAL_STARTUP_SECONDS,
        clock: Callable[[], float] = time.monotonic,
        logger: Optional[logging.Logger] = None,
    ) -> None:
        self.cloud_client = cloud_client
        self.local_client = local_client
        self.local_manager = local_manager
        self.default_policy = self._validate_policy(default_policy)
        self.global_deadline_seconds = float(global_deadline_seconds)
        self.min_fallback_margin_seconds = float(min_fallback_margin_seconds)
        self.estimated_local_startup_seconds = float(estimated_local_startup_seconds)
        self._clock = clock
        self._logger = logger or logging.getLogger("siegfried.inference.orchestrator")

    def _validate_policy(self, policy: Union[InferencePolicy, str]) -> InferencePolicy:
        """Validate and normalize inference policy."""
        if isinstance(policy, InferencePolicy):
            return policy
        if isinstance(policy, str):
            try:
                return InferencePolicy(policy)
            except ValueError:
                raise InferenceConfigError(f"Unsupported inference policy: {policy}")
        raise InferenceConfigError(f"Invalid policy type: {type(policy).__name__}")

    def is_cloud_available(self) -> bool:
        """Check if cloud inference client is configured and available."""
        if self.cloud_client is None:
            return False
        # If client has is_available method, check it
        if hasattr(self.cloud_client, "is_available"):
            try:
                return bool(self.cloud_client.is_available())
            except Exception:
                return False
        # If client has is_configured property/method
        if hasattr(self.cloud_client, "is_configured"):
            val = getattr(self.cloud_client, "is_configured")
            return bool(val() if callable(val) else val)
        # Check _api_key if present on CloudInferenceClient
        if hasattr(self.cloud_client, "_api_key"):
            api_key = getattr(self.cloud_client, "_api_key")
            return bool(api_key)
        return True

    def is_local_available(self) -> bool:
        """Check if local inference client and server manager are available."""
        if self.local_client is None:
            return False
        # If client has is_available method, check it
        if hasattr(self.local_client, "is_available"):
            try:
                return bool(self.local_client.is_available())
            except Exception:
                return False
        # If local_manager is attached, check its state and binary/model availability
        manager = self.local_manager or getattr(self.local_client, "manager", None)
        if manager is not None:
            state = getattr(manager, "state", None)
            if state in (LlamaServerState.UNAVAILABLE, LlamaServerState.FAILED):
                return False
            # Check configured paths if present
            if hasattr(manager, "binary_path") and manager.binary_path is not None:
                if not manager.binary_path.exists():
                    return False
            if hasattr(manager, "model_path") and manager.model_path is not None:
                if not manager.model_path.exists():
                    return False
        return True

    def _is_recoverable_error(self, err: Exception) -> bool:
        """Classify whether an exception allows fallback to a secondary engine."""
        # Security violations, contract violations, deadlines, and corrupted responses are never recoverable
        if isinstance(err, (InferenceSecurityError, InferenceConfigError, InferenceDeadlineExceededError)):
            return False
        if isinstance(err, (InferenceResponseError, InferenceResponseTooLargeError)):
            return False

        # Recoverable cloud errors:
        if isinstance(err, (InferenceTimeoutError, InferenceTransportError, InferenceRateLimitError)):
            return True
        if isinstance(err, InferenceHTTPError):
            # 429 and 5xx (500, 502, 503, 504) are transient/recoverable
            return err.status_code in (429, 500, 502, 503, 504)
        if isinstance(err, InferenceAuthError):
            # Auth error on cloud is recoverable by falling back to local (at most once, no loop)
            return True

        # Recoverable local errors:
        if isinstance(
            err,
            (
                LocalInferenceUnavailableError,
                LlamaStartupError,
                LlamaHealthCheckError,
                LlamaProcessTerminatedError,
                InsufficientResourcesError,
                InferenceConcurrencyExceededError,
            ),
        ):
            return True

        return False

    def orchestrate(
        self,
        request: InferenceRequest,
        policy: Optional[Union[InferencePolicy, str]] = None,
        request_id: Optional[str] = None,
    ) -> OrchestrationResult:
        """Execute hybrid inference request governed by deterministic routing and strict deadlines."""
        # 1. Validate request contract
        if not isinstance(request, InferenceRequest):
            raise InferenceConfigError("request must be an instance of InferenceRequest")
        try:
            request.validate()
        except ValueError as e:
            raise InferenceConfigError(f"Invalid inference request contract: {e}") from e

        # 2. Resolve policy
        eff_policy = self._validate_policy(policy) if policy is not None else self.default_policy
        req_id = request_id or f"inf-{uuid.uuid4().hex[:8]}"

        # 3. Establish absolute monotonic deadline budget
        t0 = self._clock()
        if request.deadline is not None:
            abs_deadline = float(request.deadline)
        else:
            budget = min(float(request.timeout_seconds), self.global_deadline_seconds)
            abs_deadline = t0 + budget

        # Check deadline before starting
        remaining = abs_deadline - self._clock()
        if remaining <= 0:
            raise InferenceDeadlineExceededError(
                f"Inference deadline already expired before initiation ({remaining:.3f}s remaining)"
            )

        # 4. Determine primary and fallback routes
        primary_route: InferenceRoute
        fallback_route: Optional[InferenceRoute] = None

        if eff_policy == InferencePolicy.CLOUD_PREFERRED:
            if self.is_cloud_available():
                primary_route = InferenceRoute.CLOUD
                fallback_route = InferenceRoute.LOCAL if self.is_local_available() else None
            elif self.is_local_available():
                # Cloud is not configured/available; direct alternate to Local
                primary_route = InferenceRoute.LOCAL
                fallback_route = None
            else:
                raise NoAvailableEngineError(
                    "Neither Cloud nor Local inference engine is available for CLOUD_PREFERRED policy"
                )

        elif eff_policy == InferencePolicy.LOCAL_PREFERRED:
            if self.is_local_available():
                primary_route = InferenceRoute.LOCAL
                fallback_route = InferenceRoute.CLOUD if self.is_cloud_available() else None
            elif self.is_cloud_available():
                # Local is not available; direct alternate to Cloud
                primary_route = InferenceRoute.CLOUD
                fallback_route = None
            else:
                raise NoAvailableEngineError(
                    "Neither Local nor Cloud inference engine is available for LOCAL_PREFERRED policy"
                )

        elif eff_policy == InferencePolicy.LOCAL_ONLY:
            primary_route = InferenceRoute.LOCAL
            fallback_route = None  # Strict privacy: cloud fallback is completely forbidden
            if not self.is_local_available():
                raise NoAvailableEngineError(
                    "Local inference engine is unavailable and policy is LOCAL_ONLY (cloud fallback strictly forbidden)"
                )

        elif eff_policy == InferencePolicy.CLOUD_ONLY:
            primary_route = InferenceRoute.CLOUD
            fallback_route = None  # Local fallback is forbidden
            if not self.is_cloud_available():
                raise NoAvailableEngineError(
                    "Cloud inference engine is unavailable and policy is CLOUD_ONLY (local fallback forbidden)"
                )
        else:
            raise InferenceConfigError(f"Unhandled inference policy: {eff_policy}")

        # 5. Attempt 1: Execute primary engine
        primary_engine = self.cloud_client if primary_route == InferenceRoute.CLOUD else self.local_client
        if primary_engine is None:
            raise NoAvailableEngineError(f"Engine client for {primary_route.value} is not configured")

        remaining_before_primary = abs_deadline - self._clock()
        if remaining_before_primary <= 0:
            raise InferenceDeadlineExceededError(
                f"Inference deadline expired before primary engine execution ({remaining_before_primary:.3f}s remaining)"
            )

        # Pre-check for local engine startup budget if STOPPED
        if primary_route == InferenceRoute.LOCAL:
            manager = self.local_manager or getattr(self.local_client, "manager", None)
            if manager is not None and getattr(manager, "state", None) == LlamaServerState.STOPPED:
                if remaining_before_primary < self.estimated_local_startup_seconds:
                    raise InferenceDeadlineExceededError(
                        f"Insufficient remaining deadline ({remaining_before_primary:.2f}s < "
                        f"{self.estimated_local_startup_seconds:.2f}s) to start local llama-server"
                    )

        req_primary = InferenceRequest(
            messages=request.messages,
            model=request.model,
            max_tokens=request.max_tokens,
            temperature=request.temperature,
            timeout_seconds=min(request.timeout_seconds, remaining_before_primary),
            deadline=abs_deadline,
            extra_params=request.extra_params,
        )

        try:
            resp = primary_engine.generate(req_primary)
            self._validate_response(resp)
            t_finish = self._clock()
            return OrchestrationResult(
                response=resp,
                route_selected=primary_route,
                route_used=primary_route,
                fallback_used=False,
                fallback_reason=None,
                attempts=1,
                elapsed_ms=(t_finish - t0) * 1000.0,
                remaining_deadline_ms=max(0.0, (abs_deadline - t_finish) * 1000.0),
                request_id=req_id,
            )
        except Exception as primary_err:
            self._log_safe_error("primary", primary_route, primary_err, req_id)

            # Check if fallback is permitted
            if fallback_route is None:
                # Privacy constraint or policy restriction prohibits fallback
                if eff_policy == InferencePolicy.LOCAL_ONLY and isinstance(primary_err, InferenceSecurityError):
                    raise primary_err
                if eff_policy == InferencePolicy.LOCAL_ONLY:
                    raise NoAvailableEngineError(
                        f"Local engine failed under LOCAL_ONLY policy: {type(primary_err).__name__}"
                    ) from primary_err
                raise primary_err

            # Check error recoverability
            if not self._is_recoverable_error(primary_err):
                raise primary_err

            # Privacy check guard: ensure fallback route does not violate policy
            if eff_policy == InferencePolicy.LOCAL_ONLY and fallback_route == InferenceRoute.CLOUD:
                raise PrivacyViolationError("Attempted cloud fallback under LOCAL_ONLY policy")

            # 6. Attempt 2: Fallback engine execution
            return self._execute_fallback(
                request=request,
                primary_route=primary_route,
                fallback_route=fallback_route,
                primary_err=primary_err,
                abs_deadline=abs_deadline,
                t0=t0,
                req_id=req_id,
            )

    def _execute_fallback(
        self,
        request: InferenceRequest,
        primary_route: InferenceRoute,
        fallback_route: InferenceRoute,
        primary_err: Exception,
        abs_deadline: float,
        t0: float,
        req_id: str,
    ) -> OrchestrationResult:
        """Execute fallback attempt on secondary engine under remaining deadline budget."""
        remaining_fallback = abs_deadline - self._clock()
        if remaining_fallback <= self.min_fallback_margin_seconds:
            raise InferenceDeadlineExceededError(
                f"Insufficient remaining deadline ({remaining_fallback:.3f}s <= "
                f"{self.min_fallback_margin_seconds:.3f}s margin) to perform fallback"
            )

        # Check local engine startup time if fallback is Local
        if fallback_route == InferenceRoute.LOCAL:
            manager = self.local_manager or getattr(self.local_client, "manager", None)
            if manager is not None and getattr(manager, "state", None) == LlamaServerState.STOPPED:
                if remaining_fallback < self.estimated_local_startup_seconds:
                    raise InferenceDeadlineExceededError(
                        f"Insufficient remaining deadline ({remaining_fallback:.2f}s < "
                        f"{self.estimated_local_startup_seconds:.2f}s) to start local server for fallback"
                    )

        fallback_engine = self.cloud_client if fallback_route == InferenceRoute.CLOUD else self.local_client
        if fallback_engine is None:
            raise NoAvailableEngineError(
                f"Fallback engine {fallback_route.value} is not configured"
            ) from primary_err

        # Prepare request bounded by remaining deadline
        req_fallback = InferenceRequest(
            messages=request.messages,
            model=request.model,
            max_tokens=request.max_tokens,
            temperature=request.temperature,
            timeout_seconds=remaining_fallback,
            deadline=abs_deadline,  # Shared monotonic deadline!
            extra_params=request.extra_params,
        )

        try:
            resp = fallback_engine.generate(req_fallback)
            self._validate_response(resp)
            t_finish = self._clock()
            return OrchestrationResult(
                response=resp,
                route_selected=primary_route,
                route_used=fallback_route,
                fallback_used=True,
                fallback_reason=f"{type(primary_err).__name__}",
                attempts=2,
                elapsed_ms=(t_finish - t0) * 1000.0,
                remaining_deadline_ms=max(0.0, (abs_deadline - t_finish) * 1000.0),
                request_id=req_id,
            )
        except Exception as fallback_err:
            self._log_safe_error("fallback", fallback_route, fallback_err, req_id)
            # Secondary engine failed: terminates the operation (strictly max 2 attempts)
            raise NoAvailableEngineError(
                f"Both inference engines failed: primary ({primary_route.value}) failed with "
                f"{type(primary_err).__name__}, fallback ({fallback_route.value}) failed with "
                f"{type(fallback_err).__name__}"
            ) from fallback_err

    def _validate_response(self, response: Any) -> None:
        """Validate structure and non-emptiness of engine response."""
        if not isinstance(response, InferenceResponse):
            raise InferenceResponseError(
                f"Engine returned invalid response type: {type(response).__name__}"
            )
        if response.content is None or not isinstance(response.content, str) or not response.content.strip():
            raise InferenceResponseError("Engine returned empty response content")

    def _log_safe_error(
        self,
        phase: str,
        route: InferenceRoute,
        err: Exception,
        req_id: str,
    ) -> None:
        """Log safe diagnostic metadata without exposing secrets, prompts, or sensitive payloads."""
        err_type = type(err).__name__
        self._logger.warning(
            "Inference %s attempt on %s failed [req_id=%s, error_type=%s]",
            phase,
            route.value,
            req_id,
            err_type,
        )
