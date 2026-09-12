"""Compose the task surface: two typed verbs over one engine.

- `plugin("acme.devkit.lint")` mounts from an installed package's
  **`footman.tasks` entry point** — the console-script of task trees: a
  stable public identity for a Group the package offers, enumerable,
  inert until mounted. The longest installed entry-point name is the
  identity; the rest of the string walks the advertised tree.
- `include("mytasks.lint")` mounts from an **importable module** — the same
  grammar over your own reach: file-splitting, monorepo-local sharing.
  The longest importable prefix is imported (under a registry capture, so
  the provider's decorators can't leak); the rest walks the captured tree.

The type tag lives in the verb: no string is ever resolved against both
registries, so there is no precedence and no silent re-pointing when a new
package lands. The model is Python imports — `plugin("acme.devkit.lint")`
is `from acme_devkit import lint` for task trees; mounting a whole container
is the `import *`, safe here because local definitions silently win and
imported-vs-imported clashes are loud.

A mounted node lands under its **own name** (identity never becomes an
address); `into=` — a dotted address, auto-vivified — is the consumer's
placement. Everything after resolution (walk, land, filter, merge) is one
shared engine, and every imported node carries its provider identity as
provenance: collision messages cite it, `fm --plugins` reports it.

Everything resolves at import/manifest-build time; the completion hot path
is untouched. `importlib.metadata` is stdlib — footman stays zero-dependency.
"""

from __future__ import annotations

import importlib
import sys
from types import ModuleType
from typing import Any

from livery.footman import registry
from livery.footman.registry import Group, RegistrationError, Task, mounted_from

ENTRY_POINT_GROUP = "footman.tasks"

# One import (and capture) per provider module per process: every cascade
# file that includes the same module gets the same tree, whatever the import
# order — the `sys.modules` cache can't half-register a provider.
_module_trees: dict[str, Group] = {}


def _declares_tasks(module: ModuleType) -> bool:
    """Whether *module* holds anything whose registration cannot be rebuilt.

    A `Group` or a `@task`-stamped function is the shape `_reconstruct`
    refuses to speak for, because a task's registration IS the tree
    structure and a namespace cannot say where it was mounted. Everything
    else a provider declares — options, hooks — carries its own receipt and
    rebuilds.
    """
    return any(
        isinstance(value, Group) or hasattr(value, registry._PRE)
        for value in vars(module).values()
    )


def _tree_of_module(module: ModuleType, *, allow_empty: bool = False) -> Group:
    """The task tree of an already-imported module: the memo, a rebuild, an
    empty answer, or a taught no.

    A module imported *outside* `include()` already ran its decorators
    against whatever registry was live, and re-executing it would double
    every side effect. A contributions-only module (hooks and options, no
    tasks) can still be rebuilt from its own namespace; a module that
    defined tasks cannot, and the answer is guidance, not guesswork.

    The last two rungs are the same question the freshly-imported path asks
    a few lines below — *does this module hold anything footman can use?* —
    and they exist because it must be asked here too. An already-imported
    module that declares NOTHING was not spent: there was nothing to
    capture. Refusing it blamed an import for a tree that was always empty,
    and named the wrong module while doing it, because the empty one is
    usually a parent package `include()` is merely walking through
    (`include("devkit.tasks")` walks `devkit`) rather than the one the
    caller wrote.
    """
    name = module.__name__
    if name in _module_trees:
        return _module_trees[name]
    if (rebuilt := _reconstruct(module)) is not None:
        _module_trees[name] = rebuilt
        return rebuilt
    if _declares_tasks(module):
        # Genuinely spent: there IS something here, and the import that would
        # have captured it happened somewhere this call cannot reach.
        raise RegistrationError(
            f"include({name!r}): the module was already imported outside "
            f"include(), so its tasks were never captured — call include() "
            f"before anything else imports it, or have the module expose an "
            f"explicit Group and pass that instead"
        )
    if allow_empty:
        return Group("root")  # an intermediate package with nothing in it
    return _adopt_explicit_group(module)


