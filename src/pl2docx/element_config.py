"""Load and resolve Phase 3B's element/question-level formatting preferences.

`config.yaml` may declare a `global-element-preferences` section keyed by PL element
tag name (`pl-multiple-choice`, `pl-checkbox`, and the built-in fill-in-type elements
`pl-string-input`/`pl-integer-input`/`pl-number-input`/`pl-symbolic-input`/`pl-units-input`)
plus an `additional-elements` section for element kinds not natively/built-in supported
by `pl2docx.html_parser` (typically course-specific elements following the same
fill-in markup convention, e.g. `pl-scinum-input`), each declaring which built-in
behavior class (`selector` or `fill-in`) it extends. All keys are optional; an absent
or partial section reproduces Phase 3A's fixed formatting as closely as the unified
`display` vocabulary below allows.

Kept intentionally simple (a single global config, resolved by widget `kind` only) per
`planning_notes/2026-08-12 phase 3a implementation and phase 3b spec.md`'s explicit
scope note: this leaves room for a future per-question/per-zone override (e.g.
`resolve_preferences` gaining an optional question/zone argument) without requiring a
rewrite, but that override capability isn't built now.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal

import yaml

Display = Literal["inline", "block", "template", "none"]
ListStyle = Literal["letter-labels", "bubble", "checkbox"]
BehaviorClass = Literal["selector", "fill-in", "interactive"]

# Internal `pl2docx.html_parser.QuestionKind` values, keyed by the PL element tag name
# used in config.yaml.
_BUILTIN_KIND_BY_TAG: dict[str, str] = {
    "pl-multiple-choice": "multiple_choice",
    "pl-checkbox": "checkbox",
    "pl-string-input": "string_input",
    "pl-integer-input": "integer_input",
    "pl-number-input": "number_input",
    "pl-symbolic-input": "symbolic_input",
    "pl-units-input": "units_input",
}
_BUILTIN_BEHAVIOR_CLASS: dict[str, BehaviorClass] = {
    "multiple_choice": "selector",
    "checkbox": "selector",
    "string_input": "fill-in",
    "integer_input": "fill-in",
    "number_input": "fill-in",
    "symbolic_input": "fill-in",
    "units_input": "fill-in",
}
_DEFAULT_LIST_STYLE_BY_KIND: dict[str, ListStyle] = {
    "multiple_choice": "bubble",
    "checkbox": "checkbox",
}


@dataclass(frozen=True)
class SelectorPreferences:
    """Formatting preferences for a selector-type widget (`pl-multiple-choice`/`pl-checkbox`).

    Parameters
    ----------
    list_style : {"letter-labels", "bubble", "checkbox"}
        Marker style shown before each option. Element-specific default when the
        instructor doesn't set this: `"bubble"` for `pl-multiple-choice`, `"checkbox"`
        for `pl-checkbox` (or any `additional-elements` entry extending `selector`,
        which falls back to `"bubble"`).
    bold_correct : bool
        Whether to bold the correct option's run in the answer-key render, when it
        could be matched back to `ParsedQuestion.Widget.correct_option_indices`.
    display : {"inline", "block", "template", "none"} or None
        Where/how this widget's rendered content is placed. `None` means "try to
        auto-detect from the source HTML" (the `form-check-inline` signal); if no
        signal is available (e.g. a dropdown-rendered `pl-multiple-choice`), falls
        back to `"block"`. See `pl2docx.element_renderer` for how each value is
        interpreted.
    draw_border : bool
        Whether to draw a single box around this widget's entire rendered content
        (all options together).
    """

    list_style: ListStyle | None = None
    bold_correct: bool = True
    display: Display | None = None
    draw_border: bool = False


@dataclass(frozen=True)
class FillInPreferences:
    """Formatting preferences for a fill-in-type widget (`pl-string-input`/`pl-integer-input`).

    Parameters
    ----------
    display : {"inline", "block", "template", "none"} or None
        Where/how this widget's generated fill-in-the-blank content is placed. Content
        is always generated regardless of `display` — see
        `pl2docx.element_renderer`. `None` means "auto-detect"; no source-HTML signal
        exists for fill-in-type widgets, so this always falls back to `"block"`.
    draw_border : bool
        Whether to draw a single box around this widget's label/blank/suffix content.
    default_label : str or None
        Fallback label text (e.g. `"Answer:"`) to prepend before the blank when this
        widget's own source HTML supplies no `label` (`Widget.label is None`) — real
        content: most fill-in elements are used bare, with no instructor-authored
        label of their own. `None` (the default) means no fallback prefix at all, not
        the old hardcoded `"Answer:"` — that behavior must now be opted into
        explicitly per element kind via `config.yaml`, since a default the instructor
        can't turn off isn't a real default. Never applied when the widget already has
        its own `label`, regardless of this setting.
    """

    display: Display | None = None
    draw_border: bool = False
    default_label: str | None = None


ElementPreferences = SelectorPreferences | FillInPreferences


@dataclass(frozen=True)
class InteractivePreferences:
    """Fetch-time capture settings for a canvas-based interactive widget (Phase 5 subphase 2).

    Unlike `SelectorPreferences`/`FillInPreferences`, this never reaches
    `resolve_preferences()` or `pl2docx.element_renderer` — an `additional-elements`
    tag declaring `type: interactive` is captured (screenshotted) and flattened to a
    plain `<img>` entirely at fetch time (`pl2docx.canvas_capture`), before
    `pl2docx.html_parser` ever runs, so it never becomes a `Widget` needing a
    rendering-time preference at all. See `pl2docx.canvas_capture`'s module docstring.

    Parameters
    ----------
    container_selector : str or None
        CSS selector (relative to the fetched page) identifying the DOM subtree to
        screenshot and replace with the captured image. `None` means "no explicit
        config" — `pl2docx.canvas_capture._resolve_container_matches` tries a
        `f".{tag}-canvas-wrap"` selector first (a narrower wrapper around just the
        `<canvas>`, excluding toolbar-reserved layout space — confirmed real
        convention for `pl-lewisstructure`/`pl-orbitaldiagram`), falling back to
        `f".{tag}"` (the whole element) only if that narrower one matches nothing.
        Neither is universal — core PL's `pl-drawing` uses `.pl-drawing-container`
        with no `-canvas-wrap` at all — so an element that doesn't follow either
        convention needs an explicit override here.
    hide_selectors : list[str] or None
        CSS selectors (relative to the page) to hide before screenshotting (e.g. a
        toolbar/controls div) — an explicit, possibly empty, list here fully replaces
        `pl2docx.canvas_capture`'s own default candidate-selector guesses; `None`
        means "no explicit config, fall back to those guesses". See
        `pl2docx.canvas_capture._DEFAULT_HIDE_SUFFIXES`'s docstring for why a single
        derived default string doesn't reliably cover every real element.
    """

    container_selector: str | None = None
    hide_selectors: list[str] | None = None


@dataclass(frozen=True)
class ElementConfig:
    """Resolved element/question-level formatting preferences for one pl2docx run.

    Parameters
    ----------
    preferences : dict[str, ElementPreferences]
        Explicit instructor overrides, keyed by internal `QuestionKind` string (e.g.
        `"multiple_choice"`), already merged from `global-element-preferences` and
        `additional-elements`. Not necessarily covering every kind
        `pl2docx.html_parser` can produce — `resolve_preferences` falls back to
        built-in defaults for any kind missing here. Never includes `interactive`-typed
        tags (see `interactive_preferences`).
    behavior_class : dict[str, BehaviorClass]
        Which behavior class applies to each non-built-in tag declared via
        `additional-elements` (`"selector"`, `"fill-in"`, or `"interactive"`). Built-in
        kinds don't need an entry (see `_BUILTIN_BEHAVIOR_CLASS`).
    interactive_preferences : dict[str, InteractivePreferences]
        `additional-elements` entries declaring `type: interactive`, keyed by PL tag
        name — consumed by `pl2docx.fetch`/`pl2docx.canvas_capture` at fetch time, not
        by `resolve_preferences`/`pl2docx.element_renderer` (see
        `InteractivePreferences`'s docstring for why this is a separate dict, not
        merged into `preferences`).
    """

    preferences: dict[str, ElementPreferences]
    behavior_class: dict[str, BehaviorClass]
    interactive_preferences: dict[str, InteractivePreferences] = field(default_factory=dict)


def load_element_config(path: str | Path) -> ElementConfig:
    """Load Phase 3B element preferences from a `config.yaml` file.

    Parameters
    ----------
    path : str or pathlib.Path
        Path to a YAML config file. Only the `global-element-preferences` and
        `additional-elements` top-level keys are read here; other keys (base URL,
        course/assessment ids, etc.) are `pl2docx.config.load_config`'s concern.

    Returns
    -------
    ElementConfig
        Resolved instructor overrides. Empty (all built-in defaults apply) if
        `path` has neither section, or doesn't exist.

    Raises
    ------
    ValueError
        If an `additional-elements` entry is missing its required `type` key, or
        declares a `type` other than `"selector"`/`"fill-in"`/`"interactive"`.
    """
    path = Path(path)
    if not path.exists():
        return ElementConfig(preferences={}, behavior_class={}, interactive_preferences={})

    with path.open("r", encoding="utf-8") as f:
        raw = yaml.safe_load(f) or {}

    behavior_class: dict[str, BehaviorClass] = {}
    interactive_preferences: dict[str, InteractivePreferences] = {}
    additional = raw.get("additional-elements") or {}
    for tag, entry in additional.items():
        entry = entry or {}
        kind_type = entry.get("type")
        if kind_type not in ("selector", "fill-in", "interactive"):
            raise ValueError(
                f"additional-elements entry '{tag}' must declare type: "
                f"selector|fill-in|interactive (got {kind_type!r})."
            )
        behavior_class[tag] = kind_type
        if kind_type == "interactive":
            interactive_preferences[tag] = _build_interactive_preferences(entry)

    preferences: dict[str, ElementPreferences] = {}
    global_prefs = raw.get("global-element-preferences") or {}
    for tag, entry in {**global_prefs, **additional}.items():
        if behavior_class.get(tag) == "interactive":
            continue  # handled above - never becomes a rendering-time Widget preference
        entry = {k: v for k, v in (entry or {}).items() if k != "type"}
        kind = _BUILTIN_KIND_BY_TAG.get(tag, tag)
        cls = _BUILTIN_BEHAVIOR_CLASS.get(kind) or behavior_class.get(tag)
        if cls is None:
            raise ValueError(
                f"'{tag}' in global-element-preferences is not a built-in element and "
                "has no matching additional-elements 'type' declaration."
            )
        preferences[kind] = _build_preferences(cls, entry)

    return ElementConfig(
        preferences=preferences,
        behavior_class=behavior_class,
        interactive_preferences=interactive_preferences,
    )


def _build_preferences(behavior_class: BehaviorClass, entry: dict) -> ElementPreferences:
    entry = {k.replace("-", "_"): v for k, v in entry.items()}
    if behavior_class == "selector":
        return SelectorPreferences(**entry)
    return FillInPreferences(**entry)


def _build_interactive_preferences(entry: dict) -> InteractivePreferences:
    entry = {k.replace("-", "_"): v for k, v in entry.items() if k != "type"}
    return InteractivePreferences(**entry)


def resolve_preferences(element_config: ElementConfig, kind: str) -> ElementPreferences:
    """Resolve the effective formatting preferences for one widget kind.

    Parameters
    ----------
    element_config : ElementConfig
        The run's loaded element configuration.
    kind : str
        A `pl2docx.html_parser.QuestionKind` value (or an `additional-elements` kind
        string) identifying the widget type to resolve preferences for.

    Returns
    -------
    SelectorPreferences or FillInPreferences
        The instructor's explicit override for `kind` if one was configured,
        otherwise a preferences instance built from built-in defaults (element-
        specific `list_style` default applied for selector-type kinds).

    Raises
    ------
    KeyError
        If `kind` isn't a built-in kind and has no matching preferences/behavior-class
        entry in `element_config` (an unconfigured, non-built-in element type), or if
        `kind` is configured as `type: interactive` — an interactive-typed tag is a
        fetch-time-only concern (see `InteractivePreferences`'s docstring) and never
        becomes a `Widget` needing a rendering-time preference, so calling this for one
        is always a caller bug, not a normal "use defaults" case.
    """
    if kind in element_config.preferences:
        prefs = element_config.preferences[kind]
        if isinstance(prefs, SelectorPreferences) and prefs.list_style is None:
            default_style = _DEFAULT_LIST_STYLE_BY_KIND.get(kind, "bubble")
            return SelectorPreferences(
                list_style=default_style,
                bold_correct=prefs.bold_correct,
                display=prefs.display,
                draw_border=prefs.draw_border,
            )
        return prefs

    behavior_class = _BUILTIN_BEHAVIOR_CLASS.get(kind) or element_config.behavior_class.get(kind)
    if behavior_class is None:
        raise KeyError(
            f"No built-in or configured behavior class for widget kind '{kind}'."
        )
    if behavior_class == "interactive":
        raise KeyError(
            f"'{kind}' is configured as type: interactive - it's captured and flattened "
            "to a plain image at fetch time and never becomes a Widget needing a "
            "rendering-time preference."
        )
    if behavior_class == "selector":
        return SelectorPreferences(list_style=_DEFAULT_LIST_STYLE_BY_KIND.get(kind, "bubble"))
    return FillInPreferences()


def additional_fill_in_tags(element_config: ElementConfig) -> list[str]:
    """List the `additional-elements` tags declared as extending fill-in behavior.

    Parameters
    ----------
    element_config : ElementConfig
        The run's loaded element configuration.

    Returns
    -------
    list[str]
        PL element tag names (e.g. `["pl-scinum-input"]`) whose `additional-elements`
        entry declared `type: fill-in`. Meant to be passed straight through to
        `pl2docx.html_parser.parse_instance_question_html`'s
        `additional_fill_in_tags` parameter, so the parser also detects these
        tags' widgets using the same tag-name-derived pattern as every built-in
        fill-in element — see that function's docstring for why this is safe to
        do generically, without any course-specific knowledge in `pl2docx` itself.
    """
    return [tag for tag, cls in element_config.behavior_class.items() if cls == "fill-in"]


def additional_interactive_tags(element_config: ElementConfig) -> dict[str, InteractivePreferences]:
    """Resolve `additional-elements` tags declared as `type: interactive`.

    Parameters
    ----------
    element_config : ElementConfig
        The run's loaded element configuration.

    Returns
    -------
    dict[str, InteractivePreferences]
        Tag name -> preferences, unchanged from what `config.yaml` declared -
        `container_selector`/`hide_selectors` are passed through as-is, `None` left
        for the caller (`pl2docx.canvas_capture`) to interpret as "fall back to my
        own default candidate guesses" (it tries several selectors per tag, not a
        single fixed default, so resolving one here wouldn't be meaningful - see
        that module's `_resolve_container_matches`/`_resolve_hide_selectors`). Meant
        to be passed straight through to
        `pl2docx.canvas_capture.capture_interactive_elements`.
    """
    return dict(element_config.interactive_preferences)
