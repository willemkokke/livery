# Completion on fish

This is a recording of a real fish session — the completion hook loaded
the way the next section describes, then four <kbd>Tab</kbd> presses: the task menu, a prefix completed, a
task's options with what each one does, and a group descended by its
dotted address. Every shell's page records the *same* session, so the
pages differ only where the shells do. Regenerated from a live shell on
every docs build, so it cannot drift from what your terminal will do:

![Animated: fm TAB lists every task with its summary in fish's pager, bui TAB completes to build, --TAB shows its described options, and deploy. TAB descends the group](_generated/shots/fish-cast.svg)

## Install

```console
fm --install-completion=fish
```

This writes one file, `~/.config/fish/completions/fm.fish`, and that's the
whole install — fish auto-loads that directory, so there is no rc file to
edit and nothing to source. Running it twice changes nothing.

For the **current session only**, without writing the file:

```console
fm --setup-completion=fish | source
```

## What you get

fish renders completion candidates with their descriptions natively — footman
emits `value<TAB>description` pairs and fish's pager does the rest, including
fuzzy filtering as you keep typing:

```text
$ fm <TAB>
build  (compile and bundle)  deploy  (ship to an environment)  docs  (Documentation)
```

Task and group names carry their one-line docstring; flags and choice values
complete as plain candidates.

## Colours and appearance

The completion pager has its own colour family, `fish_pager_color_*`. Set
them universally (`set -U` persists across sessions, no config file needed):

```fish
set -U fish_pager_color_description yellow      # the (description) text
set -U fish_pager_color_completion  normal      # the candidate itself
set -U fish_pager_color_prefix      cyan --bold # the part you already typed
set -U fish_pager_color_selected_background --background=brblack
set -U fish_pager_color_progress    brwhite --background=cyan
```

Colours take fish's named colours (`red`, `brred`, …), hex values
(`set -U fish_pager_color_description ffb86c`), and modifiers like `--bold`,
`--italics`, `--underline`. `fish_config` opens a browser UI with the same
knobs under *colors*, and any fish theme that styles the pager styles `fm`'s
completions with it — footman adds nothing shell-specific.

## Uninstall

```console
fm --uninstall-completion=fish
```

Deletes `~/.config/fish/completions/fm.fish` — the one thing the installer
created.
