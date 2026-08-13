import pytest

from pl2docx.element_config import (
    FillInPreferences,
    InteractivePreferences,
    SelectorPreferences,
    additional_interactive_tags,
    load_element_config,
    resolve_preferences,
)


def _write(tmp_path, text):
    path = tmp_path / "config.yaml"
    path.write_text(text, encoding="utf-8")
    return path


def test_missing_file_gives_empty_config(tmp_path):
    config = load_element_config(tmp_path / "does_not_exist.yaml")
    assert config.preferences == {}
    assert config.behavior_class == {}


def test_absent_section_falls_back_to_builtin_defaults(tmp_path):
    path = _write(tmp_path, "base_url: 'http://localhost:3000'\n")
    config = load_element_config(path)

    mc_prefs = resolve_preferences(config, "multiple_choice")
    assert mc_prefs == SelectorPreferences(list_style="bubble")

    checkbox_prefs = resolve_preferences(config, "checkbox")
    assert checkbox_prefs == SelectorPreferences(list_style="checkbox")

    string_prefs = resolve_preferences(config, "string_input")
    assert string_prefs == FillInPreferences()


def test_explicit_override_applied(tmp_path):
    path = _write(
        tmp_path,
        """
global-element-preferences:
  pl-multiple-choice:
    list-style: letter-labels
    display: inline
    draw-border: true
  pl-string-input:
    display: template
""",
    )
    config = load_element_config(path)

    mc_prefs = resolve_preferences(config, "multiple_choice")
    assert mc_prefs == SelectorPreferences(list_style="letter-labels", display="inline", draw_border=True)

    string_prefs = resolve_preferences(config, "string_input")
    assert string_prefs == FillInPreferences(display="template")


def test_override_without_list_style_still_gets_element_default(tmp_path):
    path = _write(
        tmp_path,
        """
global-element-preferences:
  pl-checkbox:
    bold-correct: false
""",
    )
    config = load_element_config(path)
    prefs = resolve_preferences(config, "checkbox")
    assert prefs.list_style == "checkbox"
    assert prefs.bold_correct is False


def test_additional_elements_extends_behavior_class(tmp_path):
    path = _write(
        tmp_path,
        """
additional-elements:
  pl-scinum-input:
    type: fill-in
    display: none
""",
    )
    config = load_element_config(path)
    assert config.behavior_class["pl-scinum-input"] == "fill-in"

    prefs = resolve_preferences(config, "pl-scinum-input")
    assert prefs == FillInPreferences(display="none")


def test_additional_elements_missing_type_raises(tmp_path):
    path = _write(
        tmp_path,
        """
additional-elements:
  pl-scinum-input:
    display: none
""",
    )
    with pytest.raises(ValueError):
        load_element_config(path)


def test_resolve_preferences_unknown_kind_raises():
    from pl2docx.element_config import ElementConfig

    config = ElementConfig(preferences={}, behavior_class={})
    with pytest.raises(KeyError):
        resolve_preferences(config, "pl-totally-unconfigured-element")


def test_additional_elements_interactive_type_parsed(tmp_path):
    path = _write(
        tmp_path,
        """
additional-elements:
  pl-orbitaldiagram:
    type: interactive
    hide-selectors:
      - ".pl-orbitaldiagram-controls"
""",
    )
    config = load_element_config(path)
    assert config.behavior_class["pl-orbitaldiagram"] == "interactive"
    prefs = config.interactive_preferences["pl-orbitaldiagram"]
    assert prefs == InteractivePreferences(hide_selectors=[".pl-orbitaldiagram-controls"])


def test_interactive_type_never_appears_in_widget_preferences(tmp_path):
    """An interactive-typed tag must never reach `preferences`/`resolve_preferences` -
    it's a fetch-time-only concern (pl2docx.canvas_capture), never a rendering-time
    Widget kind."""
    path = _write(
        tmp_path,
        """
additional-elements:
  pl-orbitaldiagram:
    type: interactive
""",
    )
    config = load_element_config(path)
    assert "pl-orbitaldiagram" not in config.preferences
    with pytest.raises(KeyError):
        resolve_preferences(config, "pl-orbitaldiagram")


def test_additional_elements_interactive_type_accepted_as_valid(tmp_path):
    """type: interactive must not trip the type-validation error that used to only
    accept selector|fill-in."""
    path = _write(
        tmp_path,
        """
additional-elements:
  pl-orbitaldiagram:
    type: interactive
""",
    )
    load_element_config(path)  # must not raise


def test_additional_interactive_tags_leaves_unconfigured_selectors_unresolved(tmp_path):
    """container_selector/hide_selectors stay None when not explicitly configured -
    pl2docx.canvas_capture resolves its own default guesses (trying multiple
    candidate selectors), so there's nothing meaningful to pre-resolve here."""
    path = _write(
        tmp_path,
        """
additional-elements:
  pl-orbitaldiagram:
    type: interactive
""",
    )
    config = load_element_config(path)
    resolved = additional_interactive_tags(config)
    assert resolved["pl-orbitaldiagram"].container_selector is None
    assert resolved["pl-orbitaldiagram"].hide_selectors is None


def test_additional_interactive_tags_respects_explicit_container_selector(tmp_path):
    path = _write(
        tmp_path,
        """
additional-elements:
  pl-drawing:
    type: interactive
    container-selector: ".pl-drawing-container"
    hide-selectors:
      - ".pl-drawing-sidebar"
""",
    )
    config = load_element_config(path)
    resolved = additional_interactive_tags(config)
    assert resolved["pl-drawing"].container_selector == ".pl-drawing-container"
    assert resolved["pl-drawing"].hide_selectors == [".pl-drawing-sidebar"]
