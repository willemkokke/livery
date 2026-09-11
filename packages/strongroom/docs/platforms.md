# Platforms

The store imports only the standard library and runs wherever
Python does. What differs by platform is which materialiser rung a
view reaches, and what the tests prove where.

## macOS

APFS gives copy-on-write through `clonefile`, reached through
`ctypes`; a view's files are clones, private copies that cost
nothing until written. Symlinks and hardlinks work. The clone rung's
success path is proven here.

## Linux

`FICLONE` through `fcntl` gives copy-on-write on btrfs, XFS and
bcachefs; ext4 refuses it and the view falls to hardlinks, links, or
copies. Symlinks and hardlinks work. The clone wiring is proven
through the ioctl seam; its success path is proven only on a
supporting filesystem.

## Windows

Designed in, not gated: no Windows runner runs the suite, and the
Windows-only paths are exercised through their seams with fakes.

- Paths stay inside the 260-character limit by layout: about eighty
  characters from the store root to an object.
- A replace over a file a reader holds open fails; landing treats it
  as success when the destination verifies, since the bytes are the
  same by name.
- A liveness probe is never sent to a lock's holder, because
  `os.kill` there terminates the process; a lock is stale by age
  alone.
- Symlinks need Developer Mode or a privilege, so the symlink rung
  refuses and files copy; a directory symlink entry is a junction's
  case, which this implementation does not create yet, so it copies.
- Hardlinks work on NTFS.
- ReFS and a Dev Drive give copy-on-write, which this implementation
  does not wire yet; the clone rung refuses and the view falls
  through.
- NTFS is case-insensitive, which is why two names equal when case is
  ignored are refused in one tree.

## A WASI world

Only the copy rung exists, a copy into a preopened directory, and
that is enough: a confined body's inputs are a view, not a corpus.
The read-only client uses no OS facility beyond that.
