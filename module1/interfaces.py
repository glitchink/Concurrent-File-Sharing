from dataclasses import dataclass, field
from datetime import datetime, timezone
import logging
from typing import Any, Optional, Protocol


@dataclass(frozen=True)
class AuthorizationResult:
    allowed: bool
    username: Optional[str] = None
    error_code: Optional[str] = None
    message: Optional[str] = None


class AuthService(Protocol):
    def authorize(
        self,
        session_token: Optional[str],
        action: str,
        file_id: Optional[str] = None,
    ) -> AuthorizationResult:
        ...


class Monitor(Protocol):
    def emit(self, event: dict[str, Any]) -> None:
        ...


class NullMonitor:
    def emit(self, event: dict[str, Any]) -> None:
        del event


class AllowAllAuthService:
    """Explicitly unsafe adapter for local development and tests only."""

    unsafe = True

    def authorize(
        self,
        session_token: Optional[str],
        action: str,
        file_id: Optional[str] = None,
    ) -> AuthorizationResult:
        del session_token, action, file_id
        return AuthorizationResult(allowed=True, username="local")


@dataclass
class Services:
    auth: Optional[AuthService] = None
    monitor: Optional[Monitor] = field(default_factory=NullMonitor)
    logger: logging.Logger = field(
        default_factory=lambda: logging.getLogger("module1")
    )

    def __post_init__(self) -> None:
        if self.monitor is None:
            self.monitor = NullMonitor()

    def authorize(
        self,
        session_token: Optional[str],
        action: str,
        file_id: Optional[str] = None,
    ) -> AuthorizationResult:
        if self.auth is None:
            return AuthorizationResult(
                allowed=False,
                error_code="AUTH_UNAVAILABLE",
                message="Authentication service is not configured.",
            )
        try:
            result = self.auth.authorize(session_token, action, file_id)
        except Exception:
            self.logger.exception("Authorization service failed")
            return AuthorizationResult(
                allowed=False,
                error_code="AUTH_UNAVAILABLE",
                message="Authentication service is unavailable.",
            )
        if not isinstance(result, AuthorizationResult):
            return AuthorizationResult(
                allowed=False,
                error_code="AUTH_INVALID_RESPONSE",
                message="Authentication service returned an invalid response.",
            )
        return result

    def emit_event(self, event: dict[str, Any]) -> None:
        event.setdefault(
            "timestamp",
            datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        )
        try:
            self.monitor.emit(event)
        except Exception:
            self.logger.exception("Monitoring hook failed")
