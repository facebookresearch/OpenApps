"""
Copyright (c) Meta Platforms, Inc. and affiliates.
All rights reserved.
This source code is licensed under the license found in the
LICENSE file in the root directory of this source tree.

The UI Library -- a Storybook-style surface for the shared design system.

It serves two audiences at once, which is the whole reason it exists:

* **Researchers** get one page where the design tokens are visible as
  swatches and specimens rather than as hex strings in a yaml file, next to
  the components those tokens actually paint. Switching `apps/theme=` and
  reloading is the fastest way to see what a theme really does.
* **Agents** get an app whose state is a small, diffable set of choices --
  which variant each component is showing, and which stories are pinned --
  so "set the button to the danger variant" is a checkable task.

Nothing here defines a component. The atoms, molecules and brand mark all
come from :mod:`open_apps.ui`, and the tokens from
:mod:`open_apps.theme`; this app only *renders* them. That is deliberate --
a storybook that reimplements its own examples stops being evidence about
the real library the moment the two drift.
"""
# No `from __future__ import annotations` here on purpose: fastlite reads the
# dataclass's annotations to build the sqlite schema, and stringified
# annotations make it look up the *string* "bool" in its type map and raise.
import json
from dataclasses import dataclass

from fasthtml.common import (
    H2,
    A,
    Code,
    Div,
    FastHTML,
    Response,
    Style,
    database,
)

from open_apps.apps.start_page.helper import create_logo_header
from open_apps.frontend import local_hdrs
from open_apps.i18n import html_lang, label, language_of, t
from open_apps.icons import Icon
from open_apps.theme import resolve_theme, theme_style
from open_apps.ui import (
    AppTile,
    Badge,
    Clock,
    Divider,
    IconButton,
    LauncherButton,
    Stack,
    Surface,
    Text,
    Toolbar,
    UIButton,
    WeatherChip,
    Wordmark,
    component_styles,
)


@dataclass
class StorySetting:
    """One story's current knob state.

    `component` is the primary key, so the table is a small fixed-size row
    per documented component -- which keeps `/uilibrary_all` cheap to diff
    and makes a task's target state easy to express.
    """

    component: str
    variant: str
    pinned: bool


app = FastHTML(hdrs=local_hdrs(), cls="p-4", default_hdrs=False)
stories = None
logo_title_container = None


# --------------------------------------------------------------------------
# What the library contains
# --------------------------------------------------------------------------
#
# Variant lists are copied from the component signatures in
# `open_apps.ui.atoms`, which validate their own `variant` argument against
# exactly these sets. Keeping them here rather than introspecting keeps the
# page honest about what it claims to document: if an atom gains a variant
# and this list is not updated, the new one is simply undocumented, which is
# a visible gap rather than a silently broken specimen.

ATOM_VARIANTS: dict[str, tuple[str, ...]] = {
    "Surface": ("flat", "elevated"),
    "Stack": ("column", "row"),
    "Text": ("title", "body", "caption"),
    "UIButton": ("primary", "neutral", "danger", "ghost"),
    "IconButton": ("ghost", "primary", "neutral", "danger"),
    "Badge": ("neutral", "success", "warning", "danger"),
    "Divider": ("default",),
}

# Molecules are shown as static specimens. They are the desktop shell's
# parts, and several of them post to `/desktop/*` and swap `#desktop-shell`;
# wiring those up here would either 404 or mutate another app's state, so the
# storybook renders them with inert URLs and documents the interaction in
# prose instead.
MOLECULE_VARIANTS: dict[str, tuple[str, ...]] = {
    "Clock": ("default",),
    "WeatherChip": ("celsius", "fahrenheit"),
    "Toolbar": ("default",),
    "AppTile": ("shortcut", "dock"),
    "LauncherButton": ("closed", "open"),
}

BRAND_VARIANTS: dict[str, tuple[str, ...]] = {
    "Wordmark": ("small", "medium", "large"),
}

ALL_VARIANTS: dict[str, tuple[str, ...]] = {
    **ATOM_VARIANTS,
    **MOLECULE_VARIANTS,
    **BRAND_VARIANTS,
}

SECTION_SLUGS = ("tokens", "atoms", "molecules", "brand")


