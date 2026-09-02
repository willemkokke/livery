#!/usr/bin/env bash
# The one shim between Claude Code's hooks and their fm tasks.
#
# Two rules, both learned the hard way:
#
# 1. Prefer `fm` on PATH over `uv run fm`. `uv run` parses
#    pyproject.toml before launching anything, so a conflict marker
#    there makes uv itself exit 2 - which the harness reads as a
#    block - and every Bash command dies, including the ones that
#    would resolve the conflict. The PATH binary also skips ~300ms
#    of resolver on a hook that runs before every Bash call.
# 2. A guard that cannot run must not deny. Only exit 2 is the
#    hook's own policy refusal and is propagated; every other
#    failure (uv missing, TOML broken, interpreter gone) becomes 0.
#    Failing closed on infrastructure failure turns a bad config
#    into an unrecoverable session.
hook="$1"
if command -v fm >/dev/null 2>&1; then
  fm "hooks.${hook}" 1>&2
else
  uv run --no-sync fm "hooks.${hook}" 1>&2
fi
code=$?
if [ "$code" -eq 2 ]; then
  exit 2
fi
exit 0
