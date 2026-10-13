"""X (Twitter) signals at no cost: the post or profile a token links to, read through the public FxTwitter API.

Free sources cannot search X for mentions; that part is left to Claude's web search in the vetter. What the code
measures is the token's own claim: the narrative post (views, likes, who posted it) or the project's account
(followers, age). A deleted post or a 1-follower account behind a $1M token is a red flag code can act on.
"""
from __future__ import annotations

import math
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from typing import Protocol
from urllib.parse import urlparse

import httpx

from desk.config import MemesSettings

FX_URL = "https://api.fxtwitter.com"
_HANDLE = re.compile(r"^[A-Za-z0-9_]{1,15}$")
_RESERVED = {"i", "home", "search", "explore", "hashtag", "intent", "share", "messages", "notifications", "settings"}


class XUnavailable(Exception):
    """The X data source is down or rate limited: try again next cycle."""


@dataclass(frozen=True)
class XLink:
    kind: str  # "status" | "profile" | "community"
    handle: str | None
    ref: str | None  # post id or community id


@dataclass(frozen=True)
class XPost:
    id: str
    author: str
    author_followers: int
    author_verified: bool
    text: str
    created_at: datetime | None
    likes: int
    retweets: int
    replies: int
    quotes: int
    views: int | None


@dataclass(frozen=True)
class XProfile:
    handle: str
    followers: int
    tweets: int
    joined: datetime | None
    verified: bool


class XSource(Protocol):
    def post(self, handle: str, post_id: str) -> XPost | None: ...

    def profile(self, handle: str) -> XProfile | None: ...


@dataclass(frozen=True)
class XSignal:
    kind: str | None  # None: the token has no usable X link
    post: XPost | None = None
    profile: XProfile | None = None
    found: bool = True  # False: the linked post or account no longer exists

    @classmethod
    def collect(cls, link: XLink | None, source: XSource) -> XSignal:
        """Raises XUnavailable when the source is down (the candidate waits for the next cycle)."""
        if link is None:
            return cls(None)
        if link.kind == "status":
            post = source.post(link.handle, link.ref)
            return cls("status", post=post, found=post is not None)
        if link.kind == "profile":
            profile = source.profile(link.handle)
            return cls("profile", profile=profile, found=profile is not None)
        return cls("community")


def parse_x_link(url: str | None) -> XLink | None:
    try:
        parsed = urlparse((url or "").strip())
    except ValueError:
        return None
    host = (parsed.hostname or "").lower().removeprefix("www.").removeprefix("mobile.")
    if host not in ("x.com", "twitter.com"):
        return None
    parts = [p for p in parsed.path.split("/") if p]
    if len(parts) >= 3 and parts[0] == "i" and parts[1] == "communities" and parts[2].isdigit():
        return XLink("community", None, parts[2])
    if not parts or parts[0].lower() in _RESERVED or not _HANDLE.match(parts[0]):
        return None
    if len(parts) >= 3 and parts[1] == "status" and parts[2].isdigit():
        return XLink("status", parts[0], parts[2])
    if len(parts) == 1:
        return XLink("profile", parts[0], None)
    return None


def _int(value) -> int:
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


def _joined(value: str | None) -> datetime | None:
    """'Tue Oct 13 21:16:36 +0000 2026' without depending on the machine's locale."""
    try:
        _, month, day, clock, tz, year = (value or "").split()
        return parsedate_to_datetime(f"{day} {month} {year} {clock} {tz}")
    except (ValueError, TypeError):
        return None


def _verified(user: dict) -> bool:
    return bool((user.get("verification") or {}).get("verified"))


