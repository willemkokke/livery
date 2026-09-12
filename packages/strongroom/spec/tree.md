# The tree

A directory as a manifest: git's tree with sizes. Vectors:
`vectors/tree.json`.

## Shape

```json
{"entries": [entry, ...]}
```

An entry names a blob, a subtree, or a symlink:

| Kind | Fields |
| --- | --- |
| `blob` | `name`, `kind`, `digest`, `size`, `executable` |
| `tree` | `name`, `kind`, `digest`, `size`, `executable` (always `false`) |
| `symlink` | `name`, `kind`, `target` |

Exactly those keys, no more and no fewer. `digest` is a digest string
([digest.md](digest.md)). `size` is the blob's byte count, or for a
subtree the byte count of its canonical JSON, so a materialiser plans
and verifies a view without opening a blob. `executable` is a boolean.
`target` is the link's whole content: no digest, no size.

Entries sort by the UTF-8 bytes of `name`, ascending. A tree whose
entries are not in that order is refused on decode.

A tree's bytes are its canonical JSON ([canonical.md](canonical.md)),
and its digest is the digest of those bytes. A tree is a blob in the
store; the referrer (a tree entry of kind `tree`, a version's `tree`)
says to read it as a tree. Decoding refuses bytes that are not already
canonical: the same tree with a space in it would be a second name for
the same content.

A Merkle tree over directories: an unchanged subtree is one digest
comparison, a partial checkout is a subtree fetch, and unchanged
directories dedup for free. A flat listing is a derived view, never
the stored form.

## Portable names

A name is one path component, refused at publish where the producer
can still fix it, never discovered at checkout on the platform that
cannot represent it:

- not empty, and not `.` or `..`;
- Unicode NFC;
- at most 255 UTF-8 bytes;
- no `/`, no `\`, no NUL, no character below U+0020, and none of
  `< > : " | ? *`;
- no trailing dot and no trailing space;
- not a Windows-reserved stem in any case, with or without an
  extension: `CON`, `PRN`, `AUX`, `NUL`, `COM1` to `COM9`, `LPT1` to
  `LPT9`. `con.txt` is refused with `CON`.

Within one tree, no two names are equal when case is ignored
(Unicode case folding), because NTFS and APFS by default are
case-insensitive and would collapse them.

The total path budget, 1024 UTF-8 bytes with separators, is enforced
when a view is planned and when outputs are collected, not per
subtree: a subtree cannot know its depth
([materialiser.md](materialiser.md)).

## Symlink entries

The target is content, inline in the hashed entry. Rules, in the
portable-names spirit:

- not empty;
- Unicode NFC;
- forward slashes only: a backslash or a NUL is refused;
- relative always: a target starting with `/`, a drive letter and
  colon, or `\\` is refused. It names one machine's disk.

`..` steps are allowed in a target, because a subtree cannot know
where it is mounted. A target that leaves the view it is made in is
parked by the materialiser and refused on collect
([materialiser.md](materialiser.md)), never by the format. Two links
to the same target dedup as part of their tree like any other entry.

## Refusals

| Input | Rule |
| --- | --- |
| entries out of name-byte order | not sorted |
| `a` and `A`, or the same name twice | same name when case is ignored |
| `CON`, `con.txt`, `LPT9` | Windows-reserved |
| `x.`, `x ` | ends in a dot or a space |
| `a/b`, `a\b`, `a:b`, a control character | separator, control character, or one of `<>:"\|?*` |
| an empty name; `.`; `..` | empty; relative directory marker |
| a name in NFD; 256 bytes | not in Unicode NFC; longer than 255 UTF-8 bytes |
| `size` as a float, a boolean, or negative | not an integer; negative |
| `executable` as a string; `true` on a tree | not a boolean; cannot be executable |
| kind `dir`; a missing or extra key; a malformed digest | not blob, tree or symlink; wrong keys; the digest rule |
| target `/etc/passwd`, `C:/x`, `dir\file`, empty, NFD | absolute; backslash; empty; not in Unicode NFC |
