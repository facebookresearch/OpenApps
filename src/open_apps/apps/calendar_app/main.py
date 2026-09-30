"""
Copyright (c) Meta Platforms, Inc. and affiliates.
All rights reserved.
This source code is licensed under the license found in the
LICENSE file in the root directory of this source tree.
"""
from fasthtml.common import *
from fasthtml.common import fast_app, serve
from fasthtml.common import (
    Title, Container, Titled, Div, H1, H2, H3, H4, P, A, Img, Button, Form, Input
    , Textarea, Select, Option, Label, Script, Link, Style, Table, Thead, Tbody, Tr, Th, Td,
    Ul, Li, Hr, Article, Button, RedirectResponse, Container, MarkdownJS, Span, Time,
    HighlightJS, database, dataclass)
from datetime import datetime, timedelta
from itertools import groupby
from src.open_apps.frontend import local_hdrs
from src.open_apps.theme import render_theme_css, resolve_theme
import calendar
import os
import logging
import yaml, json
from feedgen.feed import FeedGenerator
from starlette.responses import Response
from typing import Optional, List
from src.open_apps.apps.start_page.helper import create_logo_header

# Set up logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# fix relative path issue
current_dir = os.path.dirname(os.path.abspath(__file__))

# Static, theme-agnostic component styles. Every color, font and radius is a
# design token from the shared theme (`config/apps/theme/`), resolved
# per-request by `calendar_theme()`; the three structural values the calendar
# owns come from its `layout` group as `--layout-*`. Nothing here depends on
# the config, so this block is built once at import instead of rebuilt in
# `set_environment`.
_COMPONENT_CSS = """
    /* Base styles */
    body {
        font-family: var(--font-family);
        font-size: var(--font-size-base);
        color: var(--color-fg);
        background-color: var(--color-bg);
    }

    h1, h2, h3, h4, h5, h6 {
        font-family: var(--font-heading);
    }

    h1 {
        font-size: var(--font-size-heading);
    }

    /* Button styles */
    [role="button"], button {
        border-radius: var(--radius);
        padding: var(--layout-button-padding);
    }

    /* Apply primary color to buttons */
    [role="button"]:not(.outline):not(.secondary),
    button:not(.outline):not(.secondary) {
        background-color: var(--color-primary);
        border-color: var(--color-primary);
        color: var(--color-on-primary);
    }

    /* Apply hover state for primary buttons */
    [role="button"]:not(.outline):not(.secondary):hover,
    button:not(.outline):not(.secondary):hover {
        background-color: var(--color-primary-hover);
        border-color: var(--color-primary-hover);
    }

    /* Secondary buttons */
    [role="button"].secondary,
    button.secondary {
        background-color: var(--color-neutral);
        border-color: var(--color-neutral);
        color: var(--color-btn-fg);
    }

    /* Apply border color to form elements */
    input, select, textarea {
        border: 1px solid var(--color-border);
        border-radius: var(--radius);
        background-color: var(--color-bg);
        color: var(--color-fg);
    }

    [role="button"].outline,
    button.outline {
        background-color: var(--color-bg);
        border-color: var(--color-border);
        color: var(--color-primary);
    }

    /* Apply container width */
    #calendar-container {
        width: var(--layout-container-width);
        margin: 0 auto;
    }

    /* Apply border color to tables */
    table, th, td {
        border-color: var(--color-border);
    }

    /* Links */
    a:not([role="button"]) {
        color: var(--color-primary);
    }

    a:not([role="button"]):hover {
        color: var(--color-primary-hover);
    }

    /* Calendar app specific styles */
    .logo-title-container {
        display: flex;
        align-items: center;
        text-decoration: none;
    }

    .custom-logo {
        max-height: 50px;
        margin-right: 10px;
    }
    .calendar-title {
        margin: 0;
        color: var(--color-primary);
    }

    .logo-title-container a {
        text-decoration: none;
    }

    .button-container {
        display: flex;
        justify-content: space-between;
        margin-top: var(--layout-spacing);
    }

    .error-message {
        background-color: color-mix(in srgb, var(--color-danger) 10%, var(--color-bg));
        color: var(--color-danger);
        padding: var(--layout-spacing);
        margin-bottom: var(--layout-spacing);
        border-radius: var(--radius);
        text-align: center;
    }
"""

