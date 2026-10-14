"""The workshop's public names, served from the modules that define them."""

from __future__ import annotations

import pytest


def test_a_public_name_follows_its_module_and_keeps_no_patch(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import livery.workshop
    from livery.workshop import _forge_lane

    real = _forge_lane.this_repository
    # A test that patched the public name itself leaves the real value set
    # on the module after its undo; this test reads the served name.
    monkeypatch.delitem(vars(livery.workshop), "this_repository", raising=False)

    def fake(root: object) -> None:
        return None

    monkeypatch.setattr(_forge_lane, "this_repository", fake)
    served: object = livery.workshop.this_repository
    assert served is fake
    monkeypatch.setattr(_forge_lane, "this_repository", real)
    assert livery.workshop.this_repository is real
    assert "this_repository" not in vars(livery.workshop)
