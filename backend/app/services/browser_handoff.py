from __future__ import annotations

from dataclasses import dataclass
import hashlib
import secrets
import threading
import time


@dataclass(frozen=True)
class BrowserHandoff:
    tester_id: str
    project_id: str
    stage: str
    expires_at: float
    agent_text: bool = False
    agent_image: bool = False
    agent_name: str = "外部 Agent"


class BrowserHandoffError(ValueError):
    pass


_handoffs: dict[str, BrowserHandoff] = {}
_handoffs_lock = threading.Lock()


def _token_key(token: str) -> str:
    return hashlib.sha256(str(token or "").encode("utf-8")).hexdigest()


def _purge_expired(now: float) -> None:
    expired = [key for key, handoff in _handoffs.items() if handoff.expires_at <= now]
    for key in expired:
        _handoffs.pop(key, None)


def issue_browser_handoff(
    *,
    tester_id: str,
    project_id: str,
    stage: str,
    ttl_seconds: int,
    agent_text: bool = False,
    agent_image: bool = False,
    agent_name: str = "外部 Agent",
    now: float | None = None,
) -> tuple[str, BrowserHandoff]:
    issued_at = time.time() if now is None else float(now)
    token = secrets.token_urlsafe(32)
    handoff = BrowserHandoff(
        tester_id=str(tester_id),
        project_id=str(project_id),
        stage=str(stage),
        expires_at=issued_at + max(1, int(ttl_seconds)),
        agent_text=bool(agent_text),
        agent_image=bool(agent_image),
        agent_name=str(agent_name or "外部 Agent").strip()[:60] or "外部 Agent",
    )
    with _handoffs_lock:
        _purge_expired(issued_at)
        _handoffs[_token_key(token)] = handoff
    return token, handoff


def redeem_browser_handoff(
    token: str,
    *,
    target_project_id: str,
    now: float | None = None,
) -> BrowserHandoff:
    redeemed_at = time.time() if now is None else float(now)
    key = _token_key(token)
    with _handoffs_lock:
        _purge_expired(redeemed_at)
        handoff = _handoffs.pop(key, None)
    if not handoff:
        raise BrowserHandoffError("交接链接已失效或已使用，请从 CLI 重新打开项目")
    if handoff.project_id != str(target_project_id):
        raise BrowserHandoffError("交接链接与目标项目不匹配，请从 CLI 重新打开项目")
    return handoff


def clear_browser_handoffs_for_tests() -> None:
    with _handoffs_lock:
        _handoffs.clear()
