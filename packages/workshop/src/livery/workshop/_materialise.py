"""Materialise wheel-shipped agent content into a repository's ``.claude/``.

The contract is sync-materialised wheel content: after ``fm sync``,
``.claude/skills/<name>`` and ``.claude/hooks/<script>`` match what
the mounted layers ship. Links are the zero-drift way to honour it (a
layer upgrade moves every repository at once, nothing to re-copy) but
they are an optimisation, not the contract: where a link cannot be
made, the same content is copied and refreshed on every sync. Nothing
here ever fails a reconcile; the worst case is a printed line.

Link mechanism, in order of preference: a relative ``os.symlink`` (a
relative target survives the repository moving; in the monorepo the
target sits inside the repository, so containment holds by
construction); a Windows directory junction, which needs no privilege
where symlinks do; a copy, recorded in a manifest so the next sync
knows the entry is ours to refresh rather than a developer's own
work.

**Local content always wins.** A real directory whose content differs
from the shipped copy is a deliberate override: it is kept and named
in the summary, and the managed ``.gitignore`` is self-scoped (it
lists only what this module materialised), so the override commits
like any repository file.

**Editing through a link writes into the wheel.** In the monorepo
that is correct: the target is the source tree. In an instance it
edits site-packages, machine-wide and lost on the next upgrade; the
escape is to copy the entry to a real directory of the same name,
which then takes precedence.
"""

from __future__ import annotations

import filecmp
import hashlib
import os
import shutil
import stat
import sys
import tempfile
from collections.abc import Callable
from pathlib import Path

_MANIFEST = ".livery-materialised"
"""What this module *copied* into a directory: ``<hash> <name>`` lines.

A manifest rather than a marker inside each entry, because the copy
fallback must work for files too (the hook scripts) and a file has
nowhere to carry a marker. Links need no record; they are
self-identifying. The hash is the copied content's digest at copy
time: it is how a later sync tells a stale copy of ours (refresh)
from a local edit (an override, kept and named). A legacy line
without a hash grants ownership without that distinction, and the
next refresh upgrades it.
"""

_GITIGNORE_HEADER = (
    "# Managed by `{prog} sync` - the entries below are materialised from the\n"
    "# mounted layers' wheels. This list is deliberately self-scoped: a\n"
    "# skill you add yourself is NOT ignored and commits normally.\n"
)


def _is_link(path: Path) -> bool:
    """Whether *path* is a symlink or a Windows reparse point (junction)."""
    if path.is_symlink():
        return True
    try:
        tag = getattr(path.lstat(), "st_reparse_tag", 0)
    except OSError:
        return False
    return bool(tag)


def _points_at(link: Path, target: Path) -> bool:
    """Whether *link* already resolves to *target* (False when dangling)."""
    try:
        return os.path.samestat(link.stat(), target.stat())
    except OSError:
        return False


def _relative_target(link: Path, target: Path) -> str:
    """*target* relative to *link*'s directory, or absolute if impossible.

    A relative target is what makes the link survive the repository
    moving; a cross-volume layout falls back to the absolute path.
    """
    try:
        return os.path.relpath(target, link.parent)
    except ValueError:  # different drives on Windows
        return str(target)


def _make_link(link: Path, target: Path) -> str:
    """Materialise *link* to *target*: "linked", "junction", or "copied"."""
    link.parent.mkdir(parents=True, exist_ok=True)
    try:
        os.symlink(
            _relative_target(link, target), link, target_is_directory=target.is_dir()
        )
    except (OSError, NotImplementedError):
        pass
    else:
        return "linked"
    if sys.platform == "win32" and target.is_dir():
        try:
            import _winapi

            _winapi.CreateJunction(str(target), str(link))
        except (ImportError, OSError, AttributeError):
            pass
        else:
            return "junction"
    if target.is_dir():
        shutil.copytree(target, link)
    else:
        shutil.copy2(target, link)
    return "copied"


def _remove(path: Path) -> None:
    """Remove a link, a plain file, or a directory we own."""
    if _is_link(path):
        try:
            path.unlink()
        except OSError:
            os.rmdir(path)  # a junction reads as a directory to some APIs
        return
    if not path.is_dir():
        path.unlink()
        return

    def _force(func: Callable[[str], object], target: str, _exc: object) -> None:
        os.chmod(target, stat.S_IWRITE)
        func(target)

    shutil.rmtree(path, onerror=_force)


