<!-- Seeded from the template channel (package-python kind) at
     birth; this file is the workspace's own. Edit it directly:
     the template never rewrites it.
-->
# livery-strongroom

Content-addressed storage: one address space, every tenant.

The store names bytes by their digest, keeps trees and versions as
blobs in specified formats, moves refs by compare-and-swap with a
record beside each, and hands a real path to a program that needs
one. It knows no tool, no call and no dataset. It imports only the
standard library.

The standard lives in `spec/`, with golden vectors; the Python here
is its reference implementation.
