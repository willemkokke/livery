---
icon: lucide/hammer
---

# Using the tools

Every tool is a handle; every handle speaks the same grammar.

## The translation

Keyword arguments become flags mechanically:

- `fix=True` → `--fix` (`False`/`None` → omitted entirely)
- `strict=off` → `--no-strict` — `off` is the `livery.toolroom.off` sentinel
  for disabling a default-on flag; `no_strict=True` spells the same
  thing by name
- `output_format="github"` → `--output-format github`
- `select=["E", "F"]` → `--select E --select F` (an empty list or
  tuple is omitted, so a parameter's default passes straight through)
- `x=1` (single letter) → `-x 1`
- a trailing underscore escapes Python keywords: `import_="x"` →
  `--import x`

Positional strings pass through verbatim, and attribute access chains
subcommands:

<!-- example: fragment -->
```python
from livery.toolroom import docker

docker.compose.up(detach=True)  # docker compose up --detach
```

Any executable works without being declared: `livery.toolroom.terraform("plan")`
runs `terraform plan`. Tools with quirks are curated — `eclint` takes
single-dash long flags, `mkdocs build` negates `clean` as `--dirty`,
`python` means the running interpreter, never whatever `python` is on
`PATH` — and the curation rides every call. Point a handle at a
different executable deliberately with `.at()` (`python.at(venv_python)`);
ambient intent is spelled `livery.toolroom.python3`.

## Results and failures

A call answers in `Result`: an `int` subclass that *is* the exit code,
carrying `stdout`, `stderr`, `ok`, `code`, and `to_argv()`. Standalone,
a non-zero exit raises `ToolError` — which carries the `Result` — so
the default reading of a tool call is "this worked":

<!-- example: fragment -->
```python
from livery.toolroom import git, ToolError

try:
    sha = git("rev-parse", "HEAD").stdout.strip()
except ToolError as err:
    print(err.result.stderr)
```

Tolerate failure deliberately with `nofail`:

<!-- example: fragment -->
```python
r = git.opts(nofail=True).push()
if not r.ok:
    ...
```

## Secrets show as `***`

A value that redacts itself — footman's `Secret`, or any `str` subclass
carrying a `reveal()` method — is never shown by default. The child
process receives the real bytes; every surface built to be *read*
spells it `***`: the shown line, `Result.command`, a `ToolError`
message, and (hosted) footman's receipts, which see the marker because
the bridge passes a `str` through as its own type rather than
flattening it:

<!-- example: fragment -->
```python
from footman import Secret
from livery.toolroom import docker

docker.login("ghcr.io", username="ci", password=Secret("hunter2"))
# shown and recorded: docker login ghcr.io --username ci --password ***
# executed: the real token, which only the child ever sees
```

Redaction is of the display, never the execution — and unwrapping is
the caller's to do. `str(secret)`, an f-string, or `reveal()` all pass
in the clear, because a string operation on a `Secret` answers in plain
`str`: that is footman's documented way of saying you meant it. The
same line holds for `.argv` and `to_argv()` — a built command line
carries real values, because it exists to be executed, not shown.

toolroom never imports footman for any of this: the marker is
duck-typed on `reveal()`, so the redaction works standalone and for any
type that opts into the same shape.

## `.opts()` — run policy

Policy rides *beside* the call and never becomes a tool flag, so a
tool's own `--capture` (pytest has one) can't collide:

<!-- example: fragment -->
```python
pytest.opts(capture=False)("-s")  # stream live
git.opts(cwd=repo_dir).status()  # root this call elsewhere
uv.pip.install.opts(input=reqs)("-r", "-")  # feed stdin, exactly once
```

The vocabulary: `nofail`, `capture`, `input`, `env`, `cwd`, `rel`,
`timeout`, and — meaningful under a footman host — `in_process`,
`title`, `recorded`, `pre_record`. Standalone, the reporting-lane
options are accepted and ignored; `in_process=True` as a demand is a
taught refusal, because the in-process lane belongs to the host.

## `.flags()` — a tool's own globals

Some flags belong to the tool, not the verb, and must precede it:

<!-- example: fragment -->
```python
docker.flags(host="tcp://x").ps(all=True)
#  -> docker --host=tcp://x ps --all
```

## `.at()` — a different executable

`python.at(venv_python)` is that venv's interpreter carrying python's
whole typed surface. Identity, policy, and the tool's own flags are
three separate channels: `.at()`, `.opts()`, `.flags()`.

## `.argv` — build, don't run

Insert `.argv` before the parentheses and the call hands back an
`Argv` — the raw tokens, an ordinary `list[str]`:

<!-- example: fragment -->
```python
cmd = mkdocs.gh_deploy.argv(force=True)
#  -> Argv(['mkdocs', 'gh-deploy', '--force'])

ssh("deploy@host", cmd.posix())  # quote for the shell that parses it
uv.run("--", *cmd)  # or splat the tokens on
```

`.posix()` and `.windows()` quote for the *destination*, never this
machine — a line built on Windows for a Linux box still comes back
POSIX-quoted.

## Versions

`tool.installed_version()` asks the binary itself and answers as a
comparable int tuple, cached per process, resolved outside any host so
dry-run and recording can't lie to it:

<!-- example: fragment -->
```python
if ruff.installed_version() >= (0, 14):
    ...
```