def _reconstruct(module: ModuleType) -> Group | None:
    """Rebuild an already-imported, contributions-only module's tree from
    its own namespace — or `None` for a module this cannot speak for.

    The import that should have fired inside a proper load's capture is
    spent, and the capture it actually fired in is gone (it happened: a
    bare `import livery.footman.env_files` anywhere in the process left the scan
    unable to see `--env-file`, an order-dependent flake in the suite and a
    dead end for any later mount). But the declarations survive as
    module-level names, each carrying its own receipt: a `GlobalOption` is
    stamped with its defining module (`owner`), and a hook decorator writes
    the (kind, item) pairs it registered on the decorated fn
    (`registry._CONTRIBUTED`) — the exact objects, wrappers included.
    Module-level names are as far as this can see, which is the documented
    provider convention. Tasks and groups don't reconstruct — their
    registration is the tree structure itself — so any sign of either (a
    `Group`, a `@task`-stamped fn) keeps the caller's taught refusal.
    """
    name = module.__name__
    tree = Group("root")
    seen: set[int] = set()
    for value in vars(module).values():
        if isinstance(value, Group) or hasattr(value, registry._PRE):
            return None
        if id(value) in seen:
            continue  # one declaration, however many names it goes by
        seen.add(id(value))
        if isinstance(value, registry.GlobalOption):
            if value.owner == name:
                tree.contributions["globals"].append(value)
        elif getattr(value, "__module__", None) == name:
            for kind, item in getattr(value, registry._CONTRIBUTED, ()):
                tree.contributions[kind].append(item)
    if not any(tree.contributions.values()):
        return None
    if (module.__doc__ or "").strip():
        tree.help = (module.__doc__ or "").strip().splitlines()[0]
    return tree


def _adopt_explicit_group(module: ModuleType) -> Group:
    """A never-registering provider's single module-level `Group`, if any."""
    groups = [v for v in vars(module).values() if isinstance(v, Group)]
    if len(groups) == 1:
        return groups[0]
    detail = "no module-level Group" if not groups else f"{len(groups)} Groups"
    raise RegistrationError(
        f"include({module.__name__!r}): the module registered no tasks and "
        f"has {detail} to adopt — define tasks with @task/group(), or expose "
        f"exactly one Group"
    )


def _import_source(dotted: str, *, allow_empty: bool = False) -> Group:
    """Import *dotted* under `capture()` and memoise its captured tree.

    *allow_empty* tolerates a module that registers nothing and offers no
    explicit Group — an intermediate package on the way to a longer prefix
    (`include("pkg.tasks")` walks through `pkg`) is allowed to be empty; a
    terminal one keeps the taught refusal.
    """
    if dotted in _module_trees:
        return _module_trees[dotted]

    if dotted in sys.modules:
        return _tree_of_module(sys.modules[dotted], allow_empty=allow_empty)
    with registry.capture() as captured:
        try:
            module = importlib.import_module(dotted)
        except ImportError as exc:
            # A missing module (a typo, a module not on the path, a missing
            # dependency) otherwise surfaces as "failed to import <tasks.py>",
            # blaming the file and never naming the include() call that broke.
            # Name it, and the reason — the same taught shape plugin() gives. A
            # RegistrationError from the provider's own body isn't an
            # ImportError, so it propagates already-taught, untouched.
            raise RegistrationError(
                f"include({dotted!r}): failed to import ({type(exc).__name__}: {exc})"
            ) from exc
    if captured.tasks or captured.groups or any(captured.contributions.values()):
        # Tasks, groups, or lifecycle contributions alone — a hooks-only
        # provider (a `@pre_tasks` module) is a valid mount with no tree.
        tree = captured
    elif allow_empty:
        tree = captured  # an empty intermediate package: fine, walk on
    else:
        # Nothing registered at module level: the provider keeps an explicit
        # Group instead (the entry-point convention) — unambiguous only
        # because the capture came back empty.
        tree = _adopt_explicit_group(module)
    if not tree.help and (module.__doc__ or "").strip():
        # A container describes itself through its module docstring — the
        # first line becomes the root's help, so `--plugins` and group help
        # have words even for a bare bundle of groups.
        tree.help = (module.__doc__ or "").strip().splitlines()[0]
    _module_trees[dotted] = tree
    return tree


