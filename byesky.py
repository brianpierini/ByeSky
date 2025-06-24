import os
import sys
import logging
import getpass
import re
import json
from datetime import datetime, timedelta, timezone

import click
from tqdm import tqdm
from tenacity import retry, stop_after_attempt, wait_exponential, retry_if_exception_type
from dateutil import parser
from atproto import Client, models

# ─── Logging Setup ───────────────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)]
)
logger = logging.getLogger(__name__)

# ─── Security Checks ─────────────────────────────────────────────────────────────
if os.geteuid() == 0:
    logger.warning("It is not recommended to run this script as root.")

# ─── Retry Decorator ─────────────────────────────────────────────────────────────
retry_on_network = retry(
    stop=stop_after_attempt(3),
    wait=wait_exponential(multiplier=1, min=1, max=10),
    retry=retry_if_exception_type(Exception),
    reraise=True
)

# ─── Core Logic ────────────────────────────────────────────────────────────────
@retry_on_network
def fetch_feed_page(client, actor, cursor, limit):
    return client.app.bsky.feed.get_author_feed({
        "actor": actor,
        "cursor": cursor,
        "limit": limit
    })

@retry_on_network
def delete_record(client, handle, uri):
    rkey = uri.split("/")[-1]
    return client.com.atproto.repo.delete_record(models.ComAtprotoRepoDeleteRecord.Data(
        repo=handle,
        collection="app.bsky.feed.post",
        rkey=rkey
    ))

def process_posts(
    handle, token, days_old, preview_only, log_file,
    match_patterns=None, use_regex=False, after=None, before=None, backup_file=None,
    include_replies=False, include_reposts=False,
    quiet=False
):
    client = Client()
    client.login(handle, token)
    cutoff = datetime.now(timezone.utc) - timedelta(days=days_old)

    # Parse after/before if provided
    after_dt = parser.parse(after).astimezone(timezone.utc) if after else None
    before_dt = parser.parse(before).astimezone(timezone.utc) if before else None

    posts = []
    cursor = None

    # Prepare matchers
    compiled_patterns = []
    if match_patterns:
        if use_regex:
            compiled_patterns = [re.compile(p, re.IGNORECASE) for p in match_patterns]
        else:
            compiled_patterns = match_patterns

    if not quiet:
        logger.info(f"Scanning posts older than {days_old} days…")

    # Hide fetching pages progress bar in quiet mode
    page_bar_args = {"desc": "Fetching pages", "unit": "page"}
    if quiet:
        page_bar_args["disable"] = True

    with tqdm(**page_bar_args) as page_bar:
        while True:
            feed = fetch_feed_page(client, handle, cursor, limit=50)
            for item in feed.feed:
                pt = parser.isoparse(item.post.indexed_at)
                try:
                    pt = pt.astimezone(timezone.utc)
                except Exception:
                    pt = pt.replace(tzinfo=timezone.utc)
                record_dict = item.post.record.dict()
                text = record_dict.get("text", "")

                # Filter by cutoff, date range, and match patterns
                if pt < cutoff:
                    # Replies: check if 'reply' field exists and is not None
                    is_reply = getattr(item.post, "reply", None) is not None
                    # Reposts: check if 'embed' field is a repost (type 'app.bsky.embed.record#view')
                    embed = getattr(item.post, "embed", None)
                    is_repost = False
                    if embed and hasattr(embed, "$type"):
                        is_repost = embed.type == "app.bsky.embed.record#view"
                    elif embed and isinstance(embed, dict):
                        is_repost = embed.get("$type") == "app.bsky.embed.record#view"

                    # Exclude replies/reposts if not included
                    if (is_reply and not include_replies) or (is_repost and not include_reposts):
                        continue

                    in_range = True
                    if after_dt and pt < after_dt:
                        in_range = False
                    if before_dt and pt > before_dt:
                        in_range = False
                    matched = True
                    if compiled_patterns:
                        if use_regex:
                            matched = any(p.search(text) for p in compiled_patterns)
                        else:
                            matched = any(p.lower() in text.lower() for p in compiled_patterns)
                    if in_range and matched:
                        posts.append((item.post.uri, item.post, pt))
            cursor = feed.cursor
            page_bar.update()
            if not cursor:
                break

    total = len(posts)
    if not total:
        logger.info("✅ No posts to delete/preview.")
        return {"scanned": page_bar.n * 50, "matched": 0, "deleted": 0, "failed": 0}

    # Decide where to log
    if not log_file:
        log_file = "preview_log.txt" if preview_only else "deleted_posts_log.txt"
    logger.info(f"Writing details to {log_file}")

    # Decide backup file
    if not backup_file:
        backup_file = "deleted_posts_backup.jsonl"

    deleted = failed = 0
    backup_fh = None
    if not preview_only:
        backup_fh = open(backup_file, "a", encoding="utf-8")

    # Only show the main progress bar if not quiet, otherwise disable it
    post_bar_args = {"desc": "Processing posts", "unit": "post"}
    if quiet:
        post_bar_args["disable"] = False  # Show progress bar even in quiet mode

    with open(log_file, "a", encoding="utf-8") as f, \
         tqdm(posts, **post_bar_args) as post_bar:
        for uri, post, pt in post_bar:
            record_dict = post.record.dict()
            text = record_dict.get("text", "")
            text = text.replace("\n", " ")
            f.write(f"{pt.strftime('%Y-%m-%d %H:%M:%S')} UTC  {text}\n---\n")
            if not preview_only:
                # Backup full post JSON before deletion
                backup_fh.write(json.dumps({
                    "uri": uri,
                    "datetime": pt.isoformat(),
                    "post": post.dict()
                }, ensure_ascii=False) + "\n")
                try:
                    delete_record(client, handle, uri)
                    deleted += 1
                except Exception as e:
                    logger.warning(f"Failed deleting {uri}: {e}")
                    failed += 1
            post_bar.update()
    if backup_fh:
        backup_fh.close()

    return {
        "scanned": page_bar.n * 50,
        "matched": total,
        "deleted": deleted,
        "failed": failed
    }

