# Completion on zsh

This is a recording of a real zsh session, with the hook installed the way the
next section describes, then four <kbd>Tab</kbd> presses: the task menu, a
prefix completed, a task's options with what each one does, and a group
descended by its dotted address. Every shell's page records the *same*
session, so the pages differ only where the shells do. It is regenerated
from a live shell on every docs build, so it cannot drift from what your
terminal will do:

![Animated: fm TAB lists every task with its summary, bui TAB completes to build, --TAB shows its options each with what it does, and deploy. TAB descends the group](_generated/shots/zsh-cast.svg)

!!! note "Two zsh settings the recording assumes"

    The recording is a stock zsh apart from two lines, so that what it shows
    is zsh at its best rather than zsh out of the box:

    ```sh
    zstyle ':completion:*' menu select   # Tab walks the candidates
    unsetopt LIST_AMBIGUOUS              # ...and lists them even when Tab
                                         #    could extend the prefix
    ```

    Without the first, Tab lists but does not move a selection through the
    list. Without the second, a Tab that manages to complete a common prefix
    shows you nothing: `fm deploy -`<kbd>Tab</kbd> silently becomes `--`
    and the options stay hidden until you press again.

## Install

```console
fm --install-completion=zsh
```

This writes the hook to `$XDG_DATA_HOME/fm/completion.zsh` (default
`~/.local/share/fm/completion.zsh`) and appends one guarded `source` line to
the `.zshrc` zsh actually reads, under `$ZDOTDIR` when you've set one.
Running it twice changes nothing. If completion has never been initialised in
your setup (a fresh machine, a minimal rc), the hook runs `compinit` itself,
so there's nothing to arrange first.

For the **current session only**, with no rc file touched:

```console
eval "$(fm --setup-completion=zsh)"
```

## What you get

Footman's zsh completion hook feeds candidates through `_describe`, the same completion
builtin `_git` and `_npm` use. Task and group names carry their one-line
docstring, right-aligned into a column:

```text
$ fm <TAB>
build    -- compile and bundle
deploy   -- ship to an environment
docs     -- Documentation
```

Because it's plain `compsys`, everything you already configure for zsh
completion (menu selection, colours, group formats) applies to `fm` with no
special cases.

## Colours and appearance

All styling goes through `zstyle`. The completion context for footman's
candidates ends in the command name, so use `:completion:*:*:fm:*` to scope a
setting to `fm` alone, or `:completion:*` to style everything at once. Some
useful recipes for your `.zshrc`:

```sh
# Dim the description column (everything after the " -- " separator).
zstyle ':completion:*:*:fm:*' list-colors '=(#b)*( -- *)=0=2'

# Or colour it. 38;5;N is a 256-colour index (244 = mid grey).
zstyle ':completion:*:*:fm:*' list-colors '=(#b)*( -- *)=0=38;5;244'

# A heading above the list, in colour.
zstyle ':completion:*:descriptions' format '%F{yellow}— %d —%f'

# Arrow-key menu selection, with a visible highlight on the current row.
zstyle ':completion:*' menu select
zstyle ':completion:*:*:fm:*' list-colors 'ma=48;5;24;38;5;255'
```

The `=(#b)pattern=default=capture` syntax is zsh's `list-colors` matching:
the parenthesised group gets the second colour spec (standard ANSI SGR
codes: `2` dim, `31`-`37` foreground, `48;5;N` background). The `--`
separator itself is a compsys default; change it per command with
`zstyle ':completion:*:*:fm:*' list-separator '·'` if you prefer.

Colours here are your shell's to decide; footman emits plain
`value<TAB>description` pairs and the hook hands them to `compsys`, so any
theme (or framework like oh-my-zsh) that styles completion styles `fm` too.

## Uninstall

```console
fm --uninstall-completion=zsh
```

Removes the script and the `source` line from your `.zshrc`. (Everything the
installer did, undone; run it twice and the second run reports there's
nothing left to remove.)
