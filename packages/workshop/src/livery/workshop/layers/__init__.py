"""The layers inside the workshop wheel, one package each under this namespace.

A layer shipped as a distribution of its own contributes a directory
here from another tree; the path extension below joins it, and this
file carries nothing else, so the two trees never disagree on it.
"""

from pkgutil import extend_path

__path__ = extend_path(__path__, __name__)