def _fork(tree: Group) -> Group:
    """A structural copy of *tree*: fresh Group objects and dicts, shared fns.

    A memoised provider tree grafted into a project is later mutated by the
    cascade overlay/tag in place — without a fork, one project's tasks (and
    DEFINING_DIR stamps) leak into the shared `_module_trees` memo and thus into
    every later in-process invocation (F38). The task callables stay shared on
    purpose: DEFINING_DIR is re-stamped on each load, so sharing them is safe
    and keeps `recording()`/identity checks meaningful.
    """
    fork = Group(tree.name, tree.help, tree.hidden, tree.expose)
    fork.tasks.update(tree.tasks)  # share fns, but into a fresh dict
    for name, sub in tree.groups.items():
        fork.groups[name] = _fork(sub)  # recurse: fresh subgroup objects
    # A faithful copy carries *every* Group field, not only tasks/groups: a
    # provider's lifecycle contributions (`@pre_tasks` hooks today) ride
    # along, provenance survives, and a runnable group keeps its
    # `@group.default` for free — the default *is* the child task named
    # `default`, so the tasks-dict copy above already carried it.
    # `test_compose`'s field census fails the moment a new Group field is
    # added but not copied here.
    fork.contributions = {k: list(b) for k, b in tree.contributions.items()}
    fork.mounted_from = tree.mounted_from
    return fork


def _stamp(node: Group, identity: str) -> None:
    """Record *identity* as provenance on every node of a mounted tree.

    Groups carry it as a field (each mount grafts fresh Group objects); task
    fns carry the marker attribute — they are shared between forks, and the
    identity is the same everywhere the same provider's fn lands.
    """
    node.mounted_from = identity
    for fn in node.tasks.values():
        setattr(fn, registry._MOUNTED, identity)
    for sub in node.groups.values():
        _stamp(sub, identity)


def _walk_subpath(
    tree: Group, segments: list[str], *, verb: str, source: str
) -> Group | Task:
    """Walk the remainder of a source string inside a provider's tree."""
    node: Group = tree
    for pos, seg in enumerate(segments):
        last = pos == len(segments) - 1
        if seg in node.groups:
            node = node.groups[seg]
            continue
        if seg in node.tasks and last:
            return node.tasks[seg]
        bad = ".".join(segments[: pos + 1])
        known = ", ".join(sorted([*node.groups, *node.tasks])) or "nothing"
        raise RegistrationError(
            f"{verb}({source!r}): no task or group at {bad!r} in the "
            f"provider's tree (has: {known})"
        )
    return node


def _load_entry_point(name: str) -> Group:
    """Load the installed `footman.tasks` entry point *name* to its Group.

    **The only place that may call `ep.load()`.** A module imports once per
    process, so its `@task` decorators and `GlobalOption` constructions fire
    inside exactly one `registry.capture()` — whoever called `load()` first.
    Every later `load()` re-resolves the cached module, runs no body, and
    captures nothing, which is why `_module_trees` memoises the tree the one
    real import produced. A second call site spends that import on itself and
    leaves the next caller holding a plugin with no tasks and no options: it
    happened, from a scan built to *describe* a plugin, and cost four tests
    that only failed when the scan happened to run first.

    Raises `RegistrationError` with a taught message for the failure shapes
    that matter: claimed by two distributions, an import-time crash (a
    missing optional dep must not dump a traceback on every `--help`), or an
    entry point that resolves to something that isn't a Group or a module
    of tasks.
    """
    from importlib.metadata import entry_points

    matches = [ep for ep in entry_points(group=ENTRY_POINT_GROUP) if ep.name == name]
    if len(matches) > 1:
        dists = ", ".join(str(ep.dist) for ep in matches)
        raise RegistrationError(
            f"plugin {name!r}: claimed by more than one distribution ({dists})"
        )
    try:
        with registry.capture() as captured:
            loaded = matches[0].load()
    except RegistrationError:
        raise  # already a taught message; don't re-wrap
    except Exception as exc:
        raise RegistrationError(
            f"plugin {name!r}: failed to import ({type(exc).__name__}: {exc})"
        ) from exc
    if isinstance(loaded, Group):
        return loaded
    if isinstance(loaded, ModuleType):
        module_name = loaded.__name__
        if captured.tasks or captured.groups or any(captured.contributions.values()):
            if not captured.help and (loaded.__doc__ or "").strip():
                # A container describes itself through its module docstring.
                captured.help = (loaded.__doc__ or "").strip().splitlines()[0]
            # Memoise under the module name so re-resolving in the same
            # process (or a later include of the same module) reuses the tree.
            _module_trees[module_name] = captured
            return captured
        # Registered nothing at module level. Reuse a memoised tree if a prior
        # resolve captured one (the entry point re-`load()`s the cached module,
        # so decorators no longer fire and `captured` comes back empty); rebuild
        # from the module's namespace when a bare import spent the one real
        # import before any proper load could capture it; otherwise adopt the
        # module's single explicit Group.
        if module_name in _module_trees:
            return _module_trees[module_name]
        if (rebuilt := _reconstruct(loaded)) is not None:
            _module_trees[module_name] = rebuilt
            return rebuilt
        tree = _adopt_explicit_group(loaded)
        _module_trees[module_name] = tree
        return tree
    raise RegistrationError(
        f"plugin {name!r}: entry point must resolve to a footman Group "
        f"(or a module of tasks), got {type(loaded).__name__}"
    )


