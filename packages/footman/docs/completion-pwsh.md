# Completion on PowerShell

This is a recording of a real pwsh session — the completion hook loaded
the way the next section describes, with <kbd>Tab</kbd> bound to
`MenuComplete`, then four <kbd>Tab</kbd> presses: the task menu, a prefix completed, a
task's options with what each one does, and a group descended by its
dotted address. Every shell's page records the *same* session, so the
pages differ only where the shells do. Regenerated from a live shell on
every docs build, so it cannot drift from what your terminal will do:

PSReadLine shows each candidate's description as the tooltip line
under the grid rather than in a column beside it.

![Animated: fm TAB opens PSReadLine's completion menu of tasks, bui TAB completes to build, --TAB shows its options, and deploy. TAB descends the group](_generated/shots/pwsh-cast.svg)

!!! note "One binding the recording assumes"

    <kbd>Tab</kbd> cycles completions inline by default; the grid with
    tooltips is `MenuComplete`, which is the completion story worth watching:

    ```powershell
    Set-PSReadLineKeyHandler -Key Tab -Function MenuComplete
    ```

    Put it in your `$PROFILE` to keep it. One PSReadLine habit worth knowing
    while the menu is open: the next key you press *replaces* the selection,
    so take the entry you want with <kbd>→</kbd> before typing the next word.

## Install

```console
fm --install-completion=pwsh        # `powershell` works too
```

This writes the hook to `$XDG_DATA_HOME/fm/completion.ps1` and dot-sources it
from the profile PowerShell itself reports (`$PROFILE`). On a Windows machine
with **both** PowerShell 7 (`pwsh`) and Windows PowerShell 5 (`powershell`),
each keeps its own profile — so the installer adds the line to both, and
completion works in whichever one you open. The hook itself runs unchanged on
either. Running the installer twice changes nothing, and a UTF-16 profile
(Windows PowerShell's habit) is appended in UTF-16, not corrupted.

For the **current session only** — no profile touched:

```console
fm --setup-completion=pwsh | Out-String | Invoke-Expression
```

## What you get

The hook registers a native argument completer whose results carry a
**tooltip** — each task's one-line docstring. How much of that you *see*
depends on PSReadLine's completion mode:

- Default (`Tab` cycles candidates in place): names only, no tooltips.
- **Menu completion** shows the grid *and* the tooltip of the highlighted
  candidate below it — this is the mode worth turning on:

```powershell
Set-PSReadLineKeyHandler -Key Tab -Function MenuComplete
```

```text
$ fm <TAB>
build   deploy   docs   lint
compile and bundle
```

Add it to your profile (`notepad $PROFILE`) to make it stick.

## Colours and appearance

Menu completion is drawn by PSReadLine, so its colours come from
`Set-PSReadLineOption -Colors`. Two entries matter here:

```powershell
Set-PSReadLineOption -Colors @{
    Selection = "`e[7m"           # the highlighted candidate (reverse video)
    Emphasis  = "`e[38;5;214m"    # the matched characters while filtering
}
```

Values are ANSI escape sequences (`` `e `` is the escape character in
PowerShell 7; use `$([char]27)` on Windows PowerShell 5) or console colour
names like `"DarkCyan"`. Tooltips render in the default text colour, and
`Set-PSReadLineOption -ShowToolTips:$false` hides them entirely if you'd
rather not see them.

## Uninstall

```console
fm --uninstall-completion=pwsh
```

Removes the script and the dot-source line from every PowerShell profile the
installer touched. If PowerShell itself is gone from PATH, the script is
still removed and the leftover profile line is printed so you can delete it
by hand.
