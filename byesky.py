#!/usr/bin/env python3
"""
ByeSky - Delete or preview AT Protocol / Bluesky posts older than N days.
Supports both Bluesky (bsky.social) and self-hosted PDS instances.
"""

import os
import sys
import logging
import getpass
import re
import json
import time
from datetime import datetime, timedelta, timezone
from typing import List, Optional, Tuple

import click
from tqdm import tqdm
from tenacity import retry, retry_if_exception, stop_after_attempt, wait_exponential
from dateutil import parser as dateutil_parser
from atproto import Client, models
from atproto.exceptions import RequestException

__version__ = "0.2.1"

BSKY_PDS = "https://bsky.social"
# Bluesky meters writes by points (DELETE = 1 pt): 5,000/hour and 35,000/day
# per account. 0.75s between deletes keeps a steady run just under 5,000/hr so
# the hourly wall is rarely hit; when it is, we back off on the reset header.
DELETE_DELAY = 0.75  # seconds between deletes — stays within Bluesky rate limits

# ─── Logging ─────────────────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)],
)
logger = logging.getLogger(__name__)

# ─── Security ────────────────────────────────────────────────────────────────
if os.name != "nt" and os.geteuid() == 0:
    logger.warning("Running as root is not recommended.")

# ─── Rate limiting ───────────────────────────────────────────────────────────
class RateLimitError(Exception):
    """Raised on a 429 from the PDS, carrying the reset window details."""

    def __init__(self, reset_at: Optional[int], window: Optional[int], limit):
        self.reset_at = reset_at   # unix epoch when the window resets, if known
        self.window = window       # window length in seconds (3600 / 86400)
        self.limit = limit         # the policy's point ceiling for that window
        super().__init__(f"Rate limit exceeded (limit={limit}, window={window}s)")


class DailyLimitReached(Exception):
    """Raised when the 35,000/day write limit is hit — resume on the next day."""

    def __init__(self, reset_at: Optional[int]):
        self.reset_at = reset_at
        super().__init__("Daily write rate limit reached")


def _ratelimit_from_exc(exc: RequestException) -> Optional[RateLimitError]:
    """Return a RateLimitError if exc is a 429, parsing rate-limit headers."""
    resp = getattr(exc, "response", None)
    if resp is None or getattr(resp, "status_code", None) != 429:
        return None
    # Response.headers is a plain dict; keys arrive lowercased but be defensive.
    headers = {str(k).lower(): v for k, v in (getattr(resp, "headers", None) or {}).items()}

    def _to_int(val):
        try:
            return int(val)
        except (TypeError, ValueError):
            return None

    reset_at = _to_int(headers.get("ratelimit-reset"))
    limit = headers.get("ratelimit-limit")
    window = None
    m = re.search(r"w=(\d+)", str(headers.get("ratelimit-policy", "")))
    if m:
        window = int(m.group(1))
    return RateLimitError(reset_at=reset_at, window=window, limit=limit)


# ─── Retry ───────────────────────────────────────────────────────────────────
# Retry transient network errors, but never a rate-limit signal — those are
# handled deliberately by waiting on the reset header (see _delete_with_backoff).
_network_retry = retry(
    stop=stop_after_attempt(5),
    wait=wait_exponential(multiplier=2, min=2, max=60),
    retry=retry_if_exception(lambda e: not isinstance(e, (RateLimitError, DailyLimitReached))),
    reraise=True,
)


@_network_retry
def _list_records(
    client: Client,
    did: str,
    collection: str,
    cursor: Optional[str],
    limit: int = 100,
):
    """One page via com.atproto.repo.listRecords."""
    params: dict = {"repo": did, "collection": collection, "limit": limit}
    if cursor:
        params["cursor"] = cursor
    return client.com.atproto.repo.list_records(params)


@_network_retry
def _delete_record(client: Client, did: str, collection: str, rkey: str):
    """One deletion via com.atproto.repo.deleteRecord."""
    try:
        return client.com.atproto.repo.delete_record(
            models.ComAtprotoRepoDeleteRecord.Data(
                repo=did,
                collection=collection,
                rkey=rkey,
            )
        )
    except RequestException as e:
        rl = _ratelimit_from_exc(e)
        if rl is not None:
            raise rl from e
        raise


