import json
import sys
from pathlib import Path

from mpat import _cli, _gitlab, _registry


def project(root: Path) -> Path:
    root.mkdir(parents=True, exist_ok=True)
    (root / "pyproject.toml").write_text(
        '[project]\nname = "x"\n[tool.mpat]\nmodules = ["app_patches"]\n'
    )
    (root / "app_patches.py").write_text(
        "import mpat\n"
        "\n"
        "\n"
        "mpat.watch('fakeup.LIMIT', note='issue #7')\n"
        'mpat.watch("fakeup.REGISTRY")\n'
    )
    return root


def report(capsys) -> list[dict]:
    return json.loads(capsys.readouterr().out)


def test_report_lists_only_non_ok_targets(tmp_path, upstream, monkeypatch, capsys):
    root = project(tmp_path)
    monkeypatch.syspath_prepend(str(root))
    monkeypatch.chdir(root)
    _cli.main(["lock"])
    capsys.readouterr()
    upstream.edit("__init__.py", "LIMIT = 16", "LIMIT = 2")
    assert _cli.main(["check", "--gitlab"]) == _cli.EXIT_DRIFT
    issues = report(capsys)
    assert [i["check_name"] for i in issues] == ["mpat/value"]
    issue = issues[0]
    assert issue["description"] == "watch fakeup.LIMIT: the upstream value changed (issue #7)"
    assert issue["severity"] == "major"
    assert issue["location"] == {"path": "app_patches.py", "lines": {"begin": 4}}


def test_fingerprint_is_stable_across_runs(tmp_path, upstream, monkeypatch, capsys):
    root = project(tmp_path)
    monkeypatch.syspath_prepend(str(root))
    monkeypatch.chdir(root)
    assert _cli.main(["check", "--gitlab"]) == _cli.EXIT_DRIFT
    first = report(capsys)
    assert _cli.main(["check", "--gitlab"]) == _cli.EXIT_DRIFT
    assert [i["fingerprint"] for i in report(capsys)] == [i["fingerprint"] for i in first]
    assert len({i["fingerprint"] for i in first}) == len(first)


def test_path_is_relative_to_the_repository_not_the_mpat_root(
    tmp_path, upstream, monkeypatch, capsys
):
    (tmp_path / ".git").mkdir()
    root = project(tmp_path / "services" / "api")
    monkeypatch.syspath_prepend(str(root))
    monkeypatch.chdir(root)
    assert _cli.main(["check", "--gitlab"]) == _cli.EXIT_DRIFT
    assert {i["location"]["path"] for i in report(capsys)} == {"services/api/app_patches.py"}


def test_repo_root_flag_overrides_detection(tmp_path, upstream, monkeypatch, capsys):
    (tmp_path / ".git").mkdir()
    root = project(tmp_path / "services" / "api")
    monkeypatch.syspath_prepend(str(root))
    monkeypatch.chdir(root)
    assert _cli.main(["check", "--gitlab", "--repo-root", str(tmp_path / "services")])
    assert {i["location"]["path"] for i in report(capsys)} == {"api/app_patches.py"}


def test_worktree_git_file_counts_as_the_repository_root(tmp_path):
    (tmp_path / ".git").write_text("gitdir: /elsewhere\n")
    nested = tmp_path / "a" / "b"
    nested.mkdir(parents=True)
    assert _gitlab.repo_root(nested) == tmp_path


def test_repo_root_is_none_outside_a_checkout(tmp_path):
    assert _gitlab.repo_root(tmp_path) is None


def test_unlocked_and_stale_map_to_their_own_severities(tmp_path, upstream, monkeypatch, capsys):
    root = project(tmp_path)
    monkeypatch.syspath_prepend(str(root))
    monkeypatch.chdir(root)
    assert _cli.main(["check", "--gitlab"]) == _cli.EXIT_DRIFT
    by_target = {i["description"].split()[1].rstrip(":"): i for i in report(capsys)}
    assert by_target["fakeup.LIMIT"]["severity"] == "major"
    assert by_target["fakeup.LIMIT"]["check_name"] == "mpat/unlocked"
    _cli.main(["lock"])
    (root / "app_patches.py").write_text("import mpat\n")
    _cli.main(["check", "--gitlab"])
    capsys.readouterr()
    _registry.reset()
    sys.modules.pop("app_patches", None)
    assert _cli.main(["check", "--gitlab"]) == _cli.EXIT_DRIFT
    stale = report(capsys)
    assert {i["severity"] for i in stale} == {"info"}
    assert {i["check_name"] for i in stale} == {"mpat/stale"}


def test_json_and_gitlab_are_mutually_exclusive(tmp_path, capsys):
    try:
        _cli.main(["check", "--json", "--gitlab"])
    except SystemExit as exc:
        assert exc.code == _cli.EXIT_USAGE
    else:
        raise AssertionError("expected argparse to reject both flags")