# Month grid, agenda, toolbar and layout-variant styles. Same rules as above:
# tokens only, so every theme (including `mono`, where muted == fg and
# surface == bg) reads correctly -- tints are `color-mix` of fg/primary into
# bg rather than fixed greys. Selectors that restyle a `[role="button"]` are
# written to out-rank the generic button rules in `_COMPONENT_CSS` (up to
# (0,4,0) for the primary :hover) instead of relying on source order.
_VIEW_CSS = """
    /* ---- Toolbar ---------------------------------------------------- */
    .calendar-header {
        display: flex; align-items: center; justify-content: space-between;
        flex-wrap: wrap; gap: var(--layout-spacing);
        margin-bottom: var(--layout-spacing);
    }
    .calendar-nav { display: flex; align-items: center; flex-wrap: wrap; gap: var(--space); }
    /* Pico colours headings itself; take the theme's foreground instead. */
    #current-month-year { color: var(--color-fg); }
    .calendar-nav h2 {
        margin: 0 calc(var(--space) * 0.5);
        font-size: var(--font-size-heading); font-weight: 600;
        line-height: 1.2; white-space: nowrap;
    }
    .calendar-header a.calendar-step[role="button"],
    .calendar-header a.calendar-today[role="button"] {
        padding: calc(var(--space) * 0.5) calc(var(--space) * 1.25);
        font-size: var(--font-size-sm); line-height: 1.4; white-space: nowrap;
        background-color: transparent; color: var(--color-fg);
        border: 1px solid var(--color-border);
    }
    .calendar-header a.calendar-step[role="button"]:hover,
    .calendar-header a.calendar-today[role="button"]:hover {
        background-color: color-mix(in srgb, var(--color-fg) 6%, var(--color-bg));
    }

    /* Segmented view switcher: one bordered control, selected segment tinted. */
    .calendar-header .view-toggle {
        display: inline-flex; width: auto; margin: 0; gap: 0;
        border: 1px solid var(--color-border); border-radius: var(--radius);
        overflow: hidden; box-shadow: none;
    }
    .calendar-header .view-toggle > a[role="button"] {
        flex: 1 1 auto; margin: 0; border: 0; border-radius: 0; box-shadow: none;
        padding: calc(var(--space) * 0.5) calc(var(--space) * 1.75);
        font-size: var(--font-size-sm); line-height: 1.4; text-align: center;
        background-color: transparent; color: var(--color-fg);
    }
    .calendar-header .view-toggle > a[role="button"] + a[role="button"] {
        border-left: 1px solid var(--color-border);
    }
    .calendar-header .view-toggle > a[role="button"]:hover {
        background-color: color-mix(in srgb, var(--color-fg) 6%, var(--color-bg));
    }
    .calendar-header .view-toggle > a[role="button"][aria-current="page"] {
        background-color: color-mix(in srgb, var(--color-primary) 18%, var(--color-bg));
        font-weight: 600;
    }

    /* ---- Footer: one primary action, the way out demoted to chrome ---- */
    a.calendar-create[role="button"] { font-weight: 600; }
    .button-container { align-items: center; }
    /* Still a bordered button in the bottom-right corner (UI questions ask
       for "the button at the bottom-right"), just smaller and unfilled. */
    .footer-container a.calendar-return[role="button"] {
        margin-left: auto;
        padding: calc(var(--space) * 0.5) calc(var(--space) * 1.5);
        background-color: transparent; border-color: var(--color-border);
        color: var(--color-fg); font-size: var(--font-size-sm);
    }
    .footer-container a.calendar-return[role="button"]:hover {
        color: var(--color-fg);
        background-color: color-mix(in srgb, var(--color-fg) 6%, var(--color-bg));
    }
    /* default layout: the original outlined Pico button's size, padding,
       weight and colours, so cross-app navigation stays easy to spot. */
    .footer-container a.calendar-return.calendar-return-prominent[role="button"] {
        padding: var(--layout-button-padding);
        font-size: var(--font-size-base); font-weight: 400;
        background-color: var(--color-bg); border: 1px solid var(--color-border);
        color: var(--color-primary);
    }
    .footer-container a.calendar-return.calendar-return-prominent[role="button"]:hover {
        color: var(--color-primary-hover);
        background-color: color-mix(in srgb, var(--color-primary) 8%, var(--color-bg));
    }

    /* ---- Month grid ------------------------------------------------- */
    .calendar-table { width: 100%; table-layout: fixed; border-collapse: collapse; margin: 0; }
    .calendar-table th {
        text-align: center; font-size: var(--font-size-sm); font-weight: 600;
        color: var(--color-fg); background-color: var(--color-bg);
        padding: calc(var(--space) * 0.75) 0;
        border: 0; border-bottom: 1px solid var(--color-border);
    }
    .calendar-table td {
        height: 6.5rem; vertical-align: top; padding: calc(var(--space) * 0.5);
        background-color: var(--color-bg); color: var(--color-fg);
        border: 1px solid var(--color-border);
    }
    .calendar-table td.weekend {
        background-color: color-mix(in srgb, var(--color-fg) 3%, var(--color-bg));
    }
    .calendar-table td.calendar-cell-empty {
        background-color: color-mix(in srgb, var(--color-fg) 7%, var(--color-bg));
    }
    /* `radius * 99` is a circle under any rounded theme and stays square
       under `mono`, whose zero radius deliberately removes the cue. */
    .day-number {
        display: inline-flex; align-items: center; justify-content: center;
        min-width: 1.75rem; height: 1.75rem; padding: 0 0.3rem;
        margin-bottom: 0.25rem; border-radius: calc(var(--radius) * 99);
        font-size: var(--font-size-sm); font-weight: 600; line-height: 1;
    }
    .day-number.today { background-color: var(--color-primary); color: var(--color-on-primary); }
    .calendar-table a.event-chip {
        display: block; margin: 0 0 2px;
        padding: 0.125rem calc(var(--space) * 0.75);
        font-size: calc(var(--font-size-sm) * 0.9); line-height: 1.35;
        white-space: nowrap; overflow: hidden; text-overflow: ellipsis;
        text-decoration: none; color: var(--color-fg);
        background-color: color-mix(in srgb, var(--color-primary) 14%, var(--color-bg));
        border-left: 3px solid var(--color-primary);
        border-radius: calc(var(--radius) * 0.5);
    }
    .calendar-table a.event-chip:hover {
        color: var(--color-fg);
        background-color: color-mix(in srgb, var(--color-primary) 26%, var(--color-bg));
    }
    .calendar-table a.event-chip:focus-visible {
        outline: 2px solid var(--color-primary); outline-offset: 1px;
    }

    /* ---- Agenda ------------------------------------------------------ */
    .agenda-list { list-style: none; padding: 0; margin: 0; border-top: 1px solid var(--color-border); }
    .agenda-list > li, .agenda-events > li { list-style: none; margin: 0; }
    .agenda-day {
        display: grid; grid-template-columns: 8rem 1fr; gap: var(--layout-spacing);
        align-items: start; padding: calc(var(--space) * 1.25) 0;
        border-bottom: 1px solid var(--color-border);
    }
    .agenda-date { margin: 0; font-size: var(--font-size-base); font-weight: 400; color: var(--color-fg); }
    .agenda-date time { display: flex; align-items: center; gap: var(--space); }
    .agenda-day-number {
        display: inline-flex; align-items: center; justify-content: center;
        min-width: 2.25rem; height: 2.25rem; padding: 0 0.25rem;
        border-radius: calc(var(--radius) * 99);
        font-size: 1.375rem; font-weight: 600; line-height: 1;
    }
    .agenda-date.today .agenda-day-number { background-color: var(--color-primary); color: var(--color-on-primary); }
    .agenda-date-label { font-size: var(--font-size-sm); color: var(--color-muted); }
    .agenda-events { list-style: none; margin: 0; padding: 0; }
    .agenda-events > .agenda-event {
        display: grid; grid-template-columns: 6rem 1fr; gap: var(--space);
        align-items: baseline; padding: calc(var(--space) * 0.5) 0;
    }
    .agenda-time { font-size: var(--font-size-sm); color: var(--color-muted); }
    .agenda-event-body { display: flex; flex-direction: column; min-width: 0; }
    .agenda-list a.agenda-event-link { font-weight: 600; text-decoration: none; color: var(--color-fg); }
    .agenda-list a.agenda-event-link:hover { color: var(--color-fg); text-decoration: underline; }
    .agenda-list a.agenda-event-link::before {
        content: ""; display: inline-block; width: 0.625rem; height: 0.625rem;
        margin-right: var(--space); border-radius: calc(var(--radius) * 99);
        background-color: var(--color-primary);
    }
    .agenda-location { font-size: var(--font-size-sm); color: var(--color-muted); padding-left: calc(0.625rem + var(--space)); }
    .agenda-list > li.agenda-empty {
        padding: calc(var(--space) * 5) var(--space); text-align: center; color: var(--color-muted);
    }
    .agenda-empty p { margin: 0 0 var(--space); }
    .agenda-empty .agenda-empty-title { color: var(--color-fg); font-weight: 600; }

    .footer-container { margin-top: var(--layout-spacing); }
    #about-dialog-content { padding: var(--layout-spacing); }

    /* ---- Layout variants (`config/apps/calendar/layout/`) ------------ */

    /* agenda_first: a centred masthead over a readable-width agenda. */
    .calendar-header.calendar-header-centered {
        flex-direction: column; align-items: center; gap: calc(var(--space) * 1.5);
    }
    .calendar-header-centered .calendar-nav { gap: var(--layout-spacing); }
    .calendar-header-centered #current-month-year {
        min-width: 12ch; text-align: center; font-size: calc(var(--font-size-heading) * 1.15);
    }
    .calendar-subbar { display: flex; align-items: center; gap: var(--space); }
    .calendar-header-centered + .calendar-content > .agenda-list { max-width: 48rem; margin-inline: auto; }

    /* sidebar_nav: "Add Event", the month with its nav and a mini-month,
       and the view toggle leave the bar above the grid and become a left
       rail that stays in view while the grid scrolls. */
    .calendar-body-sidebar { display: flex; gap: calc(var(--layout-spacing) * 1.5); align-items: flex-start; }
    .calendar-body-sidebar > .calendar-content { flex: 1; min-width: 0; }
    .calendar-header.calendar-rail {
        flex: 0 0 14rem; flex-direction: column; flex-wrap: nowrap;
        align-items: stretch; justify-content: flex-start;
        position: sticky; top: 1rem; margin: 0;
    }
    .calendar-rail > * { margin: 0; }
    .calendar-rail a.calendar-create[role="button"] {
        align-self: flex-start;
        padding: calc(var(--space) * 1.25) calc(var(--space) * 3);
        box-shadow: 0 1px 3px color-mix(in srgb, var(--color-fg) 30%, transparent);
    }
    .calendar-rail-month { display: flex; flex-direction: column; gap: var(--space); }
    .calendar-rail #current-month-year {
        margin: 0; font-size: var(--font-size-base); font-weight: 600; white-space: nowrap;
    }
    .calendar-rail .calendar-nav { gap: calc(var(--space) * 0.5); flex-wrap: wrap; }
    .calendar-header.calendar-rail .calendar-nav > a[role="button"] {
        padding: calc(var(--space) * 0.375) var(--space);
    }
    .calendar-header.calendar-rail .view-toggle { display: flex; width: 100%; }
    .mini-month { width: 100%; table-layout: fixed; border-collapse: collapse; margin: 0; }
    .mini-month th, .mini-month td {
        padding: 1px 0; text-align: center; border: 0;
        background-color: transparent; color: var(--color-fg);
        font-size: calc(var(--font-size-sm) * 0.85);
    }
    .mini-month th { color: var(--color-muted); font-weight: 600; }
    .mini-month span {
        display: inline-flex; align-items: center; justify-content: center;
        width: 1.6rem; height: 1.6rem; border-radius: calc(var(--radius) * 99);
    }
    .mini-month td.busy span { font-weight: 700; text-decoration: underline; text-underline-offset: 3px; }
    .mini-month td.today span {
        background-color: var(--color-primary); color: var(--color-on-primary); font-weight: 700;
    }
    /* Too narrow for a rail beside a seven-column grid: stack it on top. */
    @media (max-width: 48rem) {
        .calendar-body-sidebar { flex-direction: column; align-items: stretch; }
        .calendar-header.calendar-rail { position: static; flex-basis: auto; }
    }
"""

