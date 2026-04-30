import threading

from bot_lib.context import Context, ContextStore


def test_get_returns_none_when_no_file(tmp_path):
    store = ContextStore(str(tmp_path / "nope.json"))
    assert store.get() is None


def test_set_then_get_roundtrip(tmp_path):
    store = ContextStore(str(tmp_path / "ctx.json"))
    store.set(Context(repo="ceph-api", remote="305"))
    out = store.get()
    assert out == Context(repo="ceph-api", remote="305")


def test_set_overwrites_existing(tmp_path):
    store = ContextStore(str(tmp_path / "ctx.json"))
    store.set(Context(repo="alpha", remote="origin"))
    store.set(Context(repo="ceph-api", remote="305"))
    assert store.get() == Context(repo="ceph-api", remote="305")


def test_clear_removes_file(tmp_path):
    p = tmp_path / "ctx.json"
    store = ContextStore(str(p))
    store.set(Context(repo="x", remote="y"))
    assert p.exists()
    store.clear()
    assert store.get() is None
    assert not p.exists()


def test_clear_when_no_file_is_safe(tmp_path):
    store = ContextStore(str(tmp_path / "missing.json"))
    store.clear()  # must not raise
    assert store.get() is None


def test_get_returns_none_when_file_corrupt(tmp_path):
    p = tmp_path / "ctx.json"
    p.write_text("not valid json {{{")
    store = ContextStore(str(p))
    assert store.get() is None


def test_get_returns_none_when_keys_missing(tmp_path):
    p = tmp_path / "ctx.json"
    p.write_text('{"repo": "x"}')  # no remote
    store = ContextStore(str(p))
    assert store.get() is None


def test_set_creates_parent_dir(tmp_path):
    nested = tmp_path / "deeper" / "still" / "ctx.json"
    store = ContextStore(str(nested))
    store.set(Context(repo="x", remote="y"))
    assert nested.exists()


def test_set_with_branch_persists(tmp_path):
    store = ContextStore(str(tmp_path / "ctx.json"))
    store.set(Context(repo="ceph-api", remote="305", branch="develop"))
    out = store.get()
    assert out == Context(repo="ceph-api", remote="305", branch="develop")


def test_set_without_branch_omits_field(tmp_path):
    store = ContextStore(str(tmp_path / "ctx.json"))
    store.set(Context(repo="ceph-api", remote="305"))
    out = store.get()
    assert out is not None
    assert out.branch is None


def test_set_with_who_persists(tmp_path):
    store = ContextStore(str(tmp_path / "ctx.json"))
    store.set(Context(repo="ceph-api", remote="305", who="hjpark"))
    out = store.get()
    assert out is not None
    assert out.who == "hjpark"


def test_set_without_who_omits_field(tmp_path):
    store = ContextStore(str(tmp_path / "ctx.json"))
    store.set(Context(repo="ceph-api", remote="305"))
    assert store.get().who is None


def test_legacy_json_without_who_loads_cleanly(tmp_path):
    """Older context.json (pre-who) must still parse — who falls back to None."""
    p = tmp_path / "ctx.json"
    p.write_text('{"repo": "ceph-api", "remote": "305"}')
    store = ContextStore(str(p))
    out = store.get()
    assert out is not None
    assert out.who is None


def test_concurrent_set_does_not_corrupt(tmp_path):
    store = ContextStore(str(tmp_path / "ctx.json"))

    def writer(n):
        for i in range(50):
            store.set(Context(repo=f"r{n}", remote=f"m{i}"))

    threads = [threading.Thread(target=writer, args=(n,)) for n in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    out = store.get()
    assert out is not None
    assert out.repo.startswith("r")
    assert out.remote.startswith("m")
