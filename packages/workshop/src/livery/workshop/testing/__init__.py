"""What a layer's kinds and checks are judged against: the conformance kit.

A layer registers kinds, checks and category tables that the gate
then relies on. The kit states what the gate relies on, one clause at
a time, and judges a layer's registrations against it: a
[livery.workshop.testing.Subject][] names what the layer registers,
each [livery.workshop.testing.Clause][] returns the
[livery.workshop.testing.Violation][]s the subject commits, and
[livery.workshop.testing.CLAUSES][] lists them in order. A layer's own
suite runs every clause on its subject after the layer's
registrations ran:

```python
import pytest

from livery.workshop.testing import CLAUSES, Clause, Subject

SUBJECT = Subject("acme.layer", kinds=(...), checks=(...))


@pytest.mark.parametrize("clause", CLAUSES, ids=lambda clause: clause.name)
def test_the_layer_conforms(clause: Clause) -> None:
    assert clause.judge(SUBJECT) == []
```

The workshop's own kinds and checks run the same clauses through
[livery.workshop.testing.builtin_subject][], so the kit cannot drift
from what the gate enforces.
"""

from __future__ import annotations

from livery.workshop.testing._conformance import (
    CLAUSES,
    Clause,
    Subject,
    Violation,
    builtin_subject,
    judge,
)

__all__ = [
    "CLAUSES",
    "Clause",
    "Subject",
    "Violation",
    "builtin_subject",
    "judge",
]