styles = Style(_COMPONENT_CSS)


app, rt = fast_app(
    default_hdrs=False,
    hdrs=(
        *local_hdrs(),
        MarkdownJS(),
        HighlightJS(langs=["python", "javascript", "html", "css"]),
        Script(src="https://unpkg.com/@phosphor-icons/web"),
        styles,
        Link(
            rel="alternate",
            type="application/rss+xml",
            title="Calendar Events",
            href="/calendar/rss",
        ),
    ),
)


@dataclass
class Event:
    id: int
    title: str
    date: str
    description: str
    url: Optional[str] = None
    location: Optional[str] = None
    invitees: Optional[str] = None
    recurring: Optional[str] = None  # Can be 'weekly', 'monthly', 'yearly', or None


def set_environment(config):
    """Set environment variables for the messenger app"""
    global app, logo_title_container
    app.config = config

    db = database(config.calendar.database_path)
    # create new events
    global events
    events = db.create(Event, pk="id")
    # add events from hydra config
    update_db_from_hydra()
    # init logo title container
    logo_title_container = create_logo_header(
        app_config=config.start_page.apps.calendar,
        base_url="/calendar",
        current_file_path=__file__
    )


def calendar_theme():
    """The active theme's tokens plus this app's layout variables.

    Resolved per-request so live `reconfigure` theme and layout swaps take
    effect. The `--layout-*` block is emitted here rather than in
    `_COMPONENT_CSS` because it is the only part of the stylesheet that
    depends on config.
    """
    layout = app.config.calendar
    return Style(
        render_theme_css(resolve_theme(app.config, "calendar"))
        + f"""
:root {{
  --layout-container-width: {layout.container_width};
  --layout-spacing: {layout.spacing};
  --layout-button-padding: {layout.button_padding};
}}
"""
    )


