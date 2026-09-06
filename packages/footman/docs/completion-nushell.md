# Completion on nushell

This is a recording of a real nushell session — the external completer
wired the way the next section describes, then four <kbd>Tab</kbd> presses: the task menu, a prefix completed, a
task's options with what each one does, and a group descended by its
dotted address. Every shell's page records the *same* session, so the
pages differ only where the shells do. Regenerated from a live shell on
every docs build, so it cannot drift from what your terminal will do:

![Animated: fm TAB opens nushell's completion menu with a description column, bui TAB completes to build, --TAB shows its described options, and deploy. TAB descends the group](_generated/shots/nushell-cast.svg)

## Install

```console
fm --install-completion=nushell     # `nu` works too
```

This writes the completion hook to `$XDG_DATA_HOME/fm/completion.nu` and appends one
guarded `source` line to the config nushell itself reports
(`$nu.config-path`). The hook registers an **external completer** — and it
*wraps* whatever external completer you already run (carapace, a fish
bridge, …) instead of replacing it: `fm` lines are answered by footman,
every other command passes through untouched. Running the installer twice
changes nothing.

`fm --setup-completion=nushell` prints the same hook for a session-only
try-out — nushell sources it rather than `eval`-ing it, so save it to a
file and `source` that. For everyday use, install and `exec nu` (or open
a new shell).

## What you get

The hook returns `{value, description}` records, so nushell's completion
menu shows each task and group with its one-line docstring in the
description column, filtered as you type:

```text
$ fm <TAB>
build      compile and bundle
deploy     ship to an environment
docs       Documentation
```

## Colours and appearance

The menu is nushell's `completion_menu`, styled from your config
(`config nu` opens it). Override the menu entry to restyle it — this is
plain nushell configuration, nothing footman-specific:

```nu
$env.config.menus ++= [{
    name: completion_menu
    only_buffer_difference: false
    marker: "| "
    type: {
        layout: columnar        # or `list` for one candidate per row
        columns: 4
        col_width: 20
        col_padding: 2
    }
    style: {
        text: green                       # candidate text
        selected_text: green_reverse      # the highlighted candidate
        description_text: yellow          # the docstring column
        match_text: { attr: u }           # matched characters (underlined)
        selected_match_text: { attr: ur }
    }
}]
```

Styles take named colours (`green`, `yellow_bold`), hex strings
(`"#ffb86c"`), or records with `fg`/`bg`/`attr` (`u` underline, `r`
reverse, `b` bold). A `layout: list` menu gives the description column the
most room; `columnar` packs more candidates per screen.

## Uninstall

```console
fm --uninstall-completion=nushell
```

Removes the script and the `source` line from your nushell config. Your
previous external completer (if the hook wrapped one) is back in sole
charge on the next shell start.
