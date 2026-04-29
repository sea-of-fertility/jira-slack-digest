import pytest

from bot_lib.registry import Project, RegistryError, load_registry


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

| 이름 | 경로 | 기본 브랜치 | 테스트 명령 | 테스트 타임아웃(초) |
|---|---|---|---|---|
| myproj | {proj} | main | pytest | 120 |
""",
    )
    reg = load_registry(path)
    assert set(reg) == {"myproj"}
    p = reg["myproj"]
    assert isinstance(p, Project)
    assert p.name == "myproj"
    assert p.path == str(proj)
    assert p.default_branch == "main"
    assert p.test_cmd == "pytest"
    assert p.test_timeout == 120


@pytest.mark.parametrize("field", ["-", ""])
def test_test_cmd_skip(tmp_path, field):
    proj = tmp_path / "skipme"
    proj.mkdir()
    path = _write(
        tmp_path,
        f"""| 이름 | 경로 | 기본 브랜치 | 테스트 명령 | 테스트 타임아웃(초) |
|---|---|---|---|---|
| skipme | {proj} | main | {field} | 60 |
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
        f"""| 이름 | 경로 | 기본 브랜치 | 테스트 명령 | 테스트 타임아웃(초) |
|---|---|---|---|---|
| active | {active} | main | pytest | 60 |
| # disabled | {disabled} | main | pytest | 60 |
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
        f"""| 이름 | 경로 | 기본 브랜치 | 테스트 명령 | 테스트 타임아웃(초) |
|---|---|---|---|---|
| a | {a} | main | pytest | 60 |
| b | {b} | develop | ./gradlew test --no-daemon | 600 |
""",
    )
    reg = load_registry(path)
    assert set(reg) == {"a", "b"}
    assert reg["a"].test_timeout == 60
    assert reg["b"].default_branch == "develop"
    assert reg["b"].test_cmd == "./gradlew test --no-daemon"


def test_rejects_bad_timeout(tmp_path):
    proj = tmp_path / "p"
    proj.mkdir()
    path = _write(
        tmp_path,
        f"""| 이름 | 경로 | 기본 브랜치 | 테스트 명령 | 테스트 타임아웃(초) |
|---|---|---|---|---|
| p | {proj} | main | pytest | sixty |
""",
    )
    with pytest.raises(RegistryError, match="line 3.*타임아웃"):
        load_registry(path)


def test_rejects_column_mismatch(tmp_path):
    proj = tmp_path / "p"
    proj.mkdir()
    path = _write(
        tmp_path,
        f"""| 이름 | 경로 | 기본 브랜치 | 테스트 명령 | 테스트 타임아웃(초) |
|---|---|---|---|---|
| p | {proj} | main | pytest |
""",
    )
    with pytest.raises(RegistryError, match="line 3.*컬럼"):
        load_registry(path)


def test_rejects_missing_path(tmp_path):
    missing = tmp_path / "nope"  # not created
    path = _write(
        tmp_path,
        f"""| 이름 | 경로 | 기본 브랜치 | 테스트 명령 | 테스트 타임아웃(초) |
|---|---|---|---|---|
| p | {missing} | main | pytest | 60 |
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
        f"""| 이름 | 경로 | 기본 브랜치 | 테스트 명령 | 테스트 타임아웃(초) |
|---|---|---|---|---|
| dup | {a} | main | pytest | 60 |
| dup | {b} | main | pytest | 60 |
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
        f"""| 이름 | 경로 | 기본 브랜치 | 테스트 명령 | 테스트 타임아웃(초) |
|---|---|---|---|---|
| p | {link} | main | pytest | 60 |
""",
    )
    reg = load_registry(path)
    assert "p" in reg