def _resolve_plugin(source: str) -> tuple[str, Group | Task]:
    """Resolve *source* against the installed `footman.tasks` entry points.

    The longest installed entry-point name that prefixes *source* is the
    identity; the remainder walks the advertised tree. When a *shorter*
    installed prefix would also resolve fully, both readings are named on
    stderr — a new package must never silently re-point an existing mount.
    """
    from importlib.metadata import entry_points

    installed = {ep.name: ep for ep in entry_points(group=ENTRY_POINT_GROUP)}
    segments = source.split(".")
    prefixes = [
        ".".join(segments[:n])
        for n in range(len(segments), 0, -1)
        if ".".join(segments[:n]) in installed
    ]
    if not prefixes:
        names = ", ".join(sorted(installed)) or "none"
        raise RegistrationError(
            f"plugin({source!r}): no {ENTRY_POINT_GROUP!r} entry point "
            f"matches (installed: {names})"
        )
    identity = prefixes[0]
    rest = segments[len(identity.split(".")) :]
    tree = _load_entry_point(identity)
    node = _walk_subpath(tree, rest, verb="plugin", source=source)
    for shorter in prefixes[1:]:
        # Both viable? Name the two readings; the longest wins, on purpose.
        alt_rest = segments[len(shorter.split(".")) :]
        try:
            _walk_subpath(
                _load_entry_point(shorter), alt_rest, verb="plugin", source=source
            )
        except RegistrationError:
            continue
        print(
            f"note: plugin({source!r}) resolves via the entry point "
            f"{identity!r}; the shorter {shorter!r} would also resolve — "
            f"the longest prefix wins",
            file=sys.stderr,
        )
        break
    return identity, node


def _resolve_module(source: str) -> tuple[str, Group | Task]:
    """Resolve *source* against importable modules: the longest importable
    prefix is imported under capture; the remainder walks the captured tree.

    Probing runs shortest-first on purpose: `find_spec("a.b")` imports `a`
    to locate `b`, so testing the long prefix first would import the parent
    *outside* capture and poison the fallback. Importing each prefix under
    capture as we walk keeps every side effect caught, and the deepest
    module still wins.
    """
    segments = source.split(".")
    for n in range(len(segments), 0, -1):
        prefix = ".".join(segments[:n])
        if prefix in _module_trees:  # already captured this process: reuse
            try:
                node = _walk_subpath(
                    _module_trees[prefix], segments[n:], verb="include", source=source
                )
            except RegistrationError:
                # A shallower memo can be a package the last include() walked
                # *through* (`include("pkg.alpha")` memoises `pkg`'s empty
                # capture), and the next submodule of that package is not in
                # it. When the remainder still names an importable module, the
                # memo simply isn't the answer — fall through to the import
                # walk, which reuses the memo for the parent and imports the
                # child. An exact-prefix miss is a real typo: keep its words.
                if n < len(segments) and registry._importable(
                    ".".join(segments[: n + 1])
                ):
                    break
                raise
            return prefix, node
    if not (segments[0] in sys.modules or registry._importable(segments[0])):
        raise RegistrationError(
            f"include({source!r}): no importable module matches — "
            f"{segments[0]!r} is not importable"
        )
    best = 1
    _import_source(segments[0], allow_empty=len(segments) > 1)
    for n in range(2, len(segments) + 1):
        prefix = ".".join(segments[:n])
        if not registry._importable(prefix):
            break
        _import_source(prefix, allow_empty=n < len(segments))
        best = n
    prefix = ".".join(segments[:best])
    tree = _module_trees.get(prefix)
    if tree is None:  # pre-imported outside include(): the taught refusal
        tree = _tree_of_module(sys.modules[prefix])
    node = _walk_subpath(tree, segments[best:], verb="include", source=source)
    return prefix, node


