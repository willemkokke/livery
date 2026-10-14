"""What an extension's steps and categories are judged against: the conformance kit.

An extension declares steps and category tables that the gate then
relies on. The kit states what the gate relies on, one clause at a
time, and judges an extension against it: a
[livery.workshop.testing.Subject][] names the extension, the sets of
package-level extensions to probe it with and the steps it registers,
each [livery.workshop.testing.Clause][] returns the
[livery.workshop.testing.Violation][]s the subject commits, and
[livery.workshop.testing.CLAUSES][] lists them in order. An extension's own
suite runs every clause on its subject after the extension's
registrations ran:

```python
import pytest

from livery.workshop.testing import CLAUSES, Clause, Subject

SUBJECT = Subject("acme.extension", sets=(("acme",),), steps=(...))


@pytest.mark.parametrize("clause", CLAUSES, ids=lambda clause: clause.name)
def test_the_extension_conforms(clause: Clause) -> None:
    assert clause.judge(SUBJECT) == []
```

The workshop's own steps run the same clauses through
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