def _same_content(local: Path, shipped: Path) -> bool:
    """Whether a real entry matches the shipped copy byte for byte.

    The one question that separates a stale committed copy (safe to
    replace with a link) from a deliberate local override (keep, and
    say so).
    """
    if not local.is_dir() or not shipped.is_dir():
        return (
            local.is_file()
            and shipped.is_file()
            and filecmp.cmp(local, shipped, shallow=False)
        )
    comparison = filecmp.dircmp(str(local), str(shipped))
    # common_funny holds names present on both sides with different
    # types; those appear in neither common_files nor common_dirs, so
    # omitting the check would call the trees identical and delete the
    # local one.
    if (
        comparison.left_only
        or comparison.right_only
        or comparison.funny_files
        or comparison.common_funny
    ):
        return False
    mismatch, errors = filecmp.cmpfiles(
        str(local), str(shipped), comparison.common_files, shallow=False
    )[1:]
    if mismatch or errors:
        return False
    return all(
        _same_content(local / sub, shipped / sub) for sub in comparison.common_dirs
    )


def _digest(target: Path) -> str:
    """A stable digest of a file's bytes or a directory's whole content."""
    digest = hashlib.sha256()
    if target.is_file():
        digest.update(target.read_bytes())
        return digest.hexdigest()
    for child in sorted(target.rglob("*")):
        if child.is_file():
            digest.update(str(child.relative_to(target)).encode())
            digest.update(child.read_bytes())
    return digest.hexdigest()


def _read_manifest(root: Path) -> dict[str, str]:
    """Name to copy-time digest from a previous sync (empty when none).

    A legacy name-only line reads as an empty digest: ownership
    without the stale-versus-edited distinction.
    """
    path = root / _MANIFEST
    entries: dict[str, str] = {}
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return entries
    for line in text.splitlines():
        if not line.strip():
            continue
        first, _, rest = line.partition(" ")
        if rest and len(first) == 64 and all(c in "0123456789abcdef" for c in first):
            entries[rest] = first
        else:
            entries[line.strip()] = ""
    return entries


def write_lf(path: Path, text: str) -> None:
    r"""Write *text* with LF endings on every platform.

    ``Path.write_text`` translates ``\n`` to the platform separator;
    generated files are written on one machine and linted on another,
    so their bytes cannot depend on which one wrote them.
    """
    path.write_text(text, encoding="utf-8", newline="\n")


def _write_manifest(root: Path, copies: dict[str, str]) -> None:
    """Record what we copied and its digests; remove the file when nothing."""
    path = root / _MANIFEST
    if not copies:
        path.unlink(missing_ok=True)
        return
    body = "".join(f"{digest} {name}\n" for name, digest in sorted(copies.items()))
    if not path.exists() or path.read_text(encoding="utf-8") != body:
        write_lf(path, body)


def _entry(
    link: Path,
    target: Path,
    overrides: list[str],
    reclaimed: list[str],
    *,
    copied_digest: str | None,
) -> tuple[bool, str]:
    """Reconcile one materialised entry. Never raises.

    Returns ``(ours, mode)``: whether the entry is ours afterwards (a
    link or a copy we own) and which mechanism this call used (empty
    when nothing changed). ``ours=False`` means a local override is in
    place, which keeps its name out of the managed ``.gitignore`` so
    the developer can commit it.
    """
    if not link.exists() and not _is_link(link):
        return True, _make_link(link, target)
    if _is_link(link):
        if _points_at(link, target):
            return True, ""
        _remove(link)  # wrong or dangling target: re-point it
        return True, _make_link(link, target)
    if copied_digest is not None:
        if _same_content(link, target):
            return True, ""  # our copy, current: nothing to do or say
        if copied_digest and _digest(link) != copied_digest:
            # Changed since we copied it: a local edit, not staleness.
            # The override is kept and named, exactly as on a link
            # platform where a replaced link means the same thing.
            overrides.append(link.name)
            return False, ""
        # Stale (or legacy-owned without a digest): ours to refresh.
        _remove(link)
        return True, _make_link(link, target)
    if link.is_dir() and not any(link.iterdir()):
        link.rmdir()
        return True, _make_link(link, target)
    if _same_content(link, target):
        _remove(link)  # a committed copy identical to ours: reclaim it
        reclaimed.append(link.name)
        return True, _make_link(link, target)
    overrides.append(link.name)
    return False, ""


def _write_gitignore(root: Path, managed: list[str]) -> None:
    """Rewrite the managed .gitignore from what was actually materialised.

    Deliberately not the list of shipped names: an override owns a
    real directory of the shipped name, and listing it here would make
    the override impossible to commit.
    """
    from livery.workshop._brand import runner_prog

    body = _GITIGNORE_HEADER.format(prog=runner_prog()) + "".join(
        f"/{name}\n" for name in sorted(managed)
    )
    body += f"/{_MANIFEST}\n/.gitignore\n"
    path = root / ".gitignore"
    if not path.exists() or path.read_text(encoding="utf-8") != body:
        write_lf(path, body)


def _normalise(path: Path | str) -> str:
    r"""A local path reduced to its filesystem identity, for comparison.

    Windows hands back an extended-length form from ``readlink``
    (``\\?\D:\repo``), so a plain prefix comparison against a normal
    path fails; strip the markers, then make the path absolute, fold
    case, and keep native separators.
    """
    text = str(path)
    if text.startswith("\\\\?\\"):
        text = text[4:]
        if text[:4].upper() == "UNC\\":
            text = "\\\\" + text[4:]
    return os.path.normcase(os.path.normpath(os.path.abspath(text)))


