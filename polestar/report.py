"""Weekly, monthly, quarterly and yearly "hot takes" written by the Claude Code CLI (`claude -p`, subscription,
no API key). A report is keyed by its period id (2026-W47, 2026-11, 2026-Q4, 2026; see stats.py).

Typical flows:
  local:   python -m polestar.report --generate [--period ID]   (needs `claude` on PATH)
  server:  deploy/report.sh pipes --prompt -> claude -> --store via podman exec for every id --due prints,
           because the container has no CLI."""
import argparse
import datetime as dt
import json
import os
import re
import subprocess
import sys
import tempfile
from zoneinfo import ZoneInfo

from . import config, stats
from .collect import DB_PATH, connect

PROMPT = """You write the %(adj)s "hot takes" for the owner of a Polestar 2. Below is a JSON summary of \
the last %(unit)s's usage from their own telemetry tracker. All energy figures are estimates and there are no \
personal identifiers.

Write in English, as Markdown, with no preamble and no closing remarks:
- One headline as a level-2 heading that captures the %(unit)s.
- 3 to 5 bullets: one or two sentences each, opinionated and a little witty, backed by specific numbers \
from the data. Compare with the previous %(unit)s wherever it has data.%(trend)s
- One final line starting with "Worth watching:" (an anomaly, cost, charging habit, service due or air \
quality), or say nothing stands out.

Rules: use only numbers present in the JSON; never invent trips, places, activities or figures, and do \
not speculate about what the owner did or where the car was; when a value is null \
or a count is zero, say the data is missing instead of guessing; mention that figures are estimated only \
when it matters to a conclusion; money is in euros and distance in km. If the week has almost no data, \
say so in one sentence and give the single most interesting fact available.

JSON:
%(json)s
"""
ADJ = {"week": "weekly", "month": "monthly", "quarter": "quarterly", "year": "yearly"}
TREND = " Use by_month to point out the trend across the months."

JUDGE_PROMPT = """You grade a short %(adj)s report written for the owner of a Polestar 2 from the JSON summary below. \
Score it from 1 to 10 for how much it is worth reading, weighing equally:
- insight: it points out something useful or non-obvious (a cost lever, an anomaly, a habit worth changing);
- trend: it compares with the previous %(unit)s, or across months when by_month is given;
- fun: a strong headline, some wit, no filler.
Any figure that the JSON does not support, directly or by simple arithmetic, caps the score at 3.

Answer with JSON only, no other text: {"score": <integer 1-10>, "reason": "<one sentence, at most 25 words>"}

REPORT:
%(markdown)s

JSON:
%(json)s
"""

CLAUDE_FLAGS = ["-p", "--tools", "", "--disable-slash-commands", "--strict-mcp-config",
                "--no-session-persistence", "--output-format", "text"]


def build_prompt(period_stats):
    kind = period_stats.get("kind", "week")
    return PROMPT % {"adj": ADJ[kind], "unit": kind, "trend": TREND if "by_month" in period_stats else "",
                     "json": json.dumps(period_stats, indent=2)}


def has_data(period_stats):
    """The data gate: a period with no trips and no charge sessions gets no report and is never the best."""
    return bool((period_stats.get("driving") or {}).get("trips") or (period_stats.get("charging") or {}).get("sessions"))


def judge_prompt(db, pid):
    r = get(db, pid)
    st_ = json.loads(r["stats"])
    kind = stats.kind_of(pid)
    return JUDGE_PROMPT % {"adj": ADJ[kind], "unit": kind, "markdown": r["markdown"], "json": json.dumps(st_, indent=2)}


def parse_grade(text):
    """(score 1-10, reason) from the judge's answer; raises ValueError when it holds no usable grade."""
    m = re.search(r"\{.*?\}", text or "", re.S)
    if not m:
        raise ValueError("no JSON object in the answer")
    g = json.loads(m.group(0))
    score = max(1, min(10, int(round(float(g["score"])))))
    return score, " ".join(str(g.get("reason", "")).split())[:200]


def store_grade(db, pid, score, reason):
    db.execute("UPDATE reports SET score = ?, score_note = ? WHERE week = ?", (score, reason, pid))
    db.commit()


def ungraded(db):
    return [r[0] for r in db.execute("SELECT week FROM reports WHERE score IS NULL ORDER BY week")]


def best(db, kind):
    """The best report of `kind` as {week, score, score_note}: the highest grade among reports that pass the data gate,
    the newest on a tie. Without any grade, the latest report with score None. None when there is no report."""
    reports = list_reports(db, kind)
    if not reports:
        return None
    rows = {w: (sc, note, st_) for w, sc, note, st_ in db.execute("SELECT week, score, score_note, stats FROM reports")}
    graded = [r for r in reports if rows[r["week"]][0] is not None and has_data(json.loads(rows[r["week"]][2]))]
    pick = max(graded, key=lambda r: rows[r["week"]][0]) if graded else reports[0]   # reports are newest first: max keeps the first
    return {"week": pick["week"], "score": rows[pick["week"]][0] if graded else None,
            "score_note": rows[pick["week"]][1] if graded else None}


def exists(db, week):
    return db.execute("SELECT 1 FROM reports WHERE week = ?", (week,)).fetchone() is not None


def store(db, week, markdown, week_stats, model):
    db.execute(
        "INSERT OR REPLACE INTO reports (week, created_at, model, markdown, stats) VALUES (?,?,?,?,?)",
        (week, dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"), model, markdown.strip(),
         json.dumps(week_stats)),
    )
    db.execute("DELETE FROM report_failures WHERE week = ?", (week,))     # a stored report ends the failure
    db.commit()