def current_layout():
    """The active structure variant from `config/apps/calendar/layout/`.

    Distinct from the `--layout-*` geometry variables above: those tune
    spacing, this one rearranges the page.
    """
    config = getattr(app, "config", None)
    if config is None:
        return "default"
    return getattr(config.calendar, "layout", "default")


def default_view():
    """Which view a request without an explicit `?view=` lands on.

    The `agenda_first` layout inverts the app's default so the agenda -- not
    the month grid -- is what an agent sees on arrival.
    """
    return "agenda" if current_layout() == "agenda_first" else "calendar"


_VIEWS = ("calendar", "agenda")


def resolve_view(view: str | None) -> str:
    """Normalise a `?view=` value, falling back to the layout's default.

    `get_calendar_content` renders anything that is not "calendar" as the
    agenda, so a typo'd or agent-constructed `?view=` used to land on the
    agenda with neither toggle button highlighted.
    """
    return view if view in _VIEWS else default_view()


def update_db_from_hydra():

    for event in app.config.calendar.events:
        # check for duplicates
        existing = events("title=? AND date=?", [event['title'], event['date']])
        if not existing:
            try:
                events.insert(Event(**event))
                # logger.info(f"Added event: {event['title']} on {event['date']}")
            except Exception as e:
                logger.error(f"Error adding event {event['title']}: {str(e)}")

    # Check if any events were added
    total_events = len(events())
    if total_events > 0:
        logger.info(f"Added {total_events} events from Hydra config.")
    else:
        logger.warning("No new events added from Hydra config.")

# Helper functions
def get_month_calendar(year, month):
    cal = calendar.monthcalendar(year, month)
    month_name = calendar.month_name[month]
    return cal, month_name


def generate_rss_feed():
    fg = FeedGenerator()
    fg.title("Calendar Events")
    fg.description("Upcoming events from our calendar")
    fg.link(href="https://example.com")

    # Change this line
    upcoming_events = get_upcoming_events(
        end_date=datetime.now().date() + timedelta(days=30)
    )
    for event in upcoming_events:
        fe = fg.add_entry()
        fe.title(event.title)
        fe.description(event.description)
        fe.link(href=f"https://example.com/calendar/event/{event.id}")
        fe.pubDate(datetime.strptime(event.date, "%Y-%m-%d"))

    return fg.rss_str(pretty=True)

def add_event_button():
    """The "Add Event" link -- the page's one filled, primary action. The
    footer shows it unless `sidebar_nav` has promoted it to the top of the
    rail, so there is only ever one on a page."""
    return A(
        "Add Event",
        href="/calendar/create_event/",
        target="_blank",
        role="button",
        cls="calendar-create",
    )


def create_footer(hide_add_button=False):
    add_button = add_event_button() if not hide_add_button else ""

    # Leaving the app is chrome, not a calendar action, so the variant layouts
    # shrink it to a quiet unfilled button beside "Add Event". The default
    # layout keeps it full-size and outlined: the click-only cross-app
    # navigation tasks (`config/tasks/original_tasks.yaml`) start here and
    # need a screenshot agent to find it at a glance.
    return_cls = "outline calendar-return"
    if current_layout() == "default":
        return_cls += " calendar-return-prominent"
    return_to_apps = A("Return to List of Apps", href="/", role="button", cls=return_cls)

    footer_buttons = Div(
        add_button, return_to_apps, cls="button-container"
    )

    return Div(footer_buttons, cls="footer-container")


