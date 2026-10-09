<!-- Seeded from the package-base seeds at
     birth; this file is the workspace's own. Edit it directly:
     nothing rewrites it.
-->
# livery-extensions-claude

What Claude Code reads in a workspace. A workspace turns it on by
listing the extension:

```toml
[workspace]
extensions = ["claude"]
```

`fm sync` then writes three things:

- `CLAUDE.md`, committed: an import line for each guidance fragment in
  section order, the repository's own `fragments/` after them, then
  `CLAUDE.project.md`, the repository's own facts. Edit
  `CLAUDE.project.md`, never `CLAUDE.md`.
- `.workshop/fragments/`, this checkout's alone: the guidance every
  listed extension ships or renders, for the agent.
- `.claude/`, this checkout's alone: a link to each skill and hook
  every listed extension ships under `content/skills/` and
  `content/hooks/`, and a copy of the one `settings.json`. A copy the
  agent's tooling edits is a local override, kept and named.

The settings wire Claude Code's hooks to the extension's verbs through
one shim, `.claude/hooks/fm-hook.sh`:

- `fm hooks.pre-bash` refuses a footman command piped into `tail` or
  `head`, and a push of a branch that conflicts with `origin/main`.
- `fm hooks.post-edit` runs the gate's fixers over the edited file.
- `fm hooks.stop` refuses to end a turn whose last command was a
  failed `fm` verdict, once.

A project born while the extension is listed starts with a
`CLAUDE.project.md`; `fm new.project` lists it only when asked. A
workspace that does not list it has no `CLAUDE.md` and no `.claude/`.