def _label(key: str, default: str, **fmt) -> str:
    """One of this app's UI strings, from its content pack.

    English lives in ``content/default.yaml`` *and* in ``default`` here, so an
    older or partial pack still renders; ``german`` / ``mandarin`` override the
    keys. Component names, variant names and the ``Code()`` call snippets are
    deliberately not labels: they are identifiers and the state a task scores.
    """
    config = getattr(app, "config", None)
    cfg = getattr(config, "ui_library", None) if config is not None else None
    text = label(cfg, key, default)
    return text.format(**fmt) if fmt else text


def _language() -> str:
    """The pack's language (``content_language``); English when unset."""
    config = getattr(app, "config", None)
    return language_of(getattr(config, "ui_library", None) if config is not None else None)


def sections() -> tuple[tuple[str, str], ...]:
    """`(slug, label)` per section, with labels from the content group.

    Labels come from config rather than being hardcoded so the content axis
    (`apps/ui_library/content=german`) reaches this app's chrome the same way
    it reaches every other app's.
    """
    labels = getattr(app.config.ui_library, "section_labels", None)
    return tuple(
        (slug, (getattr(labels, slug, None) or slug.title()) if labels else slug.title())
        for slug in SECTION_SLUGS
    )

# Interactions posted from a molecule specimen land here and change nothing.
# A specimen that 404s on click reads as a broken app to an agent exploring
# the page, which is noise in an eval that is not about this app.
NOOP_URL = "/uilibrary/noop"


_LAYOUT_STYLES = Style("""
    /* Paint the page from the active theme. Without this the token block is
       declared and then unused, so the one page whose job is to demonstrate
       what a theme does would render on the browser's default white no
       matter which theme is selected -- dark swatches on a light page. */
    /* `html` as well as `body`: Pico paints the root canvas from its own
       palette (and follows the OS dark-mode setting), so without it the area
       below short content keeps Pico's colour under every theme. */
    html, body {
        background-color: var(--color-bg);
        color: var(--color-fg);
        font-family: var(--font-family);
        font-size: var(--font-size-base);
    }

    /* Bridge Pico's variables onto the theme tokens inside the app, so the
       logo header's <h1>, the section <h2>s, links and the `Code()` chips
       stop using Pico's fixed palette -- the page whose job is to show a
       theme must not leak another one. Same set as the todo app's bridge. */
    #uilibrary {
        --pico-background-color: var(--color-bg);
        --pico-color: var(--color-fg);
        --pico-muted-color: var(--color-muted);
        --pico-muted-border-color: var(--color-border);
        --pico-h1-color: var(--color-fg);
        --pico-h2-color: var(--color-fg);
        --pico-h3-color: var(--color-fg);
        --pico-h4-color: var(--color-fg);
        --pico-h5-color: var(--color-fg);
        --pico-h6-color: var(--color-fg);
        --pico-font-family: var(--font-family);
        --pico-border-radius: var(--radius);
        --pico-primary: var(--color-primary);
        --pico-primary-hover: var(--color-primary-hover);
        --pico-primary-underline: color-mix(in srgb, var(--color-primary) 50%, transparent);
        --pico-primary-background: var(--color-primary);
        --pico-primary-border: var(--color-primary);
        --pico-primary-focus: color-mix(in srgb, var(--color-primary) 35%, transparent);
        --pico-code-background-color: var(--color-surface);
        --pico-code-color: var(--color-fg);
        color: var(--color-fg);
    }
    #uilibrary h1, #uilibrary h2 {
        font-family: var(--font-heading);
        color: var(--color-fg);
    }
    #uilibrary code {
        background-color: var(--color-surface);
        color: var(--color-fg);
        font-family: var(--font-mono);
        border: 1px solid var(--color-border);
    }

    .uilib { display: flex; gap: calc(var(--space) * 3); align-items: flex-start; }
    .uilib-nav { flex: 0 0 12rem; position: sticky; top: var(--space); }
    .uilib-nav a {
        display: block; padding: var(--space); text-decoration: none;
        color: var(--color-fg); border-radius: var(--radius);
    }
    .uilib-nav a:hover { background-color: var(--color-surface); }
    .uilib-nav a.is-active { font-weight: 700; color: var(--color-primary); }
    .uilib-canvas { flex: 1; min-width: 0; }

    /* A story: specimen above, knobs below, framed so the boundary between
       two adjacent components is unambiguous. */
    .uilib-story { padding: calc(var(--space) * 2); margin-bottom: calc(var(--space) * 2); }
    .uilib-specimen {
        padding: calc(var(--space) * 2);
        margin: var(--space) 0;
        border: 1px dashed var(--color-border);
        border-radius: var(--radius);
        display: flex; flex-wrap: wrap; gap: var(--space); align-items: center;
    }
    .uilib-knobs { display: flex; flex-wrap: wrap; gap: var(--space); align-items: center; }

    /* Token swatches. The chip carries the colour itself, so this page is the
       one place where a raw token value is legitimately inlined as a style. */
    .uilib-swatches { display: flex; flex-wrap: wrap; gap: var(--space); }
    .uilib-swatch { width: 11rem; }
    .uilib-chip {
        height: 3rem; border: 1px solid var(--color-border); border-radius: var(--radius);
    }

    /* single_column: no nav rail; every section on one page. */
    .uilib.is-single_column { flex-direction: column; }
    .uilib.is-single_column .uilib-nav { display: none; }

    /* grid_gallery: specimens tile instead of stacking, for scanning the
       whole library at once rather than reading one story at a time. */
    .uilib.is-grid_gallery .uilib-canvas .uilib-stories {
        display: grid; grid-template-columns: repeat(auto-fill, minmax(18rem, 1fr));
        gap: calc(var(--space) * 2);
    }
    .uilib.is-grid_gallery .uilib-story { margin-bottom: 0; }
""")