def _ours(child: Path, source: Path, copies: set[str]) -> bool:
    """Whether *child* is content this module materialised from *source*.

    Provenance is "the link points into the source tree", compared
    lexically so a dangling link (the venv was rebuilt) is still
    recognisably ours rather than stranded forever.
    """
    if child.name in copies:
        return True
    if not _is_link(child):
        return False
    try:
        target = Path(os.readlink(child)) if child.is_symlink() else child.resolve()
    except OSError:
        return False
    if not target.is_absolute():
        target = child.parent / target
    root = _normalise(source)
    # Guard the separator: without it `.../skills-old/x` reads as ours
    # against source `.../skills` and would be pruned.
    return _normalise(target).startswith(root + os.sep)


def case_insensitive(directory: Path) -> bool:
    """Whether *directory*'s filesystem matches names ignoring case.

    Asked of the filesystem, never inferred from the platform: macOS
    ships both behaviours and a wrong answer either deletes a live
    entry or strands a stale one. A directory that cannot be written
    answers False, the conservative reading.
    """
    try:
        handle, name = tempfile.mkstemp(prefix=".livery-case-", dir=directory)
    except OSError:
        return False
    os.close(handle)
    probe = Path(name)
    try:
        return probe.with_name(probe.name.upper()).exists()
    except OSError:
        return False
    finally:
        probe.unlink(missing_ok=True)


def _prune(root: Path, shipped: set[str], source: Path, copies: set[str]) -> list[str]:
    """Drop links we made for content the layers no longer ship.

    Membership is case-folded where the filesystem is, so a shipped
    entry renamed by case alone is not read as gone and deleted.
    """
    keep = shipped | {".gitignore", _MANIFEST}
    if case_insensitive(root):
        keep = {name.lower() for name in keep}
        folded = True
    else:
        folded = False
    dropped: list[str] = []
    for child in sorted(root.iterdir()):
        name = child.name.lower() if folded else child.name
        if name in keep:
            continue
        try:
            if _ours(child, source, copies):
                _remove(child)
                dropped.append(child.name)
        except OSError:
            continue
    return dropped


def materialise(repo_root: Path, source: Path, subdir: str) -> list[str]:
    """Materialise every entry of *source* into ``<repo_root>/.claude/<subdir>``.

    Returns human-readable summary lines (empty when nothing changed
    and nothing needs saying). Errors are reported, never raised:
    agent content is a convenience, and a broken link must not fail a
    sync.
    """
    lines: list[str] = []
    if not source.is_dir():
        return lines
    root = repo_root / ".claude" / subdir
    root.mkdir(parents=True, exist_ok=True)

    shipped = [
        entry.name for entry in sorted(source.iterdir()) if entry.name != "__pycache__"
    ]
    overrides: list[str] = []
    modes: set[str] = set()
    previous = _read_manifest(root)
    ours: list[str] = []
    reclaimed: list[str] = []
    copies: dict[str, str] = {}
    for name in shipped:
        entry = root / name
        pre_existing = entry.exists() or _is_link(entry)
        try:
            mine, mode = _entry(
                entry,
                source / name,
                overrides,
                reclaimed,
                copied_digest=previous.get(name) if name in previous else None,
            )
            if mode:
                modes.add(mode)
            if mine:
                ours.append(name)
                if mode == "copied" or (not mode and name in previous):
                    copies[name] = _digest(source / name)
        except OSError as exc:
            lines.append(
                f"  Note: could not materialise .claude/{subdir}/{name} ({exc})"
            )
            # Whatever is on disk is still ours: a previous copy, or the
            # half-written one this call failed part way through. The
            # old digest (or a legacy blank) rides along, so the next
            # sync treats it as stale and refreshes it.
            if name in previous or not pre_existing:
                copies[name] = previous.get(name, "")

    try:
        dropped = _prune(root, set(shipped), source, set(previous))
    except OSError:
        dropped = []
    _write_manifest(root, copies)
    _write_gitignore(root, ours)

    if reclaimed:
        # The loudest thing this step ever does: it deletes tracked
        # files out of the working tree. Never do that silently.
        lines.append(
            f"  {subdir}: reclaimed {len(reclaimed)} committed copies now"
            " shipped by a layer; commit the deletions"
        )
    if modes - {"linked"}:
        lines.append(f"  {subdir}: materialised via {', '.join(sorted(modes))}")
    if dropped:
        lines.append(f"  {subdir}: removed {len(dropped)} entry no longer shipped")
    if overrides:
        names = ", ".join(sorted(overrides))
        lines.append(
            f"  {subdir}: local override kept, shadowing the shipped copy;"
            f" commit it like any repo file ({names})"
        )
    return lines
