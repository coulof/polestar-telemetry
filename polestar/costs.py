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


MAX_LOCATION = 40


def load_locations(db):
    """{start_ts: location name} of every session whose location was set by hand."""
    return {int(ts): name for ts, name in db.execute("SELECT start_ts, location FROM session_locations")}


def set_location(db, start_ts, name):
    """Say where the session really was: a config.toml location name (then priced with its tariff and fee) or any
    other label (priced at the default price). Replaces an earlier one."""
    name = " ".join(str(name).split())
    if not name:
        raise ValueError("the location cannot be empty")
    if len(name) > MAX_LOCATION:
        raise ValueError(f"the location can have at most {MAX_LOCATION} characters")
    db.execute("INSERT OR REPLACE INTO session_locations (start_ts, location, updated_at) VALUES (?,?,?)",
               (int(start_ts), name, dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")))
    db.commit()


def clear_location(db, start_ts):
    """Drop a location: the session goes back to the one detected from the car's last position."""
    db.execute("DELETE FROM session_locations WHERE start_ts = ?", (int(start_ts),))
    db.commit()
