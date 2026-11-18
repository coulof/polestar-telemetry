import pytest

from polestar import collect, place_labels


@pytest.fixture
def db(tmp_path):
    d = collect.connect(str(tmp_path / "t.db"))
    yield d
    d.close()


def test_a_label_can_be_set_replaced_and_cleared(db):
    assert place_labels.load(db) == {}
    place_labels.set_label(db, "57.7000,11.9000", "  Gym  ")
    place_labels.set_label(db, "57.8000,12.1000", "Parents")
    place_labels.set_label(db, "57.7000,11.9000", "Climbing\n  gym")        # replaced, whitespace tidied
    assert place_labels.load(db) == {"57.7000,11.9000": "Climbing gym", "57.8000,12.1000": "Parents"}
    place_labels.clear(db, "57.7000,11.9000")
    assert place_labels.load(db) == {"57.8000,12.1000": "Parents"}


def test_an_empty_or_too_long_label_is_refused(db):
    for bad in ("", "   ", "x" * (place_labels.MAX_LEN + 1)):
        with pytest.raises(ValueError):
            place_labels.set_label(db, "57.7000,11.9000", bad)
    assert place_labels.load(db) == {}
