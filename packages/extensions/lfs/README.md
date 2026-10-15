# livery-extensions-lfs

Git LFS for the livery workshop. Listed as `lfs` in a workspace's
`[workspace] extensions`, it pins git-lfs in the lock, installs LFS's
hooks and filter in the checkout on every `fm sync`, and makes the
rendered CI checkout fetch the LFS objects.
