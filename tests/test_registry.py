import pytest

from bot_lib.registry import Project, RegistryError, load_registry


def _write(tmp_path, body: str):
    f = tmp_path / "projects.toml"
    f.write_text(body)
    return str(f)


def test_parse_single_project(tmp_path):
    proj = tmp_path / "myproj"
    proj.mkdir()
    path = _write(
        tmp_path,
        f"""
[myproj]
path = "{proj}"
default_branch = "main"
remote = "origin"
test_cmd = "pytest"
test_timeout = 120
""",
    )
    reg = load_registry(path)
    assert set(reg) == {"myproj"}
    p = reg["myproj"]
    assert isinstance(p, Project)
    assert p.name == "myproj"
    assert p.path == str(proj)
    assert p.default_branch == "main"
    assert p.remote == "origin"
    assert p.test_cmd == "pytest"
    assert p.test_timeout == 120


@pytest.mark.parametrize("body_extra", ['test_cmd = ""', "# no test_cmd"])
def test_test_cmd_blank_or_missing_means_skip(tmp_path, body_extra):
    proj = tmp_path / "skipme"
    proj.mkdir()
    path = _write(
        tmp_path,
        f"""
[skipme]
path = "{proj}"
default_branch = "main"
{body_extra}
test_timeout = 60
""",
    )
    reg = load_registry(path)
    assert reg["skipme"].test_cmd is None


def test_disabled_section_skipped(tmp_path):
    active = tmp_path / "active"
    active.mkdir()
    disabled = tmp_path / "disabled"
    disabled.mkdir()
    path = _write(
        tmp_path,
        f"""
[active]
path = "{active}"
default_branch = "main"
test_cmd = "pytest"
test_timeout = 60

[disabled-one]
path = "{disabled}"
default_branch = "main"
test_cmd = "pytest"
test_timeout = 60
disabled = true
""",
    )
    reg = load_registry(path)
    assert set(reg) == {"active"}


def test_parse_multiple_projects(tmp_path):
    a = tmp_path / "a"
    a.mkdir()
    b = tmp_path / "b"
    b.mkdir()
    path = _write(
        tmp_path,
        f"""
[a]
path = "{a}"
default_branch = "main"
test_cmd = "pytest"
test_timeout = 60

[b]
path = "{b}"
default_branch = "develop"
remote = "upstream"
test_cmd = "./gradlew test --no-daemon"
test_timeout = 600
""",
    )
    reg = load_registry(path)
    assert set(reg) == {"a", "b"}
    assert reg["a"].test_timeout == 60
    assert reg["a"].remote == "origin"   # default
    assert reg["b"].default_branch == "develop"
    assert reg["b"].remote == "upstream"
    assert reg["b"].test_cmd == "./gradlew test --no-daemon"


# ---- remote field ----


def test_remote_field_parsed(tmp_path):
    proj = tmp_path / "p"
    proj.mkdir()
    path = _write(
        tmp_path,
        f"""
[p]
path = "{proj}"
default_branch = "dev"
remote = "305"
test_cmd = "./gradlew test"
test_timeout = 600
""",
    )
    assert load_registry(path)["p"].remote == "305"


@pytest.mark.parametrize("remote_line", ['remote = ""', "# no remote"])
def test_remote_defaults_to_origin_when_blank_or_missing(tmp_path, remote_line):
    proj = tmp_path / "p"
    proj.mkdir()
    path = _write(
        tmp_path,
        f"""
[p]
path = "{proj}"
default_branch = "main"
{remote_line}
test_cmd = "pytest"
test_timeout = 60
""",
    )
    assert load_registry(path)["p"].remote == "origin"


def test_test_timeout_defaults_to_600_when_missing(tmp_path):
    proj = tmp_path / "p"
    proj.mkdir()
    path = _write(
        tmp_path,
        f"""
[p]
path = "{proj}"
default_branch = "main"
""",
    )
    assert load_registry(path)["p"].test_timeout == 600


# ---- error cases ----


def test_rejects_bad_timeout_type(tmp_path):
    proj = tmp_path / "p"
    proj.mkdir()
    path = _write(
        tmp_path,
        f"""
[p]
path = "{proj}"
default_branch = "main"
test_cmd = "pytest"
test_timeout = "sixty"
""",
    )
    with pytest.raises(RegistryError, match="test_timeout"):
        load_registry(path)


def test_rejects_zero_timeout(tmp_path):
    proj = tmp_path / "p"
    proj.mkdir()
    path = _write(
        tmp_path,
        f"""
[p]
path = "{proj}"
default_branch = "main"
test_timeout = 0
""",
    )
    with pytest.raises(RegistryError, match="양수"):
        load_registry(path)


def test_rejects_missing_path(tmp_path):
    missing = tmp_path / "nope"
    path = _write(
        tmp_path,
        f"""
[p]
path = "{missing}"
default_branch = "main"
test_cmd = "pytest"
test_timeout = 60
""",
    )
    with pytest.raises(RegistryError, match="경로가 존재하지 않음"):
        load_registry(path)


def test_rejects_missing_path_field(tmp_path):
    path = _write(
        tmp_path,
        """
[p]
default_branch = "main"
""",
    )
    with pytest.raises(RegistryError, match="`path` 필수"):
        load_registry(path)


def test_rejects_missing_default_branch(tmp_path):
    proj = tmp_path / "p"
    proj.mkdir()
    path = _write(
        tmp_path,
        f"""
[p]
path = "{proj}"
""",
    )
    with pytest.raises(RegistryError, match="default_branch"):
        load_registry(path)


def test_rejects_unknown_field(tmp_path):
    proj = tmp_path / "p"
    proj.mkdir()
    path = _write(
        tmp_path,
        f"""
[p]
path = "{proj}"
default_branch = "main"
nonsense = "x"
""",
    )
    with pytest.raises(RegistryError, match="알 수 없는 필드"):
        load_registry(path)


def test_rejects_invalid_name_char(tmp_path):
    proj = tmp_path / "p"
    proj.mkdir()
    # TOML allows quoted keys like "a/b"; we reject them at the registry level.
    path = _write(
        tmp_path,
        f"""
"a/b" = {{ path = "{proj}", default_branch = "main" }}
""",
    )
    with pytest.raises(RegistryError, match="이름은"):
        load_registry(path)


def test_rejects_malformed_toml(tmp_path):
    path = _write(
        tmp_path,
        """
[p
path = "/x"
""",
    )
    with pytest.raises(RegistryError, match="TOML 파싱"):
        load_registry(path)


def test_follows_symlink(tmp_path):
    real = tmp_path / "real"
    real.mkdir()
    link = tmp_path / "link"
    link.symlink_to(real)
    path = _write(
        tmp_path,
        f"""
[p]
path = "{link}"
default_branch = "main"
test_cmd = "pytest"
test_timeout = 60
""",
    )
    reg = load_registry(path)
    assert "p" in reg
