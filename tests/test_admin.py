import pytest

from polestar import admin


@pytest.fixture(autouse=True)
def fresh(monkeypatch):
    monkeypatch.setenv("ADMIN_PASSWORD", "s3cret")
    admin._fails.clear()
    yield
    admin._fails.clear()


def test_right_password_unlocks_for_a_while_then_expires():
    st = {}
    assert not admin.unlocked(st, now=0)
    assert admin.unlock(st, "s3cret", now=100) is None
    assert admin.unlocked(st, now=100 + admin.UNLOCK_SECONDS - 1)
    assert not admin.unlocked(st, now=100 + admin.UNLOCK_SECONDS + 1)


def test_wrong_password_stays_locked():
    st = {}
    assert admin.unlock(st, "nope", now=0) == "Wrong password."
    assert not admin.unlocked(st, now=1)


def test_lock_ends_the_session_early():
    st = {}
    admin.unlock(st, "s3cret", now=0)
    admin.lock(st)
    assert not admin.unlocked(st, now=1)


def test_nothing_unlocks_without_a_configured_password(monkeypatch):
    monkeypatch.delenv("ADMIN_PASSWORD")
    st = {}
    assert not admin.configured()
    assert "No admin password" in admin.unlock(st, "", now=0)
    assert not admin.unlocked({admin.KEY: 10**12}, now=0)       # a forged flag does not count either


def test_too_many_wrong_attempts_lock_everyone_out_until_the_window_passes():
    for i in range(admin.MAX_FAILS):
        assert admin.unlock({}, "nope", now=i) == "Wrong password."
    other_session = {}
    assert "Too many wrong attempts" in admin.unlock(other_session, "s3cret", now=10)   # even the right one
    assert admin.unlock(other_session, "s3cret", now=admin.LOCK_SECONDS + 10) is None


def test_a_success_resets_the_failure_count():
    for i in range(admin.MAX_FAILS - 1):
        admin.unlock({}, "nope", now=i)
    assert admin.unlock({}, "s3cret", now=10) is None
    for i in range(admin.MAX_FAILS - 1):
        assert admin.unlock({}, "nope", now=20 + i) == "Wrong password."


def test_viewer_scope_has_its_own_password_and_a_longer_session(monkeypatch):
    monkeypatch.setenv("DASHBOARD_PASSWORD", "view")
    st = {}
    assert admin.configured("viewer") and not admin.unlocked(st, now=0, scope="viewer")
    assert admin.unlock(st, "s3cret", now=0, scope="viewer") == "Wrong password."     # the admin password is not the viewer one
    assert admin.unlock(st, "view", now=0, scope="viewer") is None
    assert admin.unlocked(st, now=admin.VIEWER_SECONDS - 1, scope="viewer")
    assert not admin.unlocked(st, now=1, scope="admin")                               # viewing does not unlock editing
    admin.lock(st, scope="viewer")
    assert not admin.unlocked(st, now=1, scope="viewer")


def test_viewer_scope_is_off_without_its_password(monkeypatch):
    monkeypatch.delenv("DASHBOARD_PASSWORD", raising=False)
    assert not admin.configured("viewer")
    assert "No dashboard password" in admin.unlock({}, "", now=0, scope="viewer")


def test_an_argon2_hash_works_in_place_of_the_plain_password(monkeypatch):
    monkeypatch.setenv("ADMIN_PASSWORD", admin.HASHER.hash("s3cret"))
    st = {}
    assert admin.unlock(st, "nope", now=0) == "Wrong password."
    assert admin.unlock(st, "s3cret", now=1) is None and admin.unlocked(st, now=2)


def test_a_malformed_hash_never_unlocks(monkeypatch):
    monkeypatch.setenv("ADMIN_PASSWORD", "$argon2id$garbage")
    assert admin.unlock({}, "$argon2id$garbage", now=0) == "Wrong password."