# ─── CLI ENTRYPOINT ────────────────────────────────────────────────────────────
@click.command(
    help="Delete or preview BlueSky posts older than N days.\n\n"
         "SECURITY TIP: For automation, consider passing your app password via the BYESKY_TOKEN environment variable instead of the --token argument."
)
@click.option("--handle", "-u", prompt="BlueSky handle", help="e.g. yourname.bsky.social")
@click.option("--token", "-p", default=None, help="App password (16-char); will prompt if missing")
@click.option("--days", "-d", default=30, show_default=True,
              help="Delete posts older than this many days")
@click.option("--preview/--no-preview", default=True,
              help="Only show what would be deleted without actually deleting")
@click.option("--log-file", "-l", default=None,
              help="Override log file name (defaults to preview_log.txt or deleted_posts_log.txt)")
@click.option("--match", "-m", multiple=True, help="Only delete posts containing this keyword or matching regex (can be used multiple times)")
@click.option("--regex/--no-regex", default=False, help="Interpret --match patterns as regex")
@click.option("--after", default=None, help="Only consider posts after this date (YYYY-MM-DD or ISO format)")
@click.option("--before", default=None, help="Only consider posts before this date (YYYY-MM-DD or ISO format)")
@click.option("--backup-file", default=None, help="Backup deleted posts to this JSONL file (default: deleted_posts_backup.jsonl)")
@click.option("--include-replies/--exclude-replies", default=False, help="Include replies (default: exclude)")
@click.option("--include-reposts/--exclude-reposts", default=False, help="Include reposts (default: exclude)")
@click.option("--verbose", is_flag=True, default=False, help="Enable verbose output (DEBUG logging)")
@click.option("--quiet", is_flag=True, default=False, help="Suppress most output except errors")
def cli(handle, token, days, preview, log_file, match, regex, after, before, backup_file, include_replies, include_reposts, verbose, quiet):
    # Set logging level based on flags
    if quiet:
        logger.setLevel(logging.ERROR)
    elif verbose:
        logger.setLevel(logging.DEBUG)
    else:
        logger.setLevel(logging.INFO)

    # Control atproto_client HTTP request logs
    atproto_logger = logging.getLogger("atproto_client")
    if verbose:
        atproto_logger.setLevel(logging.DEBUG)
    else:
        atproto_logger.setLevel(logging.WARNING)

    # Warn if token is passed via command line (visible in process list)
    if token and "BYESKY_TOKEN" not in os.environ:
        logger.warning("SECURITY: Passing the app password via --token exposes it in your process list. "
                       "For automation, set the BYESKY_TOKEN environment variable instead.")

    # Prefer environment variable for token if available
    if not token:
        token = os.environ.get("BYESKY_TOKEN")
    if not token:
        token = getpass.getpass("App password: ").strip()

    result = process_posts(
        handle, token, days, preview, log_file,
        match_patterns=match, use_regex=regex,
        after=after, before=before,
        backup_file=backup_file,
        include_replies=include_replies,
        include_reposts=include_reposts,
        quiet=quiet
    )

    # Always show summary, even in quiet mode
    click.echo("\n── Summary ──────────────────────────")
    click.echo(f" Posts scanned   : {result['scanned']}")
    click.echo(f" Posts matched   : {result['matched']}")
    if not preview:
        click.echo(f" Posts deleted   : {result['deleted']}")
        click.echo(f" Delete failures : {result['failed']}")
    click.echo(f" Log file        : {log_file or ('preview_log.txt' if preview else 'deleted_posts_log.txt')}")
    click.echo("──────────────────────────────────────")

    if result["failed"] > 0:
        sys.exit(1)

if __name__ == "__main__":
    cli()