"""The site's theme block: its default values, and how the answers merge over them.

The ``docs.theme`` query's combine function lives here, apart from the
site's verbs, so the declaration can name it and importing it registers
nothing.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from livery.workshop import Query

if TYPE_CHECKING:
    from collections.abc import Callable, Mapping

    from livery.workshop._declaration import Declaration

#: The query deciding the theme block's values. The base's block is
#: the default; a theme extension answers with a table of the keys it
#: changes, and the answers merge key by key in list order, so the
#: last listed extension's keys win.
THEME: Query[dict[str, object]] = Query("docs.theme")
#: The base's theme: the stock fonts, and a palette that follows the
#: OS with a manual light/dark/auto toggle cycle.
THEME_DEFAULT: dict[str, object] = {
    "language": "en",
    "font.text": "Inter",
    "font.code": "Fira Code",
    "features": [
        "announce.dismiss",
        "content.code.annotate",
        "content.code.copy",
        "content.tooltips",
        "navigation.footer",
        "navigation.indexes",
        "navigation.instant",
        "navigation.instant.prefetch",
        "navigation.sections",
        "navigation.tabs",
        "navigation.tabs.sticky",
        "navigation.top",
        "navigation.tracking",
        "search.highlight",
        "toc.follow",
    ],
    "palette": [
        {
            "media": "(prefers-color-scheme)",
            "toggle.icon": "lucide/sun-moon",
            "toggle.name": "Switch to light mode",
        },
        {
            "media": "(prefers-color-scheme: light)",
            "scheme": "default",
            "toggle.icon": "lucide/sun",
            "toggle.name": "Switch to dark mode",
        },
        {
            "media": "(prefers-color-scheme: dark)",
            "scheme": "slate",
            "toggle.icon": "lucide/moon",
            "toggle.name": "Switch to system preference",
        },
    ],
}


def merge_theme(
    answers: Mapping[str, object], lookup: Callable[[str], Declaration | None]
) -> dict[str, object]:
    """The theme's values: the default, each answer's keys over it, by extension.

    The ``docs.theme`` query's combine rule, called with the answers by
    extension in list order.

    Raises:
        ValueError: when an answer is not a table, or names a key
            outside the block's vocabulary; the message names the
            extension, and the query is named by the asker.
    """
    del lookup
    theme: dict[str, object] = dict(THEME_DEFAULT)
    for extension, value in answers.items():
        if not isinstance(value, dict):
            raise ValueError(
                f"{extension} answers {value!r}, and an answer is a table of the"
                " theme's keys"
            )
        table: dict[object, object] = dict(value)
        for key, setting in table.items():
            if not isinstance(key, str) or key not in THEME_DEFAULT:
                raise ValueError(
                    f"{extension} answers the unknown key {key!r}; the keys are"
                    f" {', '.join(THEME_DEFAULT)}"
                )
            theme[key] = setting
    return theme
