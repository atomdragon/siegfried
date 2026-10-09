"""Storage package exposing path management, atomic JSON and Vault append-only storage."""

from siegfried.storage.paths import SiegfriedPaths, default_paths
from siegfried.storage.atomic_json import atomic_write_json, read_json_locked
from siegfried.storage.vault import Vault, VaultAuditResult, VaultCorruptLine, audit_vault
from siegfried.storage.initialization import ensure_user_runtime, RuntimeInitResult
from siegfried.storage.validation import (
    validate_user_runtime,
    raise_if_runtime_invalid,
    RuntimeStatus,
    RuntimeValidationResult,
)
from siegfried.storage.secrets import load_secrets, get_secret
from siegfried.storage.aggregator import HistoricalAggregator, AggregatedMetrics

__all__ = [
    "SiegfriedPaths",
    "default_paths",
    "atomic_write_json",
    "read_json_locked",
    "Vault",
    "VaultAuditResult",
    "VaultCorruptLine",
    "audit_vault",
    "ensure_user_runtime",
    "RuntimeInitResult",
    "validate_user_runtime",
    "raise_if_runtime_invalid",
    "RuntimeStatus",
    "RuntimeValidationResult",
    "load_secrets",
    "get_secret",
    "HistoricalAggregator",
    "AggregatedMetrics",
]

