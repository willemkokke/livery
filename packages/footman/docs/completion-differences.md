# Shell differences

All five shells get the same candidates from the same resolver, and footman
leans on candidate *shape*, never on any one shell's options, so path-style
completion works everywhere. What differs is how each shell renders and
inserts those candidates. This page lists the differences you can actually
observe; nothing here changes what completes, only how it looks and feels.

## Descriptions

The resolver emits `name<TAB>summary` per candidate.

- **zsh, fish, nushell** render the summary: zsh and fish as a description
  column, nushell in its completion menu.
- **pwsh** shows the summary as a tooltip.
- **bash** has no description column; it keeps the name and drops the rest.
  This is why a group candidate always carries its trailing dot (`docs.`):
  in bash the dot *is* the descend-vs-run signal.

## The space after a unique match

Shells disagree about appending a space when exactly one candidate matches:

- **bash** and **zsh** support per-candidate no-space behaviour.
- **fish** and **nushell** append a space after any unique match.

Footman sidesteps the disagreement by never *needing* a no-space flag: when
your prefix matches a single namespace group, the resolver answers with the
group's children as full addresses (`fm do<TAB>` → `docs.build`,
`docs.serve`), so the candidate set stays non-unique and every shell holds
the cursor in-word. In the worst case, where a space lands after `fm docs.`
anyway, running it answers with the group's tasks, so nothing strands.

## Menus and selection

- **zsh** users with `menu select` arrow through candidates; its styles
  (colours, grouping) apply to `fm` like any other completer.
- **fish** pages candidates with its own pager.
- **nushell** and **pwsh** pop their native completion menus.
- **bash** lists candidates as plain columns on a second <kbd>Tab</kbd>.

## Word splitting

- **bash** splits `--opt=value` into three words at the `=`; the resolver
  reassembles them. zsh and fish pass the token whole. You'll never notice,
  but it explains stray `=` tokens if you ever watch `fm --complete` traffic.
- No shell footman supports word-breaks on `.`, which is what makes the
  dotted address one completion unit everywhere. (This is also why the
  separator is a dot and not the `:` some task runners use: `:` *is* a
  word-break character in bash's default `COMP_WORDBREAKS`.)

## File paths

Path-valued positions hand off to each shell's own file completion, so
path candidates always look native: dirs get `/`, quoting matches your
shell, and remote/fancy path plugins keep working.

Mid-list in a comma-splitting path value (`--paths=a,<TAB>`), bash, zsh,
fish, and pwsh complete the segment after the last comma with the typed
items kept in place. nushell's external-completer protocol has no
version-stable way to rewrite part of a token, so it completes the first
item natively and stays silent after a comma.

## Continuing a list

Accepting one item of a comma-splitting value doesn't end the list, and
each shell continues it in its own accent:

- **zsh** writes the comma as a removable suffix: accept `eu`, get
  `--regions=eu,`, and Tab chains straight into the next item — while a
  space or ++enter++ takes the comma back off, so stopping costs nothing.
- **bash** holds the cursor right after the accepted item (no trailing
  space), so the comma — or the closing space — is your next keystroke.
  The elderly bash 3.2 that macOS ships has no `compopt` and keeps its
  trailing space, exactly as before.
- **fish** carries the comma on each candidate: its pager shows
  `--regions=eu,` and inserts it with the cursor glued. A comma left on
  the final item is harmless — the value parses the same without it.
- **pwsh** and **nushell** already land the cursor right after every
  completion they insert, which is this behaviour by default.

## When the tasks file is broken

A tasks file that fails to import cannot offer tasks, and each shell says
so as loudly as it can:

- **zsh** shows the import error as a message line — nothing is offered
  for insertion.
- **fish**, **nushell** and **pwsh** re-offer the word you typed with the
  error beside it (description column or tooltip); accepting it changes
  nothing on the line.
- **bash** has no way to display a line without offering to insert it, so
  it stays silent — but it no longer falls back to offering filenames as
  if they were tasks.

The full error, traceback included, is one `fm --list` away, and the note
clears itself within seconds of the file importing again.
