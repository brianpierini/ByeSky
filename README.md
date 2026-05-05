# ByeSky

![Demo of ByeSky in action](media/byesky_demo.gif)

ByeSky is a CLI tool to delete Bluesky / AT Protocol posts older than a specified number of days, with advanced filtering, backup, preview, and automation options.

Supports both **Bluesky (bsky.social)** and **self-hosted PDS** instances.

## Motivation

Opinions change, trends fade, and not every thought needs to live online forever. ByeSky gives you control over your post history — safely preview what would be deleted, then remove it.

## Features

- **AT Protocol spec-compliant** — uses `com.atproto.repo.listRecords` and `com.atproto.repo.deleteRecord` directly, no AppView dependency
- **Self-hosted PDS support** — point `--pds` at any AT Protocol PDS
- **Preview mode** — see exactly what would be deleted before committing
- **Advanced filtering** — by age, date range, keyword, regex, replies, and native reposts
- **Backup** — every deleted post is saved to a JSONL file before removal
- **Rate-limit aware** — configurable delay between deletions
- **Automation-friendly** — quiet mode, env-var auth, non-zero exit codes
- **Verbose and quiet modes**
- **Cron-job friendly**

## What's New in v0.2.0

- **Self-hosted PDS support** via `--pds` — works with any AT Protocol-compliant server
- **Spec-compliant API** — switched from `app.bsky.feed.getAuthorFeed` to `com.atproto.repo.listRecords`, removing the AppView dependency
- **DID-based operations** — all repo calls now use your DID (resolved at login) instead of your handle
- **Fixed repost detection** — native reposts are fetched from the `app.bsky.feed.repost` collection; previously only embedded reposts were checked
- **Fixed reply detection** — checks the `reply` field on raw record values rather than the feed view
- **Rate-limit buffer** — configurable `--delete-delay` (default 0.5 s) between deletions
- **Improved retry logic** — 5 attempts with 2–60 s exponential back-off (up from 3 attempts)
- **Accurate scan count** — counts every record scanned rather than estimating from page count

## Disclaimer

**Warning:** Deletion is irreversible. Always run with `--preview` first and review the log before using `--no-preview`.

The author is **not responsible** for any data loss or unintended consequences.

## Installation

1. Clone this repo:
    ```zsh
    git clone https://github.com/brianpierini/ByeSky.git
    cd ByeSky
    ```