class FxTwitter:
    """Public, keyless JSON API behind fxtwitter.com embeds. Free, unofficial: it may change or go away."""

    def __init__(self, http: httpx.Client, base_url: str = FX_URL):
        self._http = http
        self._base = base_url.rstrip("/")

    def _get(self, path: str) -> dict | None:
        try:
            resp = self._http.get(self._base + path)
        except httpx.HTTPError as exc:
            raise XUnavailable(type(exc).__name__) from exc
        if resp.status_code == 429 or resp.status_code >= 500:
            raise XUnavailable(f"HTTP {resp.status_code}")
        if resp.status_code != 200:
            return None  # unknown accounts redirect away; 404 is gone
        try:
            data = resp.json()
        except ValueError:
            return None  # an unknown post comes back as the HTML embed page
        if not isinstance(data, dict) or data.get("code") != 200:
            return None
        return data

    def post(self, handle: str, post_id: str) -> XPost | None:
        data = self._get(f"/{handle}/status/{post_id}")
        tweet = (data or {}).get("tweet")
        if not isinstance(tweet, dict):
            return None
        author = tweet.get("author") or {}
        ts = tweet.get("created_timestamp")
        return XPost(
            id=str(tweet.get("id") or post_id), author=str(author.get("screen_name") or handle),
            author_followers=_int(author.get("followers")), author_verified=_verified(author),
            text=str(tweet.get("text") or ""),
            created_at=datetime.fromtimestamp(ts, timezone.utc) if isinstance(ts, (int, float)) else None,
            likes=_int(tweet.get("likes")), retweets=_int(tweet.get("retweets")), replies=_int(tweet.get("replies")),
            quotes=_int(tweet.get("quotes")), views=_int(tweet["views"]) if tweet.get("views") is not None else None,
        )

    def profile(self, handle: str) -> XProfile | None:
        data = self._get(f"/{handle}")
        user = (data or {}).get("user")
        if not isinstance(user, dict):
            return None
        return XProfile(handle=str(user.get("screen_name") or handle), followers=_int(user.get("followers")),
                        tweets=_int(user.get("tweets")), joined=_joined(user.get("joined")), verified=_verified(user))


def x_rejection(sig: XSignal, s: MemesSettings) -> str | None:
    if sig.kind is None:
        return "no_x_link" if s.x_require_link else None
    if not sig.found:
        return "x_link_dead"  # deleted post or gone account: devs clean up before and after a rug
    if sig.post is not None:
        strong = (sig.post.views or 0) >= s.x_min_narrative_views or sig.post.likes >= s.x_min_narrative_likes
        return None if strong else "x_weak_narrative"
    if sig.profile is not None and sig.profile.followers < s.x_min_profile_followers:
        return "x_no_community"
    return None


def compact(n: float) -> str:
    for size, suffix in ((1e9, "B"), (1e6, "M"), (1e3, "k")):
        if abs(n) >= size:
            return f"{n / size:.1f}{suffix}"
    return str(int(n))


def _age(then: datetime | None, now: datetime) -> str:
    if then is None:
        return "?"
    hours = max((now - then).total_seconds() / 3600, 0)
    return f"{hours:.0f}h" if hours < 48 else f"{hours / 24:.0f}d"


def describe_x(sig: XSignal, now: datetime) -> str:
    """One line for the journal, the dashboard and Claude. Never includes the post text (untrusted)."""
    if sig.kind is None:
        return "sem link do X"
    if not sig.found:
        return f"link do X ({sig.kind}) apagado ou inexistente"
    if sig.post is not None:
        p = sig.post
        views = f"{compact(p.views)} views, " if p.views is not None else ""
        check = " ✓" if p.author_verified else ""
        return (f"post de @{p.author}{check} ({compact(p.author_followers)} seguidores) há {_age(p.created_at, now)}: "
                f"{views}{compact(p.likes)} likes, {compact(p.retweets)} reposts, {compact(p.replies)} respostas")
    if sig.profile is not None:
        p = sig.profile
        check = " ✓" if p.verified else ""
        return f"perfil @{p.handle}{check}: {p.followers} seguidores, {p.tweets} posts, criado há {_age(p.joined, now)}"
    return "comunidade do X (sem métricas grátis)"


def narrative_strength(sig: XSignal) -> float:
    """Log-scale reach of what the token links to, for ranking candidates (0 when there is nothing)."""
    if sig.post is not None:
        return math.log10(1 + (sig.post.views or sig.post.likes * 50))
    if sig.profile is not None:
        return math.log10(1 + sig.profile.followers)
    return 0.0