# --------------------------------------------------------------------------
# Config / state
# --------------------------------------------------------------------------


def set_environment(config):
    """Seed one `StorySetting` row per documented component."""
    global app, stories, logo_title_container
    app.config = config
    # <html lang>, so CJK glyph variants and the accessibility tree match the
    # pack's language. FastHTML reads `htmlkw` per request from the app that
    # owns the route, so this holds once the start page mounts the routes.
    app.htmlkw = {"lang": html_lang(language_of(config.ui_library))}
    db = database(config.ui_library.database_path)
    stories = db.create(StorySetting, pk="component")

    for component, variants in ALL_VARIANTS.items():
        stories.insert(
            StorySetting(component=component, variant=variants[0], pinned=False)
        )

    logo_title_container = create_logo_header(
        app_config=config.start_page.apps.uilibrary,
        base_url="/uilibrary",
        current_file_path=__file__,
    )


def current_layout() -> str:
    """The active structure variant from `config/apps/ui_library/layout/`."""
    config = getattr(app, "config", None)
    if config is None:
        return "default"
    return getattr(config.ui_library, "layout", "default")


def _setting(component: str) -> StorySetting:
    """The stored knob state for a component, falling back to its default.

    The fallback matters during a live `reconfigure`: the table is re-seeded
    from `ALL_VARIANTS`, and a request landing between drop and insert should
    render a sane specimen rather than raise.
    """
    try:
        return stories[component]
    except Exception:
        return StorySetting(
            component=component,
            variant=ALL_VARIANTS.get(component, ("default",))[0],
            pinned=False,
        )


# --------------------------------------------------------------------------
# Specimens
# --------------------------------------------------------------------------


def _atom_specimen(component: str, variant: str):
    """Render one atom in the requested variant."""
    if component == "Surface":
        return Surface(
            Text(_label("surface_specimen_label", "A panel with a token background and border."), variant="body"),
            elevated=(variant == "elevated"),
        )
    if component == "Stack":
        return Stack(
            Badge(_label("stack_one_label", "one")),
            Badge(_label("stack_two_label", "two")),
            Badge(_label("stack_three_label", "three")),
            direction=variant,
            gap=1,
        )
    if component == "Text":
        return Text(
            _label("text_specimen_label", "The quick brown fox ({variant})", variant=variant),
            variant=variant,
        )
    if component == "UIButton":
        return UIButton(
            _label("button_specimen_label", "{variant} action", variant=variant.title()),
            variant=variant,
        )
    if component == "IconButton":
        return IconButton(
            Icon.APPS,
            label=_label("icon_button_specimen_label", "{variant} icon button", variant=variant),
            variant=variant,
        )
    if component == "Badge":
        # The badge's text is its tone name -- an identifier, like the knobs.
        return Badge(variant, tone=variant)
    if component == "Divider":
        return Div(
            Text(_label("divider_above_label", "above"), variant="caption"),
            Divider(),
            Text(_label("divider_below_label", "below"), variant="caption"),
        )
    return _no_specimen(component)


