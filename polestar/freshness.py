"""How long ago the collector last got data from the API, for deploy/freshness.sh.

`python -m polestar.freshness` prints the age in whole seconds, or `none` before the first successful poll.
This is the time of the last poll that fetched anything, not the newest event: a parked car can send no
new events for hours while the collector is healthy."""
import time

from polestar.collect import DB_PATH, connect


def age_seconds(db, now=None):
    row = db.execute("SELECT last_ok FROM poll_status WHERE id = 1").fetchone()
    return None if row is None else max(0, int((time.time() if now is None else now) - row[0]))


def main():
    age = age_seconds(connect(DB_PATH))
    print("none" if age is None else age)


if __name__ == "__main__":
    main()
