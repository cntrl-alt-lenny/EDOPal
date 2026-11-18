import subprocess
from pathlib import Path
from unittest.mock import Mock

import pytest

import desktop_helpers as desktop


@pytest.mark.parametrize("original", [None, "", "/usr/local/lib"])
def test_frozen_reveal_and_open_restore_child_environment(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, original: str | None
) -> None:
    monkeypatch.setattr(desktop.sys, "platform", "linux")
    monkeypatch.setattr(desktop.sys, "frozen", True, raising=False)
    monkeypatch.setenv("LD_LIBRARY_PATH", "/tmp/_MEI123")
    if original is None:
        monkeypatch.delenv("LD_LIBRARY_PATH_ORIG", raising=False)
    else:
        monkeypatch.setenv("LD_LIBRARY_PATH_ORIG", original)
    path = tmp_path / "a deck #2.ydk"
    path.touch()
    run = Mock(side_effect=[FileNotFoundError(), Mock(returncode=1), Mock(returncode=0)])
    monkeypatch.setattr(subprocess, "run", run)
    assert not desktop.reveal_file(path)
    assert desktop.open_link("https://example.org/releases")
    select, folder, link = run.call_args_list
    assert select.args[0][0] == "gdbus" and path.as_uri() in select.args[0][-2]
    assert folder.args[0] == ["xdg-open", str(tmp_path)]
    assert link.args[0] == ["xdg-open", "https://example.org/releases"]
    for call in run.call_args_list:
        assert call.kwargs["env"].get("LD_LIBRARY_PATH") == original
    assert desktop.os.environ["LD_LIBRARY_PATH"] == "/tmp/_MEI123"


def test_unfrozen_environment_is_preserved(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(desktop.sys, "platform", "linux")
    monkeypatch.setenv("LD_LIBRARY_PATH", "/opt/test")
    assert desktop.desktop_environment()["LD_LIBRARY_PATH"] == "/opt/test"