2. [Create a Bluesky app password](https://bsky.app/settings/app-passwords).

### Option 1: Virtual Environment (Recommended)

```zsh
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
python byesky.py --handle yourhandle.bsky.social --days 30 --preview
```

### Option 2: pipx (macOS-friendly, installs globally without conflicts)

```zsh
brew install pipx
pipx install .
byesky --handle yourhandle.bsky.social --days 30 --preview
```

### Option 3: Direct (not recommended)

```zsh
pip3 install -r requirements.txt --break-system-packages
```

> Requires Python 3.8+. Compatible with Pydantic v2+.

## Quick Start

```zsh
# Preview posts older than 30 days (default — nothing is deleted)
python3 byesky.py --handle alice.bsky.social --days 30 --preview

# Actually delete them
python3 byesky.py --handle alice.bsky.social --days 30 --no-preview

# Self-hosted PDS
python3 byesky.py --handle alice.example.com --pds https://pds.example.com --days 30 --preview
```

You will be prompted for your app password, or set it via `BYESKY_TOKEN`.

> **zsh tip:** Always quote the `--pds` URL to prevent zsh from interpreting `://` as a glob:
> ```zsh
> python3 byesky.py --handle alice.example.com --pds "https://pds.example.com" --days 30 --preview
> ```

> **Custom domain handles:** If your handle is a custom domain (e.g. `alice.example.com` rather than `alice.bsky.social`), ByeSky resolves it to your DID automatically — just pass the handle as-is.

## Usage

```zsh
python3 byesky.py [OPTIONS]
```

### Authentication

| Option | Description |
|--------|-------------|
| `--handle`, `-u` | Your AT Protocol handle (e.g. `alice.bsky.social`) |
| `--token`, `-p` | App password — reads `BYESKY_TOKEN` env var if not provided, then prompts |

**Tip:** For automation, set `BYESKY_TOKEN` in your environment instead of using `--token`.

### PDS / Server

| Option | Default | Description |
|--------|---------|-------------|
| `--pds` | `https://bsky.social` | AT Protocol PDS base URL |

For a self-hosted PDS, set `--pds https://your-pds.example.com`. Your handle and app password are resolved against that PDS.

### Filtering

| Option | Default | Description |
|--------|---------|-------------|
| `--days`, `-d` | `30` | Target posts older than this many days |
| `--after DATE` | — | Only target posts on or after this date (YYYY-MM-DD or ISO 8601) |
| `--before DATE` | — | Only target posts on or before this date |
| `--match`, `-m` | — | Only target posts containing this keyword (repeatable) |
| `--regex/--no-regex` | off | Treat `--match` values as regular expressions |
| `--include-replies/--exclude-replies` | exclude | Include reply posts |
| `--include-reposts/--exclude-reposts` | exclude | Include native reposts (`app.bsky.feed.repost` records) |

### Preview & Safety

| Option | Default | Description |
|--------|---------|-------------|
| `--preview/--no-preview` | `--preview` | Dry run — show what would be deleted without deleting |

**Always preview first.** Deletion is permanent.

### Output & Logging

| Option | Default | Description |
|--------|---------|-------------|
| `--log-file`, `-l` | auto | Log filename (default: `preview_log.txt` or `deleted_posts_log.txt`) |
| `--backup-file` | `deleted_posts_backup.jsonl` | JSONL file for deleted-post backups |
| `--verbose` | off | Enable DEBUG logging |
| `--quiet` | off | Suppress all output except errors and the final summary |

### Rate Limiting

| Option | Default | Description |
|--------|---------|-------------|
| `--delete-delay` | `0.5` | Seconds between deletions (rate-limit buffer) |

Bluesky's write rate limits are roughly 1,666 requests per 5 minutes. The default 0.5 s delay (~120 deletes/min) stays safely within that.

## Examples

```zsh
# Preview posts older than 6 months
python3 byesky.py --handle alice.bsky.social --days 180 --preview

# Delete posts older than 30 days (cron-friendly, token via env var)
BYESKY_TOKEN=xxxx-xxxx-xxxx-xxxx \
  python3 byesky.py --handle alice.bsky.social --days 30 --no-preview --quiet

# Delete posts matching a keyword, including replies, with backup
python3 byesky.py --handle alice.bsky.social --no-preview \
  --match "old opinion" --include-replies --backup-file backup.jsonl

# Regex filter: posts starting with "hot take"
python3 byesky.py --handle alice.bsky.social --preview \
  --match '^hot take' --regex

# Delete posts in a date range
python3 byesky.py --handle alice.bsky.social --no-preview \
  --after 2024-01-01 --before 2024-06-30

# Self-hosted PDS
python3 byesky.py --handle alice.example.com \
  --pds https://pds.example.com --days 30 --preview
```

## Self-Hosted PDS

If you run your own AT Protocol PDS, pass its base URL with `--pds`:

```zsh
python3 byesky.py \
  --handle alice.example.com \
  --pds "https://pds.example.com" \
  --days 30 \
  --preview
```

ByeSky communicates directly with your PDS using the `com.atproto.repo.*` lexicons — no dependency on Bluesky's AppView or any Bluesky-specific infrastructure.

### Finding your PDS URL

If you're not sure what your PDS URL is, you can look it up from your handle:

```zsh
# Step 1 — resolve your handle to a DID
curl -s "https://bsky.social/xrpc/com.atproto.identity.resolveHandle?handle=alice.example.com"

# Step 2 — look up the DID document to find your PDS serviceEndpoint
curl -s "https://plc.directory/<your-did>" | python3 -m json.tool
```

Look for `"serviceEndpoint"` under `"#atproto_pds"` in the DID document — that is your PDS URL.

### App passwords on self-hosted PDS

You need an app password from your PDS, separate from any Bluesky app passwords. Check your PDS's settings or account page. If your PDS does not expose an app password UI, you can create one via the API:

```zsh
# Get an access token
curl -s -X POST "https://pds.example.com/xrpc/com.atproto.server.createSession" \
  -H "Content-Type: application/json" \
  -d '{"identifier":"alice.example.com","password":"YOUR_PASSWORD"}' \
  | python3 -c "import sys,json; print(json.load(sys.stdin)['accessJwt'])"

# Create an app password
curl -s -X POST "https://pds.example.com/xrpc/com.atproto.server.createAppPassword" \
  -H "Authorization: Bearer <accessJwt>" \
  -H "Content-Type: application/json" \
  -d '{"name":"byesky"}' \
  | python3 -m json.tool
```

## How It Works

ByeSky uses the AT Protocol lexicons directly:

| Operation | Lexicon |
|-----------|---------|
| Authentication | `com.atproto.server.createSession` |
| Listing posts | `com.atproto.repo.listRecords` (`app.bsky.feed.post`) |
| Listing reposts | `com.atproto.repo.listRecords` (`app.bsky.feed.repost`) |
| Deleting | `com.atproto.repo.deleteRecord` |

All repository operations use your DID (resolved at login) rather than your handle, which is the spec-correct identifier for repo operations.

## Exit Codes

| Code | Meaning |
|------|---------|
| `0` | Success |
| `1` | One or more deletions failed |
| `2` | Safety check failed (e.g. `--days 0` with `--no-preview`) or login failed |
| `3` | File I/O error |
| `99` | Unexpected error |

## Troubleshooting

### Login fails

- Confirm you are using an **app password**, not your main Bluesky password.
- For self-hosted PDS: verify the `--pds` URL is correct and reachable.
- Run with `--verbose` to see the full HTTP exchange.

### `'NoneType' object is not callable` / Pydantic errors

```zsh
source venv/bin/activate
pip install --upgrade pydantic atproto
```

### Missing dependencies

```zsh
source venv/bin/activate
pip install -r requirements.txt
```

### macOS externally managed Python

```zsh
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
python byesky.py --help
```

### Too many deletion failures

Lower `--delete-delay` or increase it if you're hitting rate limits. Failures are retried up to 5 times with exponential back-off.

## Security

- Use an **app password**, not your main password.
- Store the token in `BYESKY_TOKEN` or a secrets manager — never hard-code it.
- ByeSky never prints or logs your token.

## License

MIT — Copyright 2025 Brian Pierini
