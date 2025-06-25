# ByeSky

ByeSky is a CLI tool to delete BlueSky posts older than a specified number of days, with advanced filtering, backup, preview and automation options.

## Motivation

I believe that opinions change, trends fade, and not every thought or post needs to live online forever. As someone who values privacy, I wanted a tool that empowers users to easily and safely clean up their BlueSky history giving them control over what remains public. ByeSky is designed to make it simple to review, filter, and remove old posts.

## Features

- Export logs
- Verbose and quiet modes
- Cron job friendly
- Advanced filtering (date, keyword, regex, replies, reposts)
- Backup deleted posts

## Disclaimer

**Warning:** This tool performs irreversible data deletion. Use with caution.  
Double-check your filters and options before running by using `--preview`.  

**The `--preview` option will only show what would be deleted and will NOT delete any posts.**  

**The `--no-preview` option will actually delete the matching posts.**  

The author is **not responsible** for any data loss or unintended consequences.

## Installation

1. Clone this repo and install dependencies:
    ```zsh
    git clone https://github.com/brianpierini/ByeSky.git
    cd ByeSky
    pip install -r requirements.txt
    ```

2. [Create a BlueSky app password](https://bsky.app/settings/app-passwords).

> **Note:**  
> ByeSky is compatible with Pydantic v2 and newer.  
> If you see errors about `.dict()`, upgrade Pydantic:  
> `pip install --upgrade pydantic`  
> Requires Python 3.8 or newer (recommended).

## Quick Start

```zsh
python3 byesky.py --handle johnappleseed@bsky.social --days 30 --preview
```

- By default, this will **preview** posts older than 30 days.
- To actually delete, add `--no-preview`.
- You will be prompted for your app password, or set it via the `BYESKY_TOKEN` environment variable.

## Usage

```zsh
python3 byesky.py [OPTIONS]
```

### Required

- `--handle`, `-u`  
  Your BlueSky handle (e.g., `johnappleseed@bsky.social`).

### Authentication

- `--token`, `-p`  
  Your BlueSky app password (16 chars). If omitted, you will be prompted.
  **Tip:** For automation, set the `BYESKY_TOKEN` environment variable.

### Age Filtering

- `--days`, `-d`  
  Delete posts older than this many days.  
  Example:  
  ```
  python3 byesky.py --handle johnappleseed@bsky.social --days 60 --preview
  ```

### Preview Mode

- `--preview/--no-preview`  
  Only show what would be deleted, do not actually delete.  
  Default: `--preview`  
  Example:  
  ```
  python3 byesky.py --handle johnappleseed@bsky.social --days 30 --preview
  ```

### Logging

- `--log-file`, `-l`  
  Override log file name.  
  Example:  
  ```
  python3 byesky.py --handle johnappleseed@bsky.social --days 30 --log-file mylog.txt
  ```

### Keyword/Regex Filtering

- `--match`, `-m`  
  Only delete posts containing this keyword or matching regex. Can be used multiple times.  
  Example (keyword):  
  ```
  python3 byesky.py --handle johnappleseed@bsky.social --days 30 --match hello --match world
  ```
  Example (regex):  
  ```
  python3 byesky.py --handle johnappleseed@bsky.social --days 30 --match '^foo.*bar$' --regex
  ```

- `--regex/--no-regex`  
  Interpret `--match` patterns as regex.  
  Example:  
  ```
  python3 byesky.py --handle johnappleseed@bsky.social --days 30 --match 'test\d+' --regex
  ```

### Date Range Filtering

- `--after`  
  Only consider posts after this date (inclusive).  
  Example:  
  ```
  python3 byesky.py --handle johnappleseed@bsky.social --after 2024-01-01 --preview
  ```

- `--before`  
  Only consider posts before this date (inclusive).  
  Example:  
  ```
  python3 byesky.py --handle johnappleseed@bsky.social --before 2024-06-01 --preview
  ```

### Backup

- `--backup-file`  
  Backup deleted posts to this JSONL file (default: `deleted_posts_backup.jsonl`).  
  Example:  
  ```
  python3 byesky.py --handle johnappleseed@bsky.social --no-preview --backup-file my_backup.jsonl
  ```

### Replies and Reposts

- `--include-replies/--exclude-replies`  
  Include or exclude replies (default: exclude).  
  Example:  
  ```
  python3 byesky.py --handle johnappleseed@bsky.social --preview --include-replies
  ```

- `--include-reposts/--exclude-reposts`  
  Include or exclude reposts (default: exclude).  
  Example:  
  ```
  python3 byesky.py --handle johnappleseed@bsky.social --no-preview --include-reposts
  ```

### Output Modes

- `--verbose`  
  Enable verbose output (DEBUG logging, show HTTP requests).  
  Example:  
  ```
  python3 byesky.py --handle johnappleseed@bsky.social --preview --verbose
  ```

- `--quiet`  
  Suppress most output except errors and summary.  
  Progress bars are still shown in quiet mode.  
  HTTP request logs are suppressed in quiet mode.  
  Example:  
  ```
  python3 byesky.py --handle johnappleseed@bsky.social --no-preview --quiet
  ```

### Example: Delete posts older than 6 months (for cron job)

```zsh
python3 byesky.py --handle johnappleseed@bsky.social --token YOUR_APP_PASSWORD --no-preview --days 180 --quiet
```

### Example: Delete posts after a certain date, including replies, with backup

```zsh
python3 byesky.py --handle johnappleseed@bsky.social --no-preview --after 2024-01-01 --include-replies --backup-file backup.jsonl
```

## Security

- Use an app password, not your main BlueSky password.
- Consider using environment variables or a secrets manager for automation.

## License

MIT
