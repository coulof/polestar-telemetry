"""Admin password for the few things the dashboard can change (manual charge costs).

The password is `ADMIN_PASSWORD` in `.env`. Without it editing stays off. A correct password unlocks one
browser session for a while; wrong guesses are limited for the whole process, so opening new tabs does not
reset the counter. The functions take the session state as an argument so they work without Streamlit."""
import hmac
import os
import time

KEY = "admin_until"
UNLOCK_SECONDS = 15 * 60
MAX_FAILS = 5
LOCK_SECONDS = 60
_fails = []     # times of recent wrong passwords, shared by every session of this process


def configured():
    return bool(os.environ.get("ADMIN_PASSWORD"))


def unlocked(state, now=None):
    return configured() and state.get(KEY, 0) > (time.time() if now is None else now)


def lock(state):
    state.pop(KEY, None)


def unlock(state, password, now=None):
    """Unlock the session if the password is right. Returns None on success, else the reason it failed."""
    now = time.time() if now is None else now
    if not configured():
        return "No admin password is set."
    _fails[:] = [t for t in _fails if now - t < LOCK_SECONDS]
    if len(_fails) >= MAX_FAILS:
        return f"Too many wrong attempts. Try again in {int(LOCK_SECONDS - (now - _fails[0])) + 1} s."
    if not hmac.compare_digest(password.encode(), os.environ["ADMIN_PASSWORD"].encode()):
        _fails.append(now)
        return "Wrong password."
    _fails.clear()
    state[KEY] = now + UNLOCK_SECONDS
    return None
