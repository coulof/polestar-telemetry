"""Passwords for the dashboard: `ADMIN_PASSWORD` guards the few things it can change (manual charge costs) and
`DASHBOARD_PASSWORD` optionally puts a login page in front of the whole dashboard (the "viewer" scope).

Each password comes from `.env`; unset means that scope is off (editing stays off, or no login page). The value is
either plain text or an Argon2 hash (`python -m polestar.admin`), which keeps the password itself out of `.env`. A correct
password unlocks one browser session for a while; wrong guesses are limited for the whole process, so opening
new tabs does not reset the counter. The functions take the session state as an argument so they work without
Streamlit."""
import getpass
import hmac
import os
import time

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError

KEY = "admin_until"
UNLOCK_SECONDS = 15 * 60
VIEWER_SECONDS = 12 * 3600
MAX_FAILS = 5
LOCK_SECONDS = 60
SCOPES = {   # scope -> (environment variable, session key, session length in seconds, name in messages)
    "admin": ("ADMIN_PASSWORD", KEY, UNLOCK_SECONDS, "admin"),
    "viewer": ("DASHBOARD_PASSWORD", "viewer_until", VIEWER_SECONDS, "dashboard"),
}
HASHER = PasswordHasher()
_fails = []     # times of recent wrong passwords, shared by every session and scope of this process


def matches(password, stored):
    """Check a guess against the stored value: an Argon2 hash (`$argon2...`) or, for older setups, plain text."""
    if stored.startswith("$argon2"):
        try:
            return HASHER.verify(stored, password)
        except (VerificationError, InvalidHashError):
            return False
    return hmac.compare_digest(password.encode(), stored.encode())


def configured(scope="admin"):
    return bool(os.environ.get(SCOPES[scope][0]))


def unlocked(state, now=None, scope="admin"):
    return configured(scope) and state.get(SCOPES[scope][1], 0) > (time.time() if now is None else now)


def lock(state, scope="admin"):
    state.pop(SCOPES[scope][1], None)


def unlock(state, password, now=None, scope="admin"):
    """Unlock the session if the password is right. Returns None on success, else the reason it failed."""
    env, key, seconds, name = SCOPES[scope]
    now = time.time() if now is None else now
    if not configured(scope):
        return f"No {name} password is set."
    _fails[:] = [t for t in _fails if now - t < LOCK_SECONDS]
    if len(_fails) >= MAX_FAILS:
        return f"Too many wrong attempts. Try again in {int(LOCK_SECONDS - (now - _fails[0])) + 1} s."
    if not matches(password, os.environ[env]):
        _fails.append(now)
        return "Wrong password."
    _fails.clear()
    state[key] = now + seconds
    return None


if __name__ == "__main__":      # python -m polestar.admin: prints the hash to put in .env
    print(HASHER.hash(getpass.getpass("Password: ")))