def _no_specimen(component: str):
    return Text(
        _label("no_specimen_label", "No specimen for {component}", component=component),
        variant="caption",
    )


def _molecule_specimen(component: str, variant: str):
    """Render one molecule in the requested variant, with inert URLs."""
    if component == "Clock":
        return Clock("9:41")
    if component == "WeatherChip":
        return WeatherChip("cloudy", "18°", units=variant, post_url=NOOP_URL)
    if component == "Toolbar":
        return Toolbar(left=(Clock("9:41"),), right=(Badge("3", tone="danger"),))
    if component == "AppTile":
        return AppTile("OpenTodos", href="/todo", slot=variant)
    if component == "LauncherButton":
        return LauncherButton(open=(variant == "open"), toggle_url=NOOP_URL)
    return _no_specimen(component)


def _brand_specimen(component: str, variant: str):
    heights = {"small": 18, "medium": 24, "large": 40}
    return Wordmark(height=heights.get(variant, 24))


SPECIMEN_RENDERERS = {
    "atoms": _atom_specimen,
    "molecules": _molecule_specimen,
    "brand": _brand_specimen,
}


def _story(section: str, component: str):
    """One story card: specimen, variant knobs, pin toggle and call snippet."""
    setting = _setting(component)
    variants = ALL_VARIANTS.get(component, ("default",))
    render = SPECIMEN_RENDERERS[section]

    knobs = [
        UIButton(
            variant_name,
            variant="primary" if variant_name == setting.variant else "ghost",
            hx_post=f"/uilibrary/{section}/{component}/variant/{variant_name}",
            hx_target=f"#story-{component}",
            hx_swap="outerHTML",
            aria_label=_label(
                "show_variant_aria_label",
                "Show {component} {variant} variant",
                component=component,
                variant=variant_name,
            ),
        )
        for variant_name in variants
    ]
    # Pinning is the one piece of story state that is not a variant, which
    # gives a task something to change that is independent of appearance.
    knobs.append(
        UIButton(
            _label("unpin_label", "Unpin") if setting.pinned else _label("pin_label", "Pin"),
            variant="neutral" if setting.pinned else "ghost",
            hx_post=f"/uilibrary/{section}/{component}/pin",
            hx_target=f"#story-{component}",
            hx_swap="outerHTML",
            aria_label=(
                _label("unpin_aria_label", "Unpin the {component} story", component=component)
                if setting.pinned
                else _label("pin_aria_label", "Pin the {component} story", component=component)
            ),
        )
    )

    return Surface(
        Div(
            Text(component, variant="title"),
            Badge(_label("pinned_label", "pinned"), tone="success") if setting.pinned else "",
            cls="uilib-story-head",
        ),
        Div(render(component, setting.variant), cls="uilib-specimen"),
        Div(*knobs, cls="uilib-knobs"),
        Code(f"{component}(variant={setting.variant!r})"),
        elevated=True,
        cls="uilib-story",
        id=f"story-{component}",
        data_variant=setting.variant,
        data_pinned=str(setting.pinned).lower(),
    )


# --------------------------------------------------------------------------
# Tokens
# --------------------------------------------------------------------------


def _tokens_view():
    """Swatches and specimens for every token in the active theme.

    Read from the resolved theme rather than a hardcoded list, so a theme
    that introduces a token documents itself here without a code change.
    """
    theme = resolve_theme(app.config, "ui_library")
    tokens = {str(k): str(v) for k, v in (theme.get("tokens") or {}).items()}

    colors = [(k, v) for k, v in tokens.items() if k.startswith("color-")]
    fonts = [(k, v) for k, v in tokens.items() if k.startswith("font-")]
    shape = [(k, v) for k, v in tokens.items() if not k.startswith(("color-", "font-"))]

    swatches = [
        Div(
            Div(cls="uilib-chip", style=f"background: var(--{name});"),
            Text(name, variant="caption"),
            Div(Code(value)),
            cls="uilib-swatch",
        )
        for name, value in colors
    ]

    pangram = _label("pangram_label", "The quick brown fox jumps over the lazy dog")
    sample = _label("font_size_sample_label", "The quick brown fox")
    specimens = [
        Div(
            Text(name, variant="caption"),
            Div(pangram, style=f"font-family: var(--{name});")
            if name in ("font-family", "font-heading", "font-mono")
            else Div(sample, style=f"font-size: var(--{name});"),
            Div(Code(value)),
        )
        for name, value in fonts
    ]

    shape_rows = [
        Div(
            Text(name, variant="caption"),
            Div(
                cls="uilib-chip",
                style=f"width: 6rem; border-radius: var(--{name});"
                if name == "radius"
                else f"width: var(--{name}); height: var(--{name});",
            ),
            Div(Code(value)),
        )
        for name, value in shape
    ]

    return Div(
        Text(
            _label("active_theme_label", "Active theme: {name}", name=theme.get("name", "default")),
            variant="title",
        ),
        H2(_label("color_heading_label", "Color")),
        Div(*swatches, cls="uilib-swatches"),
        H2(_label("typography_heading_label", "Typography")),
        Div(*specimens),
        H2(_label("shape_heading_label", "Shape and spacing")),
        Div(*shape_rows),
        id="uilib-tokens",
    )


