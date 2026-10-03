"""Link a directory of shipped entries into a checkout, as `fm sync` does."""

from __future__ import annotations

from pathlib import Path


def link_entries(repo: Path, source: Path, subdir: str) -> list[str]:
    """Each entry of *source* as a link output under `.claude/<subdir>/`, applied.

    The fragment engine's local outputs, the way a sync delivers an
    extension's skills or hooks: a link, a junction or a copy, an edit
    kept, an entry no longer shipped withdrawn.
    """
    from livery.workshop._fragment_engine import Output, apply

    entries = sorted(source.iterdir()) if source.is_dir() else []
    outputs = [
        Output(
            f".claude/{subdir}/{entry.name}",
            b"",
            (f"test:{subdir}/{entry.name}",),
            link=entry,
            local=True,
        )
        for entry in entries
    ]
    repo.mkdir(parents=True, exist_ok=True)
    return apply(repo, outputs)
