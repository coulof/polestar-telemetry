"""The host scripts, run against fake podman, claude and curl: failures must be recorded and notified."""
import os
import stat
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent

FAKE_PODMAN = r"""#!/usr/bin/env bash
echo "podman $*" >> "$LOG"
[ "${PODMAN_FAIL:-}" = 1 ] && exit 1
case "$*" in
  *--week-id*) echo 2026-W20 ;;
  *--exists*) exit 1 ;;
  *--prompt*) echo "PROMPT" ;;
  *"python -c"*"enabled"*) [ "${REPORT_OFF:-}" = 1 ] && echo False || echo True ;;
  *"python -c"*"config"*) echo sonnet ;;
  *--failed*|*--store*) echo "stdin: $(cat)" >> "$LOG" ;;
esac
"""
FAKE_CLAUDE = r"""#!/usr/bin/env bash
cat > /dev/null
[ "${CLAUDE_FAIL:-}" = 1 ] && exit 1
[ "${CLAUDE_EMPTY:-}" = 1 ] && exit 0
echo "## A week"
"""
FAKE_CURL = r"""#!/usr/bin/env bash
echo "curl $*" >> "$LOG"
"""


@pytest.fixture
def env(tmp_path):
    bindir = tmp_path / "bin"
    bindir.mkdir()
    for name, body in (("podman", FAKE_PODMAN), ("claude", FAKE_CLAUDE), ("curl", FAKE_CURL)):
        (bindir / name).write_text(body)
        (bindir / name).chmod(stat.S_IRWXU)
    log = tmp_path / "calls.log"
    base = {"PATH": f"{bindir}:{os.environ['PATH']}", "HOME": str(tmp_path), "LOG": str(log), "CLAUDE_BIN": str(bindir / "claude")}

    def run(script, *args, **extra):
        r = subprocess.run(["bash", str(ROOT / "deploy" / script), *args], env={**base, **extra}, capture_output=True, text=True)
        return r, (log.read_text() if log.exists() else "")
    return run


def test_report_success_stores_and_does_not_notify(env):
    r, log = env("weekly-report.sh", NOTIFY_URL="https://ntfy.test/topic")
    assert r.returncode == 0
    assert "--store 2026-W20" in log and "curl" not in log and "--failed" not in log


def test_disabled_integration_skips_claude_and_stays_quiet(env):
    r, log = env("weekly-report.sh", NOTIFY_URL="https://ntfy.test/topic", REPORT_OFF="1", CLAUDE_FAIL="1")
    assert r.returncode == 0 and "disabled" in r.stdout
    assert "--store" not in log and "--failed" not in log and "curl" not in log


def test_claude_failure_is_recorded_and_notified(env):
    r, log = env("weekly-report.sh", NOTIFY_URL="https://ntfy.test/topic", CLAUDE_FAIL="1")
    assert r.returncode == 1
    assert "--failed 2026-W20" in log and "failed while asking Claude" in log
    assert "curl" in log and "https://ntfy.test/topic" in log and "Title: Polestar weekly report failed" in log
    assert "--store" not in log
    assert "failed while asking Claude" in r.stderr


def test_empty_answer_counts_as_a_failure(env):
    r, log = env("weekly-report.sh", CLAUDE_EMPTY="1")
    assert r.returncode == 1 and "(it was empty)" in log and "--store" not in log


def test_without_a_notify_url_the_failure_is_only_recorded(env):
    r, log = env("weekly-report.sh", CLAUDE_FAIL="1")
    assert r.returncode == 1 and "--failed 2026-W20" in log and "curl" not in log


def test_container_down_notifies_even_though_it_cannot_record(env):
    r, log = env("weekly-report.sh", NOTIFY_URL="https://ntfy.test/topic", PODMAN_FAIL="1")
    assert r.returncode != 0
    assert "finding the week" in log and "curl" in log and "--failed" not in log


def test_a_notification_never_carries_database_content(env):
    r, log = env("weekly-report.sh", NOTIFY_URL="https://ntfy.test/topic", CLAUDE_FAIL="1")
    assert "PROMPT" not in log.split("curl", 1)[1]


def test_backup_failure_notifies(env, tmp_path):
    r, log = env("backup.sh", str(tmp_path / "dest"), NOTIFY_URL="https://ntfy.test/topic", PODMAN_FAIL="1")
    assert r.returncode != 0 and "Polestar backup failed" in log and "https://ntfy.test/topic" in log