def _vivify_into(into: str | Group | None, verb: str) -> Group:
    """The consumer-side landing group: root, a Group, or a dotted address
    (created on demand — placement is always the consumer's)."""
    if into is None:
        return registry.root
    if isinstance(into, Group):
        return into
    node = registry.root
    walked: list[str] = []
    for seg in into.split("."):
        walked.append(seg)
        addr = ".".join(walked)
        if seg == "default" or not seg or any(c.isspace() for c in seg):
            # `into=` names a *group* to graft into; `default` is task-typed
            # by definition, and empty/whitespace segments are not addresses.
            # For a provider's default, mount it by address instead:
            # plugin("acme.linters.default", into="lint").
            raise RegistrationError(
                f"{verb}(into={into!r}): {addr!r} cannot name a group — "
                f"'default' is a task; to adopt a provider's default, mount "
                f'it directly: {verb}("…​.default", into="<group>")'
                if seg == "default"
                else f"{verb}(into={into!r}): {addr!r} is not a legal group address"
            )
        if seg in node.tasks:
            raise RegistrationError(
                f"{verb}(into={into!r}): {addr!r} is a task — into= "
                f"names a group to graft into"
            )
        sub = node.groups.get(seg)
        if sub is None:
            sub = Group(seg)
            node.groups[seg] = sub
        node = sub
    return node


def _merge_group(dst: Group, src: Group, *, override: bool, at: str) -> None:
    """The one recursive leaf merge: two mounts into one subtree compose all
    the way down; only a same-address leaf conflicts.

    Local-vs-imported: the local leaf silently wins, whatever the order.
    Imported-vs-imported (a task-vs-task or type clash): loud unless
    `override=True` — every mount is authored, so a clash is a bug with a
    one-line fix (`exclude=`/`into=`), and loud beats silently running the
    wrong task.
    """

    def clash(name: str, theirs: Task | Group) -> None:
        addr = f"{at}.{name}" if at else name
        raise RegistrationError(
            f"{addr!r} claimed by both {mounted_from(theirs)!r} and "
            f"{src.mounted_from!r} — exclude= one side, retarget with into=, "
            f"or pass override=True to take the later mount's"
        )

    for name, fn in src.tasks.items():
        existing_t = dst.tasks.get(name)
        existing_g = dst.groups.get(name)
        if existing_t is not None:
            if mounted_from(existing_t) is None:  # local silently wins
                continue
            if not override:
                clash(name, existing_t)
            dst.tasks[name] = fn
        elif existing_g is not None:
            if existing_g.mounted_from is None:  # local group beats a mounted task
                continue
            if not override:
                clash(name, existing_g)
            del dst.groups[name]
            dst.tasks[name] = fn
        else:
            dst.tasks[name] = fn
    for name, sub in src.groups.items():
        existing_t = dst.tasks.get(name)
        existing_g = dst.groups.get(name)
        if existing_t is not None:
            if mounted_from(existing_t) is None:
                continue
            if not override:
                clash(name, existing_t)
            del dst.tasks[name]
            dst.groups[name] = sub
        elif existing_g is not None:
            # Group-vs-group is composition, never a clash: recurse. This is
            # what lets two mounts (or a mount and a local group) share one
            # namespace all the way down.
            _merge_group(
                existing_g, sub, override=override, at=f"{at}.{name}" if at else name
            )
        else:
            dst.groups[name] = sub


def _validate_filter(node: Group, address: str, verb: str) -> None:
    """Resolve one filter address segment-wise; every miss is taught."""
    current = node
    walked: list[str] = []
    for pos, seg in enumerate(address.split(".")):
        last = pos == len(address.split(".")) - 1
        walked.append(seg)
        parent = ".".join(walked[:-1]) or "the mounted node"
        if seg in current.groups:
            current = current.groups[seg]
            continue
        if seg in current.tasks:
            if last:
                return
            raise RegistrationError(
                f"{verb}(): {'.'.join(walked)!r} is a task, not a group — "
                f"nothing lives beneath it"
            )
        known = ", ".join(sorted([*current.groups, *current.tasks])) or "nothing"
        raise RegistrationError(
            f"{verb}(): no task or group at {'.'.join(walked)!r} "
            f"({parent} has: {known})"
        )


