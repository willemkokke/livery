# livery-extensions-lfs

Git LFS for the workshop. A workspace turns it on by listing the
extension:

```toml
[workspace]
extensions = ["lfs"]
```

Listed, it does three things:

- The lock pins `git_lfs`, the git-lfs tool, so every machine and every
  CI job runs the same release of it.
- Every `fm sync` runs `git lfs install --local` once over the
  workspace, after it installs the tools. That writes LFS's hooks and
  the checkout's filter configuration where they are missing, so a sync
  that finds them in place changes nothing. A failure stops the sync
  with git-lfs's own words. A workspace that is not a git checkout gets
  nothing.
- The checkout step of the rendered GitHub and Gitea workflows sets
  `lfs: true`, so a job reads the files and not their pointers. GitLab's
  runner fetches the LFS objects by itself where its image has git-lfs.

A workspace that does not list the extension gets none of the three.

## Attribute lines

The extension ships no attribute line of its own: the extension that
ships a kind of file says which of them go through LFS. It ships the
lines in `content/for/lfs/root/.gitattributes`, beside its
`extension.toml`, and declares a `[for.lfs]` table there:

```toml
[for.lfs]
```

```text
*.png filter=lfs diff=lfs merge=lfs -text
```

`fm sync` composes those lines into the root `.gitattributes` while the
workspace lists `lfs`, and removes them when it stops listing it. A root
entry's `without` keeps one extension's lines out while `lfs` stays
listed:

```toml
[workspace]
extensions = [{ name = "assets", without = ["lfs"] }, "lfs"]
```