def _delete_with_backoff(client: Client, did: str, collection: str, rkey: str):
    """Delete one record, waiting out the hourly write limit.

    The hourly window (5,000/hr) is handled by sleeping until its reset header
    and retrying the same delete. The daily window (35,000/day) would mean
    blocking for up to 24h, so it is surfaced as DailyLimitReached for a clean,
    resumable exit instead.
    """
    while True:
        try:
            return _delete_record(client, did, collection, rkey)
        except RateLimitError as rl:
            # Daily window (w > 1 hour): don't block for hours — bail to resume later.
            if rl.window and rl.window > 3600:
                raise DailyLimitReached(rl.reset_at) from rl
            wait_s = max(0.0, rl.reset_at - time.time()) if rl.reset_at else 60.0
            reset_str = (
                datetime.fromtimestamp(rl.reset_at, timezone.utc).strftime("%H:%M:%S UTC")
                if rl.reset_at else "unknown"
            )
            logger.warning(
                "Hourly write limit reached (%s/hr). Waiting ~%d min until reset at %s…",
                rl.limit or "5000", int(wait_s // 60) + 1, reset_str,
            )
            time.sleep(wait_s + 2)


# ─── Record helpers ──────────────────────────────────────────────────────────
def _parse_created_at(value) -> Optional[datetime]:
    """Return UTC-aware datetime from a record value's createdAt field."""
    raw: Optional[str] = None
    if hasattr(value, "created_at"):
        raw = value.created_at
    elif isinstance(value, dict):
        raw = value.get("createdAt") or value.get("created_at")
    if not raw:
        return None
    try:
        dt = dateutil_parser.isoparse(raw)
        return dt.astimezone(timezone.utc) if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
    except (ValueError, TypeError):
        return None


def _is_reply(value) -> bool:
    if hasattr(value, "reply"):
        return value.reply is not None
    if isinstance(value, dict):
        return value.get("reply") is not None
    return False


def _record_text(value) -> str:
    if hasattr(value, "text"):
        return value.text or ""
    if isinstance(value, dict):
        return value.get("text", "") or ""
    return ""


# ─── Core logic ──────────────────────────────────────────────────────────────
def process_posts(
    *,
    handle: str,
    token: str,
    pds_url: str,
    days_old: int,
    preview_only: bool,
    log_file: Optional[str],
    match_patterns: Tuple[str, ...],
    use_regex: bool,
    after: Optional[str],
    before: Optional[str],
    backup_file: Optional[str],
    include_replies: bool,
    include_reposts: bool,
    delete_delay: float,
    quiet: bool,
) -> dict:
    client = Client(base_url=pds_url)
    try:
        client.login(handle, token)
    except Exception as e:
        logger.error("Login failed: %s", e)
        sys.exit(2)

    did: str = client.me.did
    logger.info("Authenticated  handle=%s  DID=%s  PDS=%s", handle, did, pds_url)

    cutoff = datetime.now(timezone.utc) - timedelta(days=days_old)
    after_dt = dateutil_parser.parse(after).astimezone(timezone.utc) if after else None
    before_dt = dateutil_parser.parse(before).astimezone(timezone.utc) if before else None

    compiled: list = (
        [re.compile(p, re.IGNORECASE) for p in match_patterns]
        if use_regex and match_patterns
        else list(match_patterns)
    )

    # posts first, then native reposts if requested
    collections = ["app.bsky.feed.post"]
    if include_reposts:
        collections.append("app.bsky.feed.repost")

    # (uri, collection, rkey, created_at, text)
    candidates: List[Tuple[str, str, str, datetime, str]] = []
    total_scanned = 0

    for collection in collections:
        cursor: Optional[str] = None
        label = collection.rsplit(".", 1)[-1]

        with tqdm(desc=f"Scanning {label}s", unit=" page", disable=quiet) as pbar:
            while True:
                page = _list_records(client, did, collection, cursor)
                for rec in page.records:
                    total_scanned += 1
                    created_at = _parse_created_at(rec.value)
                    if created_at is None:
                        logger.debug("Skipping %s — no createdAt", rec.uri)
                        continue
                    if created_at >= cutoff:
                        continue
                    if after_dt and created_at < after_dt:
                        continue
                    if before_dt and created_at > before_dt:
                        continue

                    text = ""
                    if collection == "app.bsky.feed.post":
                        if not include_replies and _is_reply(rec.value):
                            continue
                        text = _record_text(rec.value)

                    if compiled:
                        if use_regex:
                            if not any(p.search(text) for p in compiled):
                                continue
                        else:
                            tl = text.lower()
                            if not any(p.lower() in tl for p in compiled):
                                continue

                    rkey = rec.uri.split("/")[-1]
                    candidates.append((rec.uri, collection, rkey, created_at, text))

                pbar.update()
                cursor = page.cursor
                if not cursor:
                    break

        logger.info("Scanned %d %s record(s)", total_scanned, label)

    if not candidates:
        logger.info("No posts match the given criteria.")
        return {"scanned": total_scanned, "matched": 0, "deleted": 0, "failed": 0}

    log_file = log_file or ("preview_log.txt" if preview_only else "deleted_posts_log.txt")
    backup_file = backup_file or "deleted_posts_backup.jsonl"
    logger.info("Writing log to %s", log_file)

    deleted = failed = 0
    hit_daily_limit = False
    backup_fh = None
    try:
        if not preview_only:
            backup_fh = open(backup_file, "a", encoding="utf-8")

        with open(log_file, "a", encoding="utf-8") as log_fh, tqdm(
            candidates, desc="Processing", unit=" post", disable=quiet
        ) as bar:
            for uri, collection, rkey, created_at, text in bar:
                log_line = (
                    f"{created_at.strftime('%Y-%m-%d %H:%M:%S')} UTC  "
                    f"{text.replace(chr(10), ' ')}\n---\n"
                )
                if preview_only:
                    log_fh.write(log_line)
                    continue

                try:
                    _delete_with_backoff(client, did, collection, rkey)
                except DailyLimitReached:
                    logger.warning(
                        "Daily write limit (35,000/day) reached — stopping. "
                        "Re-run ByeSky after the window resets to delete the rest; "
                        "it will skip what's already gone."
                    )
                    hit_daily_limit = True
                    break
                except Exception as e:
                    logger.warning("Failed to delete %s: %s", uri, e)
                    failed += 1
                    continue

                # Record log + backup only after a confirmed deletion.
                log_fh.write(log_line)
                if backup_fh:
                    backup_fh.write(
                        json.dumps(
                            {
                                "uri": uri,
                                "collection": collection,
                                "rkey": rkey,
                                "created_at": created_at.isoformat(),
                                "text": text,
                            },
                            ensure_ascii=False,
                        )
                        + "\n"
                    )
                deleted += 1
                time.sleep(delete_delay)
    finally:
        if backup_fh:
            backup_fh.close()

    return {
        "scanned": total_scanned,
        "matched": len(candidates),
        "deleted": deleted,
        "failed": failed,
        "hit_daily_limit": hit_daily_limit,
    }


# ─── CLI ─────────────────────────────────────────────────────────────────────
@click.command(
    help=(
        "Delete or preview AT Protocol / Bluesky posts older than N days.\n\n"
        "Supports Bluesky (bsky.social) and self-hosted PDS instances via --pds.\n\n"
        "SECURITY: Pass your app password via the BYESKY_TOKEN environment variable "
        "rather than --token to avoid exposing it in your process list."
    )
)
@click.version_option(__version__, "--version", message="%(prog)s %(version)s")
@click.option(
    "--handle", "-u",
    prompt="Bluesky handle",
    help="Your handle, e.g. alice.bsky.social or alice.example.com",
)
@click.option(
    "--token", "-p",
    default=None,
    envvar="BYESKY_TOKEN",
    help="App password; falls back to BYESKY_TOKEN env var, then interactive prompt",
)
@click.option(
    "--pds",
    default=BSKY_PDS,
    show_default=True,
    help="AT Protocol PDS base URL (set to your PDS for self-hosted instances)",
)
@click.option(
    "--days", "-d",
    default=30,
    show_default=True,
    help="Target posts older than this many days",
)
@click.option(
    "--preview/--no-preview",
    default=True,
    help="Dry run — show what would be deleted without deleting (default: --preview)",
)
@click.option(
    "--log-file", "-l",
    default=None,
    help="Log filename (default: preview_log.txt or deleted_posts_log.txt)",
)
@click.option(
    "--match", "-m",
    multiple=True,
    help="Only target posts containing this keyword; may be repeated",
)
@click.option(
    "--regex/--no-regex",
    default=False,
    help="Treat --match values as regular expressions",
)
@click.option(
    "--after",
    default=None,
    metavar="DATE",
    help="Only target posts on or after this date (YYYY-MM-DD or ISO 8601)",
)
@click.option(
    "--before",
    default=None,
    metavar="DATE",
    help="Only target posts on or before this date (YYYY-MM-DD or ISO 8601)",
)
@click.option(
    "--backup-file",
    default=None,
    help="JSONL file for deleted-post backups (default: deleted_posts_backup.jsonl)",
)
@click.option(
    "--include-replies/--exclude-replies",
    default=False,
    help="Include reply posts (default: exclude)",
)
@click.option(
    "--include-reposts/--exclude-reposts",
    default=False,
    help="Include native reposts (app.bsky.feed.repost records; default: exclude)",
)
@click.option(
    "--delete-delay",
    default=DELETE_DELAY,
    show_default=True,
    type=float,
    help="Seconds between deletions (rate-limit buffer)",
)
@click.option("--verbose", is_flag=True, default=False, help="Enable DEBUG logging")
@click.option("--quiet", is_flag=True, default=False, help="Suppress all output except errors and the summary")
def cli(
    handle, token, pds, days, preview, log_file, match, regex,
    after, before, backup_file, include_replies, include_reposts,
    delete_delay, verbose, quiet,
):
    level = logging.ERROR if quiet else (logging.DEBUG if verbose else logging.INFO)
    logger.setLevel(level)
    for lib in ("atproto_client", "httpx"):
        logging.getLogger(lib).setLevel(logging.DEBUG if verbose else logging.WARNING)

    if not preview and days < 1:
        logger.error("Refusing to target posts newer than 1 day. Use --days 1 or higher.")
        sys.exit(2)

    # Normalise PDS URL
    pds = pds.rstrip("/")
    if "://" not in pds:
        pds = f"https://{pds}"

    if token and "BYESKY_TOKEN" not in os.environ:
        logger.warning(
            "SECURITY: --token exposes your password in the process list. "
            "Use the BYESKY_TOKEN environment variable for automation."
        )

    if not token:
        token = getpass.getpass("App password: ").strip()

    if not quiet:
        logger.info(
            "handle=%s  pds=%s  days=%d  preview=%s",
            handle, pds, days, preview,
        )

    if not preview and not quiet:
        click.confirm(
            f"Delete posts older than {days} days from {handle}? This cannot be undone.",
            abort=True,
        )

    try:
        result = process_posts(
            handle=handle,
            token=token,
            pds_url=pds,
            days_old=days,
            preview_only=preview,
            log_file=log_file,
            match_patterns=match,
            use_regex=regex,
            after=after,
            before=before,
            backup_file=backup_file,
            include_replies=include_replies,
            include_reposts=include_reposts,
            delete_delay=delete_delay,
            quiet=quiet,
        )
    except (OSError, IOError) as e:
        logger.error("File error: %s", e)
        sys.exit(3)
    except SystemExit:
        raise
    except Exception as e:
        logger.error("Unexpected error: %s", e)
        sys.exit(99)

    effective_log = log_file or ("preview_log.txt" if preview else "deleted_posts_log.txt")
    click.echo("\n── Summary ───────────────────────────────")
    click.echo(f"  Posts scanned   : {result['scanned']}")
    click.echo(f"  Posts matched   : {result['matched']}")
    if not preview:
        click.echo(f"  Posts deleted   : {result['deleted']}")
        click.echo(f"  Delete failures : {result['failed']}")
        remaining = result["matched"] - result["deleted"] - result["failed"]
        if result.get("hit_daily_limit"):
            click.echo(f"  Not yet deleted : {remaining} (daily limit — re-run to continue)")
    click.echo(f"  Log file        : {effective_log}")
    click.echo("──────────────────────────────────────────")

    if result.get("hit_daily_limit"):
        sys.exit(75)  # EX_TEMPFAIL — retry later
    if result.get("failed", 0) > 0:
        sys.exit(1)


if __name__ == "__main__":
    cli()
