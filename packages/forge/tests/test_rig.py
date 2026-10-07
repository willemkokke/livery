"""The live suites drive the local containers by the containers' own record."""

from __future__ import annotations

from pathlib import Path

import pytest

from forge_rig import rig_credentials


def test_the_live_suites_read_the_containers_record_not_the_cascade(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config = tmp_path / "footman"
    config.mkdir()
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    # The refusal first: without the record there is nothing to drive,
    # whatever the cascade names. A local loop environment writes its own
    # Gitea into both the environment and the shared file.
    monkeypatch.setenv("GITEA_URL", "http://localhost:64346")
    (config / ".repo.shared.env").write_text(
        "GITEA_URL=http://localhost:64346\nGITEA_TOKEN=loop\n"
    )
    assert rig_credentials() == {}
    (config / ".forge-dev.env").write_text(
        "GITEA_URL=http://localhost:3000\nGITEA_TOKEN=rig\n"
    )
    assert rig_credentials() == {
        "GITEA_URL": "http://localhost:3000",
        "GITEA_TOKEN": "rig",
    }