_KEEP = object()  # sentinel: keep this whole subtree


def _keep_tree(only: tuple[str, ...]) -> dict[str, Any]:
    """The `only=` addresses as a nested keep-tree.

    Union semantics: `only=["docs", "docs.build"]` is redundant, not an
    error — the whole-group entry subsumes the leaf.
    """
    tree: dict[str, Any] = {}
    for address in only:
        node = tree
        segments = address.split(".")
        for pos, seg in enumerate(segments):
            if node.get(seg) is _KEEP:
                break  # a whole-subtree keep already subsumes this address
            if pos == len(segments) - 1:
                node[seg] = _KEEP
            else:
                node = node.setdefault(seg, {})
    return tree


def _apply_only(node: Group, keep: dict[str, Any]) -> None:
    for name in list(node.tasks):
        if keep.get(name) is not _KEEP:
            del node.tasks[name]
    for name in list(node.groups):
        wanted = keep.get(name)
        if wanted is _KEEP:
            continue  # the whole subtree, help text and flags riding along
        if isinstance(wanted, dict):
            _apply_only(node.groups[name], wanted)
        else:
            del node.groups[name]


def _remove(node: Group, address: str) -> None:
    *parents, leaf = address.split(".")
    for seg in parents:
        sub = node.groups.get(seg)
        if sub is None:
            return  # an only= filter already dropped the path: nothing left
        node = sub
    node.tasks.pop(leaf, None)
    node.groups.pop(leaf, None)


def _drop_empty(node: Group) -> None:
    """A group pruned empty is dropped entirely, never grafted as a shell."""
    for name, sub in list(node.groups.items()):
        _drop_empty(sub)
        if not sub.tasks and not sub.groups:
            del node.groups[name]


def _prune(
    node: Group, only: tuple[str, ...], exclude: tuple[str, ...], verb: str
) -> None:
    """Apply `only=`/`exclude=` to the mounted node — full dotted addresses,
    matched exactly (no globs: the whole-group spelling `only=["docs"]` *is*
    the glob). Grafting a nested address materialises its path — the
    intermediate groups are the source's own forked copies — and
    default-ness survives only if the default survives, literally: the
    default is the child named `default`, so `only=["lint.python"]` grafts a
    default-less `lint`, `only=["lint.default"]` grafts *just* the default,
    and `exclude=["lint.default"]` grafts everything but it. No pointer
    bookkeeping — dropping the child *is* dropping default-ness.
    """
    for address in (*only, *exclude):
        _validate_filter(node, address, verb)
    if only:
        _apply_only(node, _keep_tree(tuple(only)))
    for address in exclude:
        _remove(node, address)
    _drop_empty(node)


def _mount(
    verb: str,
    identity: str,
    node: Group | Task,
    *,
    into: str | Group | None,
    only: tuple[str, ...] | list[str],
    exclude: tuple[str, ...] | list[str],
    override: bool,
    landing_name: str,
) -> Group:
    """The shared engine: fork, stamp, filter, land, merge."""
    target = _vivify_into(into, verb)
    if not isinstance(node, Group):
        # A single task (`plugin("acme.linters.default", into="lint")` — the
        # adopt-a-default one-liner). Filters filter children; a task has none.
        if only or exclude:
            raise RegistrationError(
                f"{verb}(): only=/exclude= filter a group's children, and "
                f"{landing_name!r} is a task — mount it bare"
            )
        setattr(node, registry._MOUNTED, identity)
        wrapper = Group("root")
        wrapper.tasks[landing_name] = node
        wrapper.mounted_from = identity
        _merge_group(target, wrapper, override=override, at="")
        return target

    fork = _fork(node)
    _stamp(fork, identity)
    _prune(fork, tuple(only), tuple(exclude), verb)
    # A provider's lifecycle contributions act on the whole merged tree
    # (a `@pre_tasks` hook edits the tree in place), so they belong on the live
    # root that discovery collects from, never the grafted subtree.
    for kind, bucket in fork.contributions.items():
        if verb == "plugin":
            for item in bucket:
                # The entry-point identity, written down on every
                # contribution — the mount always knew it, it just never
                # said so. Options carry it for the config section
                # derivation; hooks carry it so a plugin with no tasks at
                # all still reports as mounted (`--plugins` read only tree
                # provenance once, and called a riding plugin "(not
                # mounted)"). One singleton reached through two *different*
                # mounts has no single identity, and records that instead
                # (config= on an option then refuses, naming the fix).
                if kind == "globals":
                    if item._mounted is None:
                        item._mounted = identity
                    elif item._mounted != identity:
                        item._mounted = registry._MANY_MOUNTS
                elif getattr(item, registry._MOUNTED, None) is None:
                    setattr(item, registry._MOUNTED, identity)
        # By identity, not equality: forks share the provider's hook and
        # option *objects*, so a provider mounted at two addresses arrives
        # here twice with the same items — and a second registration would
        # run its lifecycle twice per run, silently, side effects and all.
        # The tree mounts twice; the contribution contributes once.
        existing = registry.root.contributions[kind]
        for item in bucket:
            if not any(item is have for have in existing):
                existing.append(item)
        bucket.clear()
    if fork.name == "root":
        # An anonymous container (a module capture's root): mounting it lands
        # its *children* — the splat, `import *` for task trees. A devkit
        # update that adds a group just appears on the next mount.
        _merge_group(target, fork, override=override, at="")
        return target
    # A named node lands under its own name; identity never becomes an
    # address. Compose with an existing group of that name rather than
    # clobbering it — an existing task of that name is the usual leaf clash.
    wrapper = Group("root")
    wrapper.groups[fork.name] = fork
    wrapper.mounted_from = identity
    _merge_group(target, wrapper, override=override, at="")
    return target


