"""Manual cost corrections for charge sessions: what you actually paid beats the tariff estimate.

Sessions are derived on read, so a correction is keyed by the session's start time (epoch seconds, UTC)."""
import datetime as dt


def key(start):
    """Stable id of a session: its start time as whole epoch seconds."""
    return int(start.timestamp())


def load(db):
    """{start_ts: cost in EUR} of every corrected session."""
    return {int(ts): float(cost) for ts, cost in db.execute("SELECT start_ts, cost FROM session_costs")}


def set_cost(db, start_ts, cost):
    """Store the total paid for the session (energy and any fee), replacing an earlier correction."""
    cost = float(cost)
    if cost != cost or cost < 0:
        raise ValueError("cost must be a number of 0 or more")
    db.execute("INSERT OR REPLACE INTO session_costs (start_ts, cost, updated_at) VALUES (?,?,?)",
               (int(start_ts), cost, dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")))
    db.commit()


def clear(db, start_ts):
    """Drop a correction: the session goes back to its tariff estimate."""
    db.execute("DELETE FROM session_costs WHERE start_ts = ?", (int(start_ts),))
    db.commit()
