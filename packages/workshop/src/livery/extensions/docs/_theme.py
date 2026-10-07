"""The site's theme block: its default values, and how contributions merge over them.

The ``docs.theme`` slot's compose function lives here, apart from the
site's verbs, so the declaration can name it and importing it registers
nothing.
"""

from __future__ import annotations

from livery.workshop import _slots

#: The slot deciding the theme block's values. The base's block is
#: the default; a theme extension contributes a table of the keys it
#: changes, and contributions merge key by key in contribution order,
#: so the nearest extension's keys win.
THEME_SLOT = "docs.theme"
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


def merge_theme(values: list[object]) -> object:
    """The theme's values: the default, each contribution's keys over it.

    Raises:
        SlotError: when a contribution is not a table, or names a key
            outside the block's vocabulary.
    """
    theme: dict[str, object] = dict(THEME_DEFAULT)
    for value in values:
        if not isinstance(value, dict):
            raise _slots.SlotError(
                f"slot {THEME_SLOT!r}: a contribution is a table of the theme's"
                f" keys, not {value!r}"
            )
        table: dict[object, object] = dict(value)
        for key, setting in table.items():
            if not isinstance(key, str) or key not in THEME_DEFAULT:
                raise _slots.SlotError(
                    f"slot {THEME_SLOT!r}: unknown key {key!r}; the keys are"
                    f" {', '.join(THEME_DEFAULT)}"
                )
            theme[key] = setting
    return theme
