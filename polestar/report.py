"""Weekly "hot takes" report written by the Claude Code CLI (`claude -p`, subscription, no API key).

Typical flows:
  local:   python -m polestar.report --generate            (needs `claude` on PATH)
  server:  deploy/weekly-report.sh pipes --prompt -> claude -> --store via podman exec,
           because the container has no CLI."""
import argparse
import datetime as dt
import json
import os
import sqlite3
import subprocess
import sys
import tempfile
from zoneinfo import ZoneInfo

from . import config, stats
from .collect import DB_PATH, connect

PROMPT = """You write the weekly "hot takes" for the owner of a Polestar 2. Below is a JSON summary of \
last week's usage from their own telemetry tracker. All energy figures are estimates and there are no \
personal identifiers.

Write in English, as Markdown, with no preamble and no closing remarks:
- One headline as a level-2 heading that captures the week.
- 3 to 5 bullets: one or two sentences each, opinionated and a little witty, backed by specific numbers \
from the data. Compare with the previous week wherever it has data.
- One final line starting with "Worth watching:" (an anomaly, cost, charging habit, service due or air \
quality), or say nothing stands out.

Rules: use only numbers present in the JSON; never invent trips, places, activities or figures, and do \
not speculate about what the owner did or where the car was; when a value is null \
or a count is zero, say the data is missing instead of guessing; mention that figures are estimated only \
when it matters to a conclusion; money is in euros and distance in km. If the week has almost no data, \
say so in one sentence and give the single most interesting fact available.

JSON:
%s
"""

CLAUDE_FLAGS = ["-p", "--tools", "", "--disable-slash-commands", "--strict-mcp-config",
                "--no-session-persistence", "--output-format", "text"]


def build_prompt(week_stats):
    return PROMPT % json.dumps(week_stats, indent=2)


def exists(db, week):
    return db.execute("SELECT 1 FROM reports WHERE week = ?", (week,)).fetchone() is not None


def store(db, week, markdown, week_stats, model):
    db.execute(
        "INSERT OR REPLACE INTO reports (week, created_at, model, markdown, stats) VALUES (?,?,?,?,?)",
        (week, dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"), model, markdown.strip(),
         json.dumps(week_stats)),
    )
    db.commit()


def list_weeks(db):
    rows = db.execute("SELECT week, created_at, model FROM reports ORDER BY week DESC").fetchall()
    return [{"week": w, "created_at": c, "model": m} for w, c, m in rows]


def get(db, week):
    row = db.execute("SELECT week, created_at, model, markdown, stats FROM reports WHERE week = ?", (week,)).fetchone()
    return dict(zip(("week", "created_at", "model", "markdown", "stats"), row)) if row else None


def run_claude(prompt, model, claude_bin=None, timeout=300):
    """Ask the Claude Code CLI; text only (no tools, skills or MCP), from a neutral directory."""
    cmd = [claude_bin or os.environ.get("CLAUDE_BIN", "claude"), *CLAUDE_FLAGS, "--model", model]
    r = subprocess.run(cmd, input=prompt, capture_output=True, text=True, timeout=timeout, cwd=tempfile.gettempdir())
    if r.returncode != 0 or not r.stdout.strip():
        raise RuntimeError(f"claude exited {r.returncode}: {(r.stderr or r.stdout).strip()[:300]}")
    return r.stdout.strip()


def generate(db, week, model=None, force=False):
    """Create and store the report for `week` with the local `claude` CLI. Returns the markdown."""
    if exists(db, week) and not force:
        return get(db, week)["markdown"]
    model = model or config.load()["report"]["model"]
    week_stats = stats.weekly(db, week)
    markdown = run_claude(build_prompt(week_stats), model)
    store(db, week, markdown, week_stats, model)
    return markdown


def _default_week():
    tz = ZoneInfo(config.load()["report"]["timezone"])
    return stats.last_full_week(dt.datetime.now(tz))


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--week-id", action="store_true", help="print the last full ISO week id")
    g.add_argument("--exists", metavar="WEEK", help="exit 0 if a report for WEEK is stored, else 1")
    g.add_argument("--prompt", metavar="WEEK", help="print the prompt (stats included) for WEEK")
    g.add_argument("--store", metavar="WEEK", help="store markdown read from stdin as WEEK's report")
    g.add_argument("--generate", action="store_true", help="generate with the local claude CLI")
    ap.add_argument("--week", help="with --generate: ISO week (default: last full week)")
    ap.add_argument("--model", help="model alias for the CLI (default: [report] model in config.toml)")
    ap.add_argument("--force", action="store_true", help="with --generate: replace an existing report")
    args = ap.parse_args()
    db = connect(DB_PATH)

    if args.week_id:
        print(_default_week())
    elif args.exists:
        sys.exit(0 if exists(db, args.exists) else 1)
    elif args.prompt:
        print(build_prompt(stats.weekly(db, args.prompt)))
    elif args.store:
        markdown = sys.stdin.read()
        if not markdown.strip():
            sys.exit("empty report, nothing stored")
        store(db, args.store, markdown, stats.weekly(db, args.store), args.model or config.load()["report"]["model"])
        print(f"stored {args.store}")
    else:
        week = args.week or _default_week()
        generate(db, week, args.model, args.force)
        print(f"stored {week}")


if __name__ == "__main__":
    main()