def record_failure(db, week, error):
    """Remember that the report for `week` could not be written (shown on the Report tab)."""
    db.execute("INSERT OR REPLACE INTO report_failures (week, failed_at, error) VALUES (?,?,?)",
               (week, dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"), error.strip()[:300]))
    db.commit()


def _of_kind(pid, kind):
    try:
        return kind is None or stats.kind_of(pid) == kind
    except ValueError:
        return False


def latest_failure(db, kind=None):
    """The newest failure (of `kind`, or any) whose period still has no report, as {week, failed_at, error}."""
    rows = db.execute("SELECT week, failed_at, error FROM report_failures "
                      "WHERE week NOT IN (SELECT week FROM reports) ORDER BY failed_at DESC").fetchall()
    row = next((r for r in rows if _of_kind(r[0], kind)), None)
    return dict(zip(("week", "failed_at", "error"), row)) if row else None


def list_reports(db, kind=None):
    """Stored reports (of `kind`, or all), newest period first. The `week` key holds the period id."""
    rows = db.execute("SELECT week, created_at, model FROM reports").fetchall()
    out = [{"week": w, "created_at": c, "model": m} for w, c, m in rows if _of_kind(w, kind)]
    return sorted(out, key=lambda r: stats.bounds(r["week"])[1], reverse=True)


def list_weeks(db):
    return list_reports(db, "week")


def due(db, now):
    """Ids of the last full week, month, quarter and year that have no report yet and have data (see has_data)."""
    return [pid for pid in (stats.last_full(k, now) for k in stats.KINDS)
            if not exists(db, pid) and has_data(stats.summary(db, pid))]


def get(db, week):
    row = db.execute("SELECT week, created_at, model, markdown, stats, score, score_note FROM reports WHERE week = ?",
                     (week,)).fetchone()
    return dict(zip(("week", "created_at", "model", "markdown", "stats", "score", "score_note"), row)) if row else None


def run_claude(prompt, model, claude_bin=None, timeout=300):
    """Ask the Claude Code CLI; text only (no tools, skills or MCP), from a neutral directory."""
    cmd = [claude_bin or os.environ.get("CLAUDE_BIN", "claude"), *CLAUDE_FLAGS, "--model", model]
    r = subprocess.run(cmd, input=prompt, capture_output=True, text=True, timeout=timeout, cwd=tempfile.gettempdir())
    if r.returncode != 0 or not r.stdout.strip():
        raise RuntimeError(f"claude exited {r.returncode}: {(r.stderr or r.stdout).strip()[:300]}")
    return r.stdout.strip()


def generate(db, pid, model=None, force=False):
    """Create and store the report for period `pid` with the local `claude` CLI. Returns the markdown."""
    if exists(db, pid) and not force:
        return get(db, pid)["markdown"]
    model = model or config.load()["report"]["model"]
    period_stats = stats.summary(db, pid)
    markdown = run_claude(build_prompt(period_stats), model)
    store(db, pid, markdown, period_stats, model)
    return markdown


def _now():
    return dt.datetime.now(ZoneInfo(config.load()["report"]["timezone"]))


def _default_week():
    return stats.last_full_week(_now())


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--week-id", action="store_true", help="print the last full ISO week id")
    g.add_argument("--due", action="store_true", help="print the ids of the last full week, month, quarter and year "
                                                      "that have no report yet, one per line")
    g.add_argument("--ungraded", action="store_true", help="print the ids of stored reports that have no grade yet")
    g.add_argument("--judge-prompt", metavar="ID", help="print the grading prompt for period ID's stored report")
    g.add_argument("--store-grade", metavar="ID", help="store the judge's JSON answer read from stdin as period ID's grade")
    g.add_argument("--exists", metavar="ID", help="exit 0 if a report for period ID is stored, else 1")
    g.add_argument("--prompt", metavar="ID", help="print the prompt (stats included) for period ID")
    g.add_argument("--store", metavar="ID", help="store markdown read from stdin as period ID's report")
    g.add_argument("--failed", metavar="ID", help="record the failure reason read from stdin for period ID")
    g.add_argument("--generate", action="store_true", help="generate with the local claude CLI")
    ap.add_argument("--period", "--week", dest="period", help="with --generate: period id (default: last full week)")
    ap.add_argument("--model", help="model alias for the CLI (default: [report] model in config.toml)")
    ap.add_argument("--force", action="store_true", help="with --generate: replace an existing report")
    args = ap.parse_args()
    db = connect(DB_PATH)

    if args.week_id:
        print(_default_week())
    elif args.due:
        print("\n".join(due(db, _now())))
    elif args.ungraded:
        print("\n".join(ungraded(db)))
    elif args.judge_prompt:
        print(judge_prompt(db, args.judge_prompt))
    elif args.store_grade:
        try:
            score, reason = parse_grade(sys.stdin.read())
        except (ValueError, KeyError, TypeError) as e:
            sys.exit(f"unusable grade: {e}")
        store_grade(db, args.store_grade, score, reason)
        print(f"graded {args.store_grade}: {score}/10")
    elif args.exists:
        sys.exit(0 if exists(db, args.exists) else 1)
    elif args.prompt:
        print(build_prompt(stats.summary(db, args.prompt)))
    elif args.failed:
        record_failure(db, args.failed, sys.stdin.read() or "unknown error")
        print(f"recorded failure for {args.failed}")
    elif args.store:
        markdown = sys.stdin.read()
        if not markdown.strip():
            sys.exit("empty report, nothing stored")
        store(db, args.store, markdown, stats.summary(db, args.store), args.model or config.load()["report"]["model"])
        print(f"stored {args.store}")
    else:
        pid = args.period or _default_week()
        generate(db, pid, args.model, args.force)
        print(f"stored {pid}")


if __name__ == "__main__":
    main()