def get_all_locations():
    return list(set(event.location for event in events()))


def get_events_for_month(year, month):
    # First, get events that directly fall in this month
    start_date = f"{year}-{month:02d}-01"
    end_date = f"{year}-{month:02d}-31"
    direct_month_events = events(f"date >= '{start_date}' AND date <= '{end_date}'")
    
    # Create a list to hold all events including recurring ones
    all_month_events = list(direct_month_events)
    
    # Now handle recurring events
    all_events = events()
    month_days = calendar.monthrange(year, month)[1]  # Get number of days in month
    
    for event in all_events:
        if not event.recurring:
            continue
            
        # Parse the original event date
        event_date = datetime.strptime(event.date, "%Y-%m-%d").date()
        
        # If the original event is in this month, it's already included
        if event_date.year == year and event_date.month == month:
            continue
            
        # Handle different recurrence types
        if event.recurring == "yearly":
            # Only include if the month and day match
            if event_date.month == month:
                # Create a new event instance for this year
                recurring_date = f"{year}-{event_date.month:02d}-{event_date.day:02d}"
                
                # Skip if the recurring date is invalid (e.g., Feb 29 in non-leap years)
                try:
                    datetime.strptime(recurring_date, "%Y-%m-%d")
                    recurring_event = Event(
                        id=event.id,  # Keep same ID as original
                        title=event.title,
                        date=recurring_date,
                        description=event.description,
                        url=event.url,
                        location=event.location,
                        invitees=event.invitees,
                        recurring=event.recurring
                    )
                    all_month_events.append(recurring_event)
                except ValueError:
                    pass
                
        elif event.recurring == "monthly":
            # Include if the day of month is valid for this month
            if event_date.day <= month_days:
                recurring_date = f"{year}-{month:02d}-{event_date.day:02d}"
                recurring_event = Event(
                    id=event.id,
                    title=event.title,
                    date=recurring_date,
                    description=event.description,
                    url=event.url,
                    location=event.location,
                    invitees=event.invitees,
                    recurring=event.recurring
                )
                all_month_events.append(recurring_event)
                
        elif event.recurring == "weekly":
            # Get the weekday of the original event
            event_weekday = event_date.weekday()
            
            # Check each day in this month
            for day in range(1, month_days + 1):
                check_date = datetime(year, month, day).date()
                
                # If it's the same weekday, add a recurring instance
                if check_date.weekday() == event_weekday:
                    recurring_date = f"{year}-{month:02d}-{day:02d}"
                    recurring_event = Event(
                        id=event.id,
                        title=event.title,
                        date=recurring_date,
                        description=event.description,
                        url=event.url,
                        location=event.location,
                        invitees=event.invitees,
                        recurring=event.recurring
                    )
                    all_month_events.append(recurring_event)
    
    return all_month_events


def get_upcoming_events(start_date=None, end_date=None):
    if start_date is None:
        start_date = datetime.now().date()
    if end_date is None:
        end_date = start_date + timedelta(days=30)

    # Get direct events in the date range
    direct_events = events(f"date >= '{start_date}' AND date <= '{end_date}'")
    
    # Create a list to hold all events including recurring ones
    all_events = list(direct_events)
    
    # Now handle recurring events
    all_stored_events = events()
    
    for event in all_stored_events:
        if not event.recurring:
            continue
            
        # Parse the original event date
        event_date = datetime.strptime(event.date, "%Y-%m-%d").date()
        
        # Get the date range to check
        current_date = start_date
        while current_date <= end_date:
            include_event = False
            recurring_date = None
            
            if event.recurring == "yearly" and event_date.month == current_date.month and event_date.day == current_date.day:
                # Yearly recurring event matching the month and day
                include_event = True
                recurring_date = f"{current_date.year}-{current_date.month:02d}-{current_date.day:02d}"
                
            elif event.recurring == "monthly" and event_date.day == current_date.day:
                # Monthly recurring event matching the day of month
                include_event = True
                recurring_date = f"{current_date.year}-{current_date.month:02d}-{current_date.day:02d}"
                
            elif event.recurring == "weekly" and event_date.weekday() == current_date.weekday():
                # Weekly recurring event matching the weekday
                include_event = True
                recurring_date = f"{current_date.year}-{current_date.month:02d}-{current_date.day:02d}"
            
            if include_event and recurring_date:
                # Skip the original event date if it's already in the direct events
                if event_date == current_date:
                    current_date += timedelta(days=1)
                    continue
                
                # Create a recurring instance
                recurring_event = Event(
                    id=event.id,
                    title=event.title,
                    date=recurring_date,
                    description=event.description,
                    url=event.url,
                    location=event.location,
                    invitees=event.invitees,
                    recurring=event.recurring
                )
                all_events.append(recurring_event)
            
            current_date += timedelta(days=1)
    
    return sorted(all_events, key=lambda e: e.date)  # Sort events by date