def plugin(
    source: str,
    /,
    *,
    into: str | Group | None = None,
    only: tuple[str, ...] | list[str] = (),
    exclude: tuple[str, ...] | list[str] = (),
    override: bool = False,
) -> Group:
    """Mount a task tree from an installed package's `footman.tasks` entry
    point — **entry points only**; for your own modules use `include()`.

    ```python
    plugin("acme.devkit")                      # the import *: every group, top level
    plugin("acme.devkit.lint")                 # from acme_devkit import lint
    plugin("acme.ci", exclude=["deploy"])      # everything but one child
    plugin("footman.docs", into="footman")     # built-ins are ordinary plugins
    plugin("acme.linters.default", into="lint")  # adopt a provider's default
    ```

    The longest installed entry-point name is the *identity* (consumed at
    resolve time, retained as provenance); the rest of the string walks the
    advertised tree. The mounted node lands under its **own name** — `into=`
    (a dotted address, created on demand) is the consumer's placement, and
    there is no rename. Returns the group grafted into.
    """
    identity, node = _resolve_plugin(source)
    return _mount(
        "plugin",
        identity,
        node,
        into=into,
        only=only,
        exclude=exclude,
        override=override,
        landing_name=source.rsplit(".", 1)[-1],
    )


def include(
    source: str | ModuleType | Group,
    /,
    *,
    into: str | Group | None = None,
    only: tuple[str, ...] | list[str] = (),
    exclude: tuple[str, ...] | list[str] = (),
    override: bool = False,
) -> Group:
    """Mount a task tree from an importable module — **modules only**; for an
    installed package's advertised tasks use `plugin()`.

    ```python
    include("shared_tasks")                    # everything, at root
    include("shared_tasks", only=["lint"])     # cherry-pick a child
    include("mytasks.lint")                    # one group out of a module
    include("mkdocs_helpers.tasks", into="docs")  # namespaced: fm docs.…
    ```

    The longest importable prefix is imported under a registry capture (the
    provider's decorators can't leak into your tree); the rest of the string
    walks the captured tree. Collisions are loud (`RegistrationError`)
    unless `override=True`; your own definitions silently win. Included
    tasks run from *your* file's directory — a shared lint task lints this
    project. A `Group` or imported module may be passed programmatically
    (tests, generated trees). Returns the group grafted into.
    """
    node: Group | Task
    if isinstance(source, Group):
        identity, node = source.name, source
        landing = source.name
    elif isinstance(source, ModuleType):
        identity, node = source.__name__, _tree_of_module(source)
        landing = source.__name__
    else:
        identity, node = _resolve_module(source)
        landing = source.rsplit(".", 1)[-1]
    return _mount(
        "include",
        identity,
        node,
        into=into,
        only=only,
        exclude=exclude,
        override=override,
        landing_name=landing,
    )
