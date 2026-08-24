"""
Copyright (c) Meta Platforms, Inc. and affiliates.
All rights reserved.
This source code is licensed under the license found in the
LICENSE file in the root directory of this source tree.
"""

"""
Tests for the inline SVG icon set.

The icons exist to avoid an icon font or sprite sheet, both of which are
network fetches that fail silently on an air-gapped host and leave an empty
box where an agent expects an affordance. Two properties matter and are easy
to regress:

* they must inherit theme colour (``currentColor``), so a live token swap
  recolours them along with everything else;
* they must be invisible to the accessibility tree (``aria-hidden``), because
  anything visible there becomes an extra node in the agent's observation.
"""

import pytest

from src.open_apps.icons import Icon, icon, icon_markup


#: SVG elements that actually draw. Checked as a set rather than just `<path`
#: because some glyphs are cleaner as primitives -- the launcher grid is four
#: rounded `<rect>`s, and expressing that as path data would be longer and
#: harder to adjust for no benefit. The property being tested is "this member
#: renders something", which the original `<path` check was only a proxy for.
DRAWABLE_ELEMENTS = ("<path", "<rect", "<circle", "<ellipse", "<line", "<polyline", "<polygon")


def test_every_enum_member_has_geometry():
    """A member with no geometry renders an empty svg -- a blank gap in the UI."""
    for member in Icon:
        markup = icon_markup(member)
        assert any(el in markup for el in DRAWABLE_ELEMENTS), (
            f"{member.value} renders no geometry"
        )


def test_icons_inherit_colour_from_css():
    """Hard-coded colours would not follow a theme swap."""
    for member in Icon:
        markup = icon_markup(member)
        assert 'stroke="currentColor"' in markup
        assert "fill=\"none\"" in markup


def test_icons_are_hidden_from_the_accessibility_tree():
    """Decorative glyphs must not add nodes to the agent's observation."""
    for member in Icon:
        markup = icon_markup(member)
        assert 'aria-hidden="true"' in markup
        assert 'focusable="false"' in markup


def test_icons_are_stroked_not_filled():
    """Stroked outlines read slimmer than a solid glyph at sidebar sizes."""
    for member in Icon:
        assert 'stroke-width="1.5"' in icon_markup(member)


def test_size_is_applied_to_both_axes():
    markup = icon_markup(Icon.FILE, size=14)
    assert 'width="14"' in markup
    assert 'height="14"' in markup


def test_viewbox_is_uniform_so_sizes_are_comparable():
    for member in Icon:
        assert 'viewBox="0 0 24 24"' in icon_markup(member)


def test_optional_class_is_emitted_only_when_given():
    assert ' class="' not in icon_markup(Icon.FILE)
    assert 'class="row-icon"' in icon_markup(Icon.FILE, cls="row-icon")


def test_unknown_icon_raises_rather_than_rendering_nothing():
    """A missing glyph should fail in tests, not show up in a screenshot."""
    with pytest.raises(ValueError):
        icon_markup("no-such-icon")


def test_icon_returns_markup_not_escaped_text():
    """A plain str would be HTML-escaped by FastHTML and render as source."""
    from fastcore.basics import NotStr

    assert isinstance(icon(Icon.FILE), NotStr)


def test_string_value_is_accepted_as_well_as_enum_member():
    assert icon_markup("file") == icon_markup(Icon.FILE)


def test_no_icon_references_an_external_resource():
    for member in Icon:
        markup = icon_markup(member)
        assert "http://" not in markup and "https://" not in markup or (
            "www.w3.org/2000/svg" in markup
        )
        # The only permitted URL is the SVG namespace, which is not fetched.
        assert "cdn" not in markup.lower()
