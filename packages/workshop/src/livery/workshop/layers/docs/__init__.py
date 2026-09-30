"""The docs layer: the documentation site's assembly, verbs, slots and staged assets.

A layer inside the workshop wheel, mounted through ``[workspace]
layers`` as ``livery.workshop.layers.docs`` with ``livery-workshop``
as its distribution. Its task surface arrives through its own
``footman.tasks`` entry point; the base never imports it, and reads
the facts it needs of a package's docs from
[livery.workshop._docs_contract][] and the nav blocks from
[livery.workshop._navblocks][].
"""
