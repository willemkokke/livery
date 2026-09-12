# The materialiser

The only route from a digest to a path. Behaviour, pinned by the
scenarios in `conformance/views.json` and by the rules below; which
rung an implementation reaches on a given platform is not conformance
material, the rules that decide eligibility are.

## The API

- `path(blob)` hands over a path into a read-only tier: the reference
  rung, nothing created.
- `view(tree, at, writable=False)` fills a directory; the caller says
  whether it will write, and the materialiser picks the cheapest safe
  rung per entry and records which one it used.
- `collect(at, declared, executable=())` reads declared outputs back
  into a tree, landing every object; `executable` names the outputs
  that are executable where the file mode cannot say.
- `drop_view(id)` removes what the view's record lists and nothing
  else.

## The ladder

Cheapest first, each with the rule that decides its eligibility. A
rung that refuses once is not tried again for that view.

1. **reference**: a path into a read-only tier. Zero copy. Eligible
   only when the tier is read-only at the server, or the store
   directory is owner-only and the view is not writable. This is
   `path`, never an entry inside a view.
2. **clone**: copy-on-write. APFS `clonefile`, Linux `FICLONE` on
   btrfs, XFS and bcachefs, ReFS block cloning on Windows. Always
   safe: the clone is a private copy that costs nothing until
   written. A filesystem without it refuses, and the view falls to
   the next rung.
3. **hardlink**: a second name for the object's inode, marked
   read-only. Refused into a writable view unless the caller allows
   it for consumers known never to write in place: a write in place
   would corrupt the store's object.
4. **link**: a relative symlink to the object in its tier. Refused
   from a writable view, because a program could write through it;
   the target must be a read-only tier.
5. **copy**: always available. The last rung on NTFS without ReFS,
   and the only rung in a WASI world.

An executable entry never uses hardlink or link, because both share
the target's mode and the object's own mode must not change. A clone
or a copy takes the entry's mode: read-only unless the view is
writable, executable when the entry says so.

## The executable bit

A blob entry's `executable` flag is part of the tree, never of the
object: the same bytes with and without it are one object and two
trees. `view` sets it through the file mode where the platform has
one. `collect` learns it by a ladder: the caller's declaration first,
on every platform, so one call lands one tree everywhere; then the
file mode where it carries the bit; where it does not, the view
record's answer for a path the view made, then the platform's own
reading of a new file, which on Windows is the extension (`.exe`,
`.bat`, `.cmd`, `.com`); then false. A script a program writes on
Windows arrives elsewhere without its bit unless the caller declares
it: nothing on that platform can know.

## Symlink entries

A link never leaves its view. `view` parks an entry whose target
resolves above the view's root, with the note "target escapes the
view", and creates nothing, on every platform; a subtree viewed on
its own has no parent, so its upward links park. Inside the view a
target may step up and down freely. Where the platform makes
symlinks, the entry is a real symlink with the entry's target,
spelled with the platform's separator; where it refuses, the
within-view target's content as a marked copy, or a parked refusal
when the target is absent. The record says which, and why.

`collect` reads a link's target back in the tree's spelling. A target
inside the view is content: an absolute one, which a program may
write, is recorded as the relative path from the link's own
directory. A target outside the view is the link rung's own symlink
into the store, read through as the blob it presents, when the view's
record says so or when it lands under the collecting store's root.
Any other target outside the view is refused, naming the path; a
caller who means it leaves that path out of the declared outputs.

## The path budget

A path inside a view is at most 1024 UTF-8 bytes, separators
included, enforced when a view is planned and when outputs are
collected, so a tree is refused where the producer can fix it.

## The record and the removal doctrine

A view's record is local state in the implementation's index, never a
format: the tree, the root, whether it is writable, every path
created with its rung, and every directory created. A view is a root
while its directory exists; one whose directory is gone is retired by
the next sweep.

The materialiser removes only what it created and can prove it
created: the paths its record lists. A path inside the view the
record does not list is left and named. A directory that stays
non-empty is left and named, the view's root included. A view whose
directory is already gone is retired with nothing removed.

## Warming and shedding

`prefetch(digest)` fetches the object and everything it reaches, so a
machine warms exactly what one ref needs before it disconnects. A miss
names the first digest no source answered.

`shed(source)` evicts the local copy of every object the named source
holds, checked by existence in a folder, a HEAD on an HTTP source, or
the digest an origin hint names, never by trust. Objects a live view
reaches through a hardlink or a link are kept. Integrity is unchanged:
the next fetch verifies on arrival, and a source that lied costs a
named miss. Availability is the cost, and the caller chose it.

## Windows

Paths stay inside the budget by layout. Whether a process can make a
symlink depends on Developer Mode or elevation: with either, a
symlink entry is a real link, its target spelled with backslashes on
the way out and forward slashes on the way back, so a view
round-trips; without, the rung refuses and the within-view content
copies. Hardlinks work on NTFS. ReFS or a Dev Drive gives
copy-on-write, which this implementation does not wire yet, so the
clone rung refuses and the view falls through. The file mode has no
executable bit: `view` cannot set one, and `collect` takes the bit
from the declaration, the record, or the extension, as the ladder
above says. Windows refuses to unlink a file marked read-only, and a
view marks every clone, copy, and hardlink so; dropping a view and
evicting an object clear the mark on refusal and remove again.