def show_main_layout(year, month, view="calendar", event_id=None):
    if event_id:
        event = events[event_id]
        return Titled(
            event.title,
            Div(
                H3(event.title),
                P(f"Date: {event.date}"),
                P(f"Location: {event.location}") if event.location else "",
                P(event.description),
                A(
                    "Back to Calendar",
                    href=f"/calendar/calendar_content/{year}/{month}?view={view}",
                    role="button",
                    cls="outline",
                ),
            ),
        )

    today = datetime.now().date()
    layout = current_layout()

    # Prev/next keep their "< Prev" / "Next >" labels (tasks and UI questions
    # name them) and gain a tooltip; the styling makes them compact toolbar
    # controls rather than full-width buttons.
    prev_link = A(
        "< Prev",
        href=f"/calendar/calendar_content/{year}/{month}?direction=prev&view={view}",
        role="button",
        cls="outline calendar-step",
        title="Previous month",
    )
    month_title = H2(f"{calendar.month_name[month]} {year}", id="current-month-year")
    next_link = A(
        "Next >",
        href=f"/calendar/calendar_content/{year}/{month}?direction=next&view={view}",
        role="button",
        cls="outline calendar-step",
        title="Next month",
    )
    # `/calendar` always opens on the current month, so "Today" is a link to
    # an existing route rather than new navigation state.
    today_link = A(
        "Today",
        href=f"/calendar?view={view}",
        role="button",
        cls="outline calendar-today",
        title=f"{calendar.day_name[today.weekday()]}, "
        f"{calendar.month_name[today.month]} {today.day}",
    )

    def view_link(name, label):
        # `active`/`outline` stay for anything keyed on them; `aria-current`
        # is what the segmented control styles and what an agent reading the
        # accessibility tree sees as "selected".
        selected = view == name
        return A(
            label,
            href=f"/calendar/calendar_content/{year}/{month}?view={name}",
            role="button",
            cls="active" if selected else "outline",
            aria_current="page" if selected else None,
        )

    calendar_link = view_link("calendar", "Calendar")
    agenda_link = view_link("agenda", "Agenda")

    # `agenda_first` leads with the agenda, so the toggle has to lead with it
    # too -- otherwise the highlighted button would sit second on arrival.
    toggle_links = (
        (agenda_link, calendar_link)
        if layout == "agenda_first"
        else (calendar_link, agenda_link)
    )
    view_toggle = Div(*toggle_links, cls="view-toggle", role="group", aria_label="View")

    cal, _ = get_month_calendar(year, month)
    month_events = get_events_for_month(year, month)

    if layout == "sidebar_nav":
        # A calendar app's left rail: "Add Event" leads as the filled primary
        # action, then the month title with its nav and a mini-month beneath
        # it, then the view toggle -- a real DOM order, not a CSS reshuffle,
        # so the accessibility tree reads in the order the rail is drawn.
        header = Div(
            add_event_button(),
            Div(
                month_title,
                Div(today_link, prev_link, next_link, cls="calendar-nav"),
                mini_month(year, month, cal, month_events, today),
                cls="calendar-rail-month",
            ),
            view_toggle,
            cls="calendar-header calendar-rail",
        )
    elif layout == "agenda_first":
        # A centred masthead: the month flanked by prev/next, with "Today" and
        # the toggle on a second line -- deliberately unlike the default's
        # single left-aligned toolbar.
        header = Div(
            Div(prev_link, month_title, next_link, cls="calendar-nav"),
            Div(today_link, view_toggle, cls="calendar-subbar"),
            cls="calendar-header calendar-header-centered",
        )
    else:
        # One toolbar row, as in Google/Outlook: Today, the month between its
        # prev/next, and the view switcher pushed to the far end.
        header = Div(
            Div(today_link, prev_link, month_title, next_link, cls="calendar-nav"),
            view_toggle,
            cls="calendar-header calendar-toolbar",
        )

    content = Div(
        get_calendar_content(year, month, view, cal, month_events),
        cls="calendar-content",
    )

    if layout == "sidebar_nav":
        # Nav and toggle become a left rail; the grid keeps the rest of the
        # width. `.calendar-table` / `.agenda-list` stay where they are, so
        # existing selectors and screenshots still resolve.
        body = (Div(header, content, cls="calendar-body-sidebar"),)
    else:
        body = (header, content)

    calendar_container = Div(
        logo_title_container,  # Add logo and title container here
        *body,
        create_footer(hide_add_button=layout == "sidebar_nav"),
        id="calendar-container",
    )

    event_dialog = Container(
        Div(id="event-dialog-content"),
        header=Div(Button("x", aria_label="Close", _="on click hide #event-dialog")),
        footer=Div(Button("Close", cls="secondary", _="on click hide #event-dialog")),
        id="event-dialog",
    )

    about_dialog = Container(
        Div(id="about-dialog-content", cls="marked"),
        header=Div(Button("x", aria_label="Close", _="on click hide #about-dialog")),
        footer=Div(Button("Close", cls="secondary", _="on click hide #about-dialog")),
        id="about-dialog",
    )

    return (
        Title(app.config.start_page.apps.calendar.title),
        Container(
            Style(_VIEW_CSS),
            calendar_container,
            event_dialog,
            about_dialog,
        ),
    )


@rt("/calendar")
def get(req):
    today = datetime.now()
    view = resolve_view(req.query_params.get("view"))
    # Get error message if present
    error_message = req.query_params.get("error")
    error_div = Div(error_message, cls="error-message") if error_message else ""

    return (
        Title(app.config.start_page.apps.calendar.title),
        Container(
            styles,
            calendar_theme(),
            error_div, 
            show_main_layout(today.year, today.month, view)
        ),
    )


@rt("/calendar/rss")
def get():
    rss_feed = generate_rss_feed()
    return Response(content=rss_feed, media_type="application/rss+xml")



@rt("/calendar/calendar_content/{year}/{month}")
def get(
    year: int,
    month: int,
    view: str = "",
    direction: str = None,
):
    # Empty rather than "calendar" so the layout decides the default; the
    # nav/toggle links always pass an explicit view, so this only applies to
    # a hand-typed or agent-constructed URL.
    view = resolve_view(view)

    if direction == "prev":
        date = datetime(year, month, 1) - timedelta(days=1)
        year, month = date.year, date.month
    elif direction == "next":
        date = datetime(year, month, 1) + timedelta(days=32)
        year, month = date.year, date.month

    return (
        Title(app.config.start_page.apps.calendar.title),
        Container(
            styles,
            calendar_theme(),
            show_main_layout(year, month, view)
        )
    )


