"""Sign-in throttling (4.6): slow down guessing without letting anyone lock a person out from far away.

Three limits on FAILED attempts inside a sliding window (all configurable, see Settings):
- per (account, source address): one person guessing at one account. Small, so guessing is stopped fast;
- per source address: one machine trying many accounts (credential stuffing). Skipped when the source is unknown;
- per account from anywhere: a spread-out attack on one account. Large, so one stranger cannot lock the real user out.

A limit that is reached answers 429 with Retry-After; the correct password does not get through while a limit is in force.
Successful sign-ins are recorded but never counted. Rows older than a day are deleted. The moment a limit is first reached is
written to the audit log by the caller (the throttle itself keeps no audit trail).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from grademind_core.config import Settings
from grademind_core.db.models import LoginAttempt

UNKNOWN_IP = "unknown"  # the source could not be told: the per-address limit is skipped (it would be shared by everyone)


@dataclass(frozen=True)
class Limits:
    window: timedelta
    per_account_ip: int
    per_ip: int
    per_account: int

    @classmethod
    def from_settings(cls, s: Settings) -> Limits:
        return cls(
            timedelta(seconds=s.login_window_seconds),
            s.login_max_failures_per_account_and_ip,
            s.login_max_failures_per_ip,
            s.login_max_failures_per_account,
        )


def _failures(db: Session, since: datetime, email: str | None, ip: str | None) -> list[datetime]:
    q = select(LoginAttempt.at).where(LoginAttempt.success.is_(False), LoginAttempt.at >= since).order_by(LoginAttempt.at)
    if email is not None:
        q = q.where(LoginAttempt.email == email)
    if ip is not None:
        q = q.where(LoginAttempt.ip == ip)
    return list(db.scalars(q))


def _buckets(email: str, ip: str, lim: Limits) -> list[tuple[str, str | None, str | None, int]]:
    out: list[tuple[str, str | None, str | None, int]] = [("account_and_ip", email, ip, lim.per_account_ip)]
    if ip != UNKNOWN_IP:
        out.append(("ip", None, ip, lim.per_ip))
    out.append(("account", email, None, lim.per_account))
    return out


def retry_after(db: Session, email: str, ip: str, lim: Limits, now: datetime | None = None) -> tuple[int, str] | None:
    """(seconds to wait, which limit) if sign-in is currently refused for this account and source, else None."""
    now = now or datetime.now(UTC)
    since = now - lim.window
    worst: tuple[int, str] | None = None
    for name, e, i, limit in _buckets(email, ip, lim):
        times = _failures(db, since, e, i)
        if len(times) >= limit:
            # the limit lifts when enough old failures slide out of the window: the (len - limit + 1)-th oldest must expire
            frees_at = times[len(times) - limit] + lim.window
            wait = max(1, int((frees_at - now).total_seconds()) + 1)
            if worst is None or wait > worst[0]:
                worst = (wait, name)
    return worst


def record(db: Session, email: str, ip: str, success: bool, lim: Limits, now: datetime | None = None) -> str | None:
    """Store the attempt (the caller commits). For a failure, returns the name of the limit it has JUST reached, if any."""
    now = now or datetime.now(UTC)
    db.execute(delete(LoginAttempt).where(LoginAttempt.at < now - timedelta(days=1)))
    db.add(LoginAttempt(email=email, ip=ip, success=success))
    db.flush()
    if success:
        return None
    since = now - lim.window
    for name, e, i, limit in _buckets(email, ip, lim):
        n = int(
            db.scalar(
                select(func.count())
                .select_from(LoginAttempt)
                .where(
                    LoginAttempt.success.is_(False),
                    LoginAttempt.at >= since,
                    *([LoginAttempt.email == e] if e is not None else []),
                    *([LoginAttempt.ip == i] if i is not None else []),
                )
            )
            or 0
        )
        if n == limit:
            return name
    return None
