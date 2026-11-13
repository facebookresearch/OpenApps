"""Shared UI strings and the content-language flag (open_apps.i18n)."""
from omegaconf import OmegaConf

from open_apps import i18n


def test_every_shared_string_has_every_language():
    for key, texts in i18n.SHARED.items():
        assert set(texts) == set(i18n.LANGUAGES), key
        assert all(texts.values()), key


def test_language_of_reads_the_pack_flag_and_defaults_to_english():
    assert i18n.language_of({"content_language": "german"}) == "german"
    assert i18n.language_of(OmegaConf.create({"content_language": "mandarin"})) == "mandarin"
    assert i18n.language_of({}) == "english"
    assert i18n.language_of({"content_language": "klingon"}) == "english"
    assert i18n.language_of(None) == "english"


def test_shell_language_follows_the_start_page():
    apps = OmegaConf.create({"start_page": {"content_language": "german"}, "todo": {}})
    assert i18n.shell_language(apps) == "german"
    assert i18n.shell_language({}) == "english"


def test_t_html_lang_and_label():
    assert i18n.t("close", "german") == "Schließen"
    assert i18n.t("dismiss_notification", "mandarin", title="X") == "忽略通知：X"
    assert i18n.html_lang("mandarin") == "zh-Hans"
    assert i18n.html_lang("english") == "en"
    assert i18n.label({"add_label": "Hinzufügen"}, "add_label", "Add") == "Hinzufügen"
    assert i18n.label({}, "add_label", "Add") == "Add"


def test_every_language_uses_the_same_placeholders():
    """A translation that drops or renames ``{title}`` would raise KeyError --
    or silently lose the app name -- only in that language."""
    import string

    for key, texts in i18n.SHARED.items():
        fields = {
            lang: {f for _, f, _, _ in string.Formatter().parse(text) if f}
            for lang, text in texts.items()
        }
        assert len({frozenset(f) for f in fields.values()}) == 1, (key, fields)


def test_english_shell_strings_are_the_historical_ones():
    """English renders exactly what it did before the strings moved here:
    task prompts and tests select on these names."""
    assert i18n.t("close_window", title="OpenTodos") == "Close OpenTodos"
    assert i18n.t("pin_app", title="OpenTodos") == "Pin OpenTodos to desktop"
    assert i18n.t("unpin_app", title="OpenTodos") == "Unpin OpenTodos from desktop"
    assert i18n.t("open_launcher") == "Open app launcher"
    assert i18n.t("new_message", sender="Alice") == "New message · Alice"
    assert i18n.t("main_page") == "main-page"