# The toggle_location route has been removed as we're showing all events regardless of location


_WEEKEND_COLUMNS = (5, 6)  # `calendar.monthcalendar` weeks start on Monday


def _iso(year, month, day):
    return f"{year}-{month:02d}-{day:02d}"


def event_chip(event):
    """A month-grid event as a compact chip: title only, truncated by CSS.

    The location used to be appended as "(location)", which printed
    "(None)" under every event without one. It moves to the tooltip -- the
    agenda still shows it -- so the grid carries exactly what a real month
    view does.
    """
    tooltip = f"{event.title}, {event.location}" if event.location else event.title
    return A(
        event.title,
        href=f"/calendar/event/{event.id}",
        cls="event-link event-chip",
        title=tooltip,
    )


def mini_month(year, month, cal, month_events, today):
    """The `sidebar_nav` rail's mini-month, as in Google/Apple Calendar.

    Purely a glance: days are not links (there is no per-day route), and the
    main grid beside it carries the same dates, so it is hidden from the
    accessibility tree rather than giving an agent a second, inert set of
    day numbers to read.
    """
    busy = {e.date for e in month_events}
    head = Tr(*[Th(calendar.day_abbr[i][0]) for i in range(7)])
    rows = []
    for week in cal:
        cells = []
        for day in week:
            if day == 0:
                cells.append(Td(""))
                continue
            classes = []
            if datetime(year, month, day).date() == today:
                classes.append("today")
            if _iso(year, month, day) in busy:
                classes.append("busy")
            cells.append(Td(Span(str(day)), cls=" ".join(classes) or None))
        rows.append(Tr(*cells))
    return Table(Thead(head), Tbody(*rows), cls="mini-month", aria_hidden="true")


def month_grid(year, month, cal, month_events, today):
    weekday_headers = [
        Th(
            calendar.day_abbr[i],
            scope="col",
            cls="weekend" if i in _WEEKEND_COLUMNS else None,
        )
        for i in range(7)
    ]

    calendar_body = []
    for week in cal:
        week_row = []
        for col, day in enumerate(week):
            weekend = " weekend" if col in _WEEKEND_COLUMNS else ""
            if day == 0:
                # Days outside the month stay blank -- a second "1" from the
                # next month would give an agent two cells with the same
                # label -- but are shaded so the month's edges read at a glance.
                week_row.append(Td("", cls="calendar-cell-empty" + weekend))
                continue
            iso = _iso(year, month, day)
            is_today = datetime(year, month, day).date() == today
            day_content = [
                Div(
                    str(day),
                    cls="day-number today" if is_today else "day-number",
                    aria_current="date" if is_today else None,
                ),
                *[event_chip(e) for e in month_events if e.date == iso],
            ]
            week_row.append(Td(*day_content, cls="calendar-cell" + weekend))
        calendar_body.append(Tr(*week_row))

    return Table(
        Thead(Tr(*weekday_headers)), Tbody(*calendar_body), cls="calendar-table"
    )


def agenda(year, month, upcoming_events, today):
    """The month's events grouped under one date header per day.

    Events carry a date but no time, so every row is "All day" -- the label
    a real calendar gives a dateless event -- rather than an empty column.
    """
    if not upcoming_events:
        # Keep the list itself so `.agenda-list` resolves (and has height)
        # even for an empty month.
        return Ul(
            Li(
                P(f"No events in {calendar.month_name[month]} {year}", cls="agenda-empty-title"),
                P("Use Add Event to schedule one, or Next > to look ahead."),
                cls="agenda-empty",
            ),
            cls="agenda-list",
        )

    days = []
    for iso, day_events in groupby(upcoming_events, key=lambda e: e.date):
        day = datetime.strptime(iso, "%Y-%m-%d").date()
        is_today = day == today
        header = H4(
            Time(
                Span(str(day.day), cls="agenda-day-number"),
                " ",
                Span(
                    f"{calendar.month_abbr[day.month]}, {calendar.day_abbr[day.weekday()]}",
                    cls="agenda-date-label",
                ),
                datetime=iso,
            ),
            cls="agenda-date today" if is_today else "agenda-date",
            aria_current="date" if is_today else None,
        )
        rows = [
            Li(
                Span("All day", cls="agenda-time"),
                Div(
                    A(e.title, href=f"/calendar/event/{e.id}", cls="agenda-event-link"),
                    Span(e.location, cls="agenda-location") if e.location else "",
                    cls="agenda-event-body",
                ),
                cls="agenda-event",
            )
            for e in day_events
        ]
        days.append(Li(header, Ul(*rows, cls="agenda-events"), cls="agenda-day"))

    return Ul(*days, cls="agenda-list")


def get_calendar_content(year, month, view, cal, month_events):
    today = datetime.now().date()
    if view == "calendar":
        return month_grid(year, month, cal, month_events, today)
    start_date = datetime(year, month, 1).date()
    end_date = (start_date.replace(day=28) + timedelta(days=4)).replace(
        day=1
    ) - timedelta(days=1)
    upcoming_events = get_upcoming_events(start_date=start_date, end_date=end_date)
    return agenda(year, month, upcoming_events, today)


