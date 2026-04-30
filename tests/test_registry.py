import pytest

from bot_lib.registry import Project, RegistryError, load_registry


HEADER = "| 이름 | 경로 | 기본 브랜치 | 원격 | 테스트 명령 | 테스트 타임아웃(초) |"
SEP = "|---|---|---|---|---|---|"


def _write(tmp_path, body: str):
    md = tmp_path / "projects.md"
    md.write_text(body)
    return str(md)


def test_parse_single_project(tmp_path):
    proj = tmp_path / "myproj"
    proj.mkdir()
    path = _write(
        tmp_path,
        f"""# Project Registry

{HEADER}
{SEP}
| myproj | {proj} | main | origin | pytest | 120 |
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


@pytest.mark.parametrize("field", ["-", ""])
def test_test_cmd_skip(tmp_path, field):
    proj = tmp_path / "skipme"
    proj.mkdir()
    path = _write(
        tmp_path,
        f"""{HEADER}
{SEP}
| skipme | {proj} | main | origin | {field} | 60 |
""",
    )
    reg = load_registry(path)
    assert reg["skipme"].test_cmd is None


def test_comment_row_skipped(tmp_path):
    active = tmp_path / "active"
    active.mkdir()
    disabled = tmp_path / "disabled"
    disabled.mkdir()
    path = _write(
        tmp_path,
        f"""{HEADER}
{SEP}
| active | {active} | main | origin | pytest | 60 |
| # disabled | {disabled} | main | origin | pytest | 60 |
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
        f"""{HEADER}
{SEP}
| a | {a} | main | origin | pytest | 60 |
| b | {b} | develop | upstream | ./gradlew test --no-daemon | 600 |
""",
    )
    reg = load_registry(path)
    assert set(reg) == {"a", "b"}
    assert reg["a"].test_timeout == 60
    assert reg["a"].remote == "origin"
    assert reg["b"].default_branch == "develop"
    assert reg["b"].remote == "upstream"
    assert reg["b"].test_cmd == "./gradlew test --no-daemon"


# ---- remote field ----


def test_remote_field_parsed(tmp_path):
    proj = tmp_path / "p"
    proj.mkdir()
    path = _write(
        tmp_path,
        f"""{HEADER}
{SEP}
| p | {proj} | dev | 305 | ./gradlew test | 600 |
""",
    )
    assert load_registry(path)["p"].remote == "305"


@pytest.mark.parametrize("field", ["-", ""])
def test_remote_defaults_to_origin_when_blank(tmp_path, field):
    proj = tmp_path / "p"
    proj.mkdir()
    path = _write(
        tmp_path,
        f"""{HEADER}
{SEP}
| p | {proj} | main | {field} | pytest | 60 |
""",
    )
    assert load_registry(path)["p"].remote == "origin"


# ---- error cases ----


def test_rejects_bad_timeout(tmp_path):
    proj = tmp_path / "p"
    proj.mkdir()
    path = _write(
        tmp_path,
        f"""{HEADER}
{SEP}
| p | {proj} | main | origin | pytest | sixty |
""",
    )
    with pytest.raises(RegistryError, match="line 3.*타임아웃"):
        load_registry(path)


def test_rejects_column_mismatch(tmp_path):
    proj = tmp_path / "p"
    proj.mkdir()
    path = _write(
        tmp_path,
        f"""{HEADER}
{SEP}
| p | {proj} | main | origin | pytest |
""",
    )
    with pytest.raises(RegistryError, match="line 3.*컬럼"):
        load_registry(path)


def test_rejects_missing_path(tmp_path):
    missing = tmp_path / "nope"
    path = _write(
        tmp_path,
        f"""{HEADER}
{SEP}
| p | {missing} | main | origin | pytest | 60 |
""",
    )
    with pytest.raises(RegistryError, match="line 3.*경로"):
        load_registry(path)


def test_rejects_duplicate_name(tmp_path):
    a = tmp_path / "a"
    a.mkdir()
    b = tmp_path / "b"
    b.mkdir()
    path = _write(
        tmp_path,
        f"""{HEADER}
{SEP}
| dup | {a} | main | origin | pytest | 60 |
| dup | {b} | main | origin | pytest | 60 |
""",
    )
    with pytest.raises(RegistryError, match="line 4.*중복"):
        load_registry(path)


def test_follows_symlink(tmp_path):
    real = tmp_path / "real"
    real.mkdir()
    link = tmp_path / "link"
    link.symlink_to(real)
    path = _write(
        tmp_path,
        f"""{HEADER}
{SEP}
| p | {link} | main | origin | pytest | 60 |
""",
    )
    reg = load_registry(path)
    assert "p" in reg