# --------------------------------------------------------------------------
# Page shell
# --------------------------------------------------------------------------


def _section_view(section: str):
    if section == "tokens":
        return _tokens_view()
    components = {
        "atoms": ATOM_VARIANTS,
        "molecules": MOLECULE_VARIANTS,
        "brand": BRAND_VARIANTS,
    }[section]
    return Div(
        *[_story(section, component) for component in components],
        cls="uilib-stories",
        id=f"uilib-{section}",
    )


def _page(active: str):
    """The shell every section shares.

    `single_column` renders all four sections at once; the other layouts show
    the active one. The nav is always in the DOM so the section links stay
    reachable, and the layout only decides whether it is displayed.
    """
    layout = current_layout()

    section_list = sections()

    nav = Div(
        *[
            A(
                label,
                href=f"/uilibrary/{slug}",
                cls="is-active" if slug == active else "",
            )
            for slug, label in section_list
        ],
        A(t("return_to_apps", _language()), href="/"),
        cls="uilib-nav",
    )

    if layout == "single_column":
        canvas = Div(
            *[Div(H2(label), _section_view(slug)) for slug, label in section_list],
            cls="uilib-canvas",
        )
    else:
        canvas = Div(_section_view(active), cls="uilib-canvas")

    return Div(
        theme_style(app.config, "ui_library"),
        component_styles(),
        _LAYOUT_STYLES,
        # `logo_title_container` already renders the icon and the app title;
        # a second heading here would print the name twice.
        logo_title_container,
        Text(getattr(app.config.ui_library, "intro", ""), variant="body"),
        Div(nav, canvas, cls=f"uilib is-{layout}"),
        id="uilibrary",
        data_layout=layout,
    )


@app.get("/uilibrary")
def index():
    return _page("tokens")


@app.get("/uilibrary/tokens")
def tokens_page():
    return _page("tokens")


@app.get("/uilibrary/atoms")
def atoms_page():
    return _page("atoms")


@app.get("/uilibrary/molecules")
def molecules_page():
    return _page("molecules")


@app.get("/uilibrary/brand")
def brand_page():
    return _page("brand")


@app.post("/uilibrary/{section}/{component}/variant/{variant}")
def set_variant(section: str, component: str, variant: str):
    """Switch a story's variant, then re-render just that story."""
    if component not in ALL_VARIANTS or variant not in ALL_VARIANTS[component]:
        # Unknown knob: re-render unchanged rather than inventing a row, so a
        # mistyped agent action is a no-op instead of corrupting the state
        # that rewards are scored against.
        return _story(section, component) if component in ALL_VARIANTS else ""
    setting = _setting(component)
    setting.variant = variant
    stories.upsert(setting)
    return _story(section, component)


@app.post("/uilibrary/{section}/{component}/pin")
def toggle_pin(section: str, component: str):
    """Pin or unpin a story, then re-render just that story."""
    if component not in ALL_VARIANTS:
        return ""
    setting = _setting(component)
    setting.pinned = not setting.pinned
    stories.upsert(setting)
    return _story(section, component)


@app.post(NOOP_URL)
def noop():
    """Absorb clicks on the molecule specimens. Deliberately changes nothing."""
    return ""


@app.get("/uilibrary_all")
def get_all():
    """Used for rewards"""
    rows = sorted(
        (setting.__dict__ for setting in stories()),
        key=lambda row: row["component"],
    )
    return Response(json.dumps(rows), headers={"Content-Type": "application/json"})


def get_uilibrary_routes():
    return app.routes