@rt("/calendar/event/{id}")
def get(id: int):
    event = events[id]
    event_url = A("Event Link", href=event.url, target="_blank") if event.url else ""
    
    # Display recurring information
    recurring_info = ""
    if event.recurring:
        recurring_info = P(f"Recurring: {event.recurring.capitalize()}")
    
    # Create delete form
    delete_form = Form(
        Button("Delete Event", type="submit", cls="outline error"),
        method="post",
        action=f"/calendar/event/{id}/delete",
    )
    return (
        Title(event.title),
        Container(
            styles,
            calendar_theme(),
            logo_title_container,
            Article(
                H3(event.title),
                P(f"Date: {event.date}"),
                P(f"Location: {event.location}") if event.location else "",
                recurring_info,
                Div(event.description, cls="marked"),
                event_url,
                Hr(),
                delete_form,
            ),
            create_footer(),
        ),
    )


@rt("/calendar/event/{id}/delete", methods=["POST"])
async def delete_event(id: int):
    try:
        # Delete from database
        event = events[id]
        events.delete(id)
        logger.info(f"Successfully deleted event: {event.title} on {event.date}")
        return RedirectResponse(url="/calendar", status_code=303)

    except Exception as e:
        logger.error(f"Error deleting event: {str(e)}")
        return RedirectResponse(
            url="/calendar?error=Failed+to+delete+event", status_code=303
        )


@rt("/calendar/create_event")
def get():
    # Get all existing locations for the dropdown (still needed for the create event form)
    all_locations = get_all_locations()

    def get_input_attrs(field_name: str, defaults: dict) -> dict:
        """Builds input placeholder and aria label attributes from config."""
        attrs = defaults.copy()
        add_event_config = app.config.calendar.style.add_event_display
        try:
            # Get placeholder from config if it exists
            placeholder = getattr(add_event_config.placeholder, field_name, None)
            if placeholder:
                attrs['placeholder'] = placeholder
        except AttributeError:
            pass

        try:
            # Get aria-label from config if it exists
            aria_label = getattr(add_event_config.aria_label, field_name, None)
            if aria_label:
                attrs['aria_label'] = aria_label
        except AttributeError:
            pass
        
        return attrs

    return (Title("Creating a new event"),
            Container(
        styles,
        calendar_theme(),
        logo_title_container,
        Form(
            H3("Create New Event"),
            
            Label("Title", For="title"),
            Input(**get_input_attrs('title', {'type': 'text', 'id': 'title', 'name': 'title', 'required': True})),
            
            Label("Date", For="date"),
            Input(**get_input_attrs('date', {'type': 'text', 'id': 'date', 'name': 'date', 'required': True})),
            
            Label("Description", For="description"),
            Textarea(**get_input_attrs('description', {'id': 'description', 'name': 'description'})),

            Label("URL", For="url"),
            Input(**get_input_attrs('url', {'type': 'url', 'id': 'url', 'name': 'url'})),

            Label("Invitees", For="invitees"),
            Input(**get_input_attrs('invitees', {'type': 'text', 'id': 'invitees', 'name': 'invitees'})),

            Label("Location", For="location"),
            Input(**get_input_attrs('location', {'type': 'text', 'id': 'location', 'name': 'location'})),
            
            Label("Recurring", For="recurring"),
            Select(
                Option("Not Recurring", value="none", selected=True),
                Option("Weekly", value="weekly"),
                Option("Monthly", value="monthly"),
                Option("Yearly", value="yearly"),
                id="recurring",
                name="recurring"
            ),
            
            Button("Submit", type="submit"),
            method="post",
            action="/calendar/create_event/save_text"
        ),
        create_footer(hide_add_button=True),
    ))

@rt("/calendar/create_event/save_text", methods=["POST"])
async def save_text(request):  # Add async here
    try:
        # Get form data directly
        form = await request.form()
        title = form.get("title", "")
        date = form.get("date", "")
        description = form.get("description", "")
        url = form.get("url", "")
        location = form.get("location", "")
        invitees = form.get("invitees", "")
        recurring = form.get("recurring", "none")
        
        # Set recurring to None if "none" is selected
        if recurring == "none":
            recurring = None

        if not all([title, date]):
            logger.error("No text content received")
            return RedirectResponse(
                url="/calendar?error=No+content+provided", status_code=303
            )
        # examine whether date follows the format YYYY-MM-DD
        try:
            datetime.strptime(date, "%Y-%m-%d")
        except ValueError:
            logger.error("Invalid date format. Expected YYYY-MM-DD.")
            return RedirectResponse(
                url="/calendar?error=Invalid+date+format", status_code=303
            )

        # Create a new Event object
        event = Event(
            id=None,
            title=title,
            date=date,
            description=description,
            url=url,
            location=location,
            invitees=invitees,
            recurring=recurring
        )

        # Save the event to the database
        events.insert(event)

        # Redirect to calendar view
        return RedirectResponse(url="/calendar", status_code=303)
    except Exception as e:
        logger.error(f"Error processing form: {str(e)}")
        return RedirectResponse(url="/calendar?error=Failed+to+process+form")


def get_calendar_routes():
    return app.routes

@app.get("/calendar_all")
def get_all():
    """Used for rewards"""
    event_list: List[dict] = [event.__dict__ for event in events()]
    return Response(json.dumps(event_list), headers={"Content-Type": "application/json"})

if __name__ == "__main__":
    print(
        "Warning: Running calendar app in standalone mode. Go to http://localhost:5001/calendar."
    )  # Changed from "todo app"
    app.routes = get_calendar_routes()
    serve()
