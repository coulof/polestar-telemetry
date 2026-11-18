"""Names you give to places on the Path tab, instead of "Place 1", "Place 2".

A place is found by grouping parked positions, so its number can change as history grows. A label is therefore keyed by
the position that started the group, rounded to about 11 m (path.seed_key): the first reading of a group never changes."""
import datetime as dt

MAX_LEN = 40


def load(db):
    """{seed key: label} of every named place."""
    return dict(db.execute("SELECT seed, label FROM place_labels"))


def set_label(db, seed, label):
    """Name the place, replacing an earlier label."""
    label = " ".join(str(label).split())
    if not label:
        raise ValueError("the label cannot be empty")
    if len(label) > MAX_LEN:
        raise ValueError(f"the label can have at most {MAX_LEN} characters")
    db.execute("INSERT OR REPLACE INTO place_labels (seed, label, updated_at) VALUES (?,?,?)",
               (seed, label, dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")))
    db.commit()


def clear(db, seed):
    """Drop a label: the place goes back to its automatic name."""
    db.execute("DELETE FROM place_labels WHERE seed = ?", (seed,))
    db.commit()
