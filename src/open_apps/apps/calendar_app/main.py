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
    Ul, Li, Hr, Article, Button, RedirectResponse, Container, MarkdownJS,
    HighlightJS, database, dataclass)
from datetime import datetime, timedelta
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

def generate_styles_from_config(config):
    """Generate CSS styles from configuration"""
    if not hasattr(config.calendar, 'style'):
        raise ValueError("Calendar config does not contain 'style' section")
    
    # Extract style config
    style_config = config.calendar.style
    colors = style_config.colors
    typography = style_config.typography
    buttons = style_config.buttons
    layout = style_config.layout
    
    # Build CSS with variables
    return f"""
        /* Custom CSS Variables */
        :root {{
            --primary: {colors.primary};
            --primary-hover: {colors.primary_hover};
            --secondary: {colors.secondary};
            --background: {colors.background};
            --text: {colors.text};
            --error: {colors.error};
            --border: {colors.border};
            --font-family: {typography.font_family};
            --heading-font: {typography.heading_font};
            --base-font-size: {typography.base_font_size};
            --heading-size: {typography.heading_size};
            --button-border-radius: {buttons.border_radius};
            --button-padding: {buttons.padding};
            --container-width: {layout.container_width};
            --spacing: {layout.spacing};
        }}
        
        /* Base styles */
        body {{
            font-family: var(--font-family);
            font-size: var(--base-font-size);
            color: var(--text);
            background-color: var(--background);
        }}
        
        h1, h2, h3, h4, h5, h6 {{
            font-family: var(--heading-font);
        }}
        
        h1 {{
            font-size: var(--heading-size);
        }}
        
        /* Button styles */
        [role="button"], button {{
            border-radius: var(--button-border-radius);
            padding: var(--button-padding);
        }}
        
        /* Apply primary color to buttons */
        [role="button"]:not(.outline):not(.secondary),
        button:not(.outline):not(.secondary) {{
            background-color: var(--primary);
            border-color: var(--primary);
        }}
        
        /* Apply hover state for primary buttons */
        [role="button"]:not(.outline):not(.secondary):hover,
        button:not(.outline):not(.secondary):hover {{
            background-color: var(--primary-hover);
            border-color: var(--primary-hover);
        }}
        
        /* Secondary buttons */
        [role="button"].secondary,
        button.secondary {{
            background-color: var(--secondary);
            border-color: var(--secondary);
        }}
        
        /* Apply border color to form elements */
        input, select, textarea {{
            border: 1px solid var(--border);
            border-radius: var(--button-border-radius);
        }}

        [role="button"].outline,
        button.outline {{
            background-color: var(--background);
            border-color: var(--border);
            color: var(--primary);
        }}
        
        /* Apply container width */
        #calendar-container {{
            width: var(--container-width);
            margin: 0 auto;
        }}
        
        /* Apply border color to tables */
        table, th, td {{
            border-color: var(--border);
        }}
        
        /* Calendar days */
        .calendar-cell {{
            border: 1px solid var(--border);
            background-color: var(--background);
        }}
        
        /* Links */
        a:not([role="button"]) {{
            color: var(--primary);
        }}
        
        a:not([role="button"]):hover {{
            color: var(--primary-hover);
        }}
        
        /* Calendar app specific styles */
        .logo-title-container {{
            display: flex;
            align-items: center;
            text-decoration: none;
        }}
        
        .custom-logo {{
            max-height: 50px;
            margin-right: 10px;
        }}
        .calendar-title {{
            margin: 0;
            color: var(--primary);
        }}
        
        .logo-title-container a {{
            text-decoration: none;
        }}
        
        .button-container {{
            display: flex;
            justify-content: space-between;
            margin-top: var(--spacing);
        }}
        
        .error-message {{
            background-color: rgba(220, 53, 69, 0.1);
            color: var(--error);
            padding: var(--spacing);
            margin-bottom: var(--spacing);
            border-radius: var(--button-border-radius);
            text-align: center;
        }}
    """

# Initialize with default styles
# Will be updated in set_environment
styles = Style("")


app, rt = fast_app(
    pico=True,
    hdrs=(
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
    global app, styles, logo_title_container
    app.config = config
    
    # Update the styles from config
    styles = Style(generate_styles_from_config(config))
    
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

def create_footer(hide_add_button=False):
    # Add event button
    add_button = (
        A(
            "Add Event",
            href="/calendar/create_event/",
            target="_blank",
            role="button",
            cls="outline"
        )
        if not hide_add_button
        else ""
    )

    
    # Add "Return to List of Apps" button
    return_to_apps = A("Return to List of Apps", href="/", role="button", cls="outline")

    footer_buttons = Div(
        add_button, return_to_apps, cls="button-container"
    )


    return Div(footer_buttons,  cls="footer-container")


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
                P(f"Location: {event.location}"),
                P(event.description),
                A(
                    "Back to Calendar",
                    href=f"/calendar/calendar_content/{year}/{month}?view={view}",
                    role="button",
                    cls="outline",
                ),
            ),
        )

    nav = Div(
        A(
            "< Prev",
            href=f"/calendar/calendar_content/{year}/{month}?direction=prev&view={view}",
            role="button",
            cls="outline",
        ),
        H2(f"{calendar.month_name[month]} {year}", id="current-month-year"),
        A(
            "Next >",
            href=f"/calendar/calendar_content/{year}/{month}?direction=next&view={view}",
            role="button",
            cls="outline",
        ),
        cls="calendar-nav",
    )

    view_toggle = Div(
        A(
            "Calendar",
            href=f"/calendar/calendar_content/{year}/{month}?view=calendar",
            role="button",
            cls="active" if view == "calendar" else "outline",
        ),
        A(
            "Agenda",
            href=f"/calendar/calendar_content/{year}/{month}?view=agenda",
            role="button",
            cls="active" if view == "agenda" else "outline",
        ),
        cls="view-toggle",
    )

    header = Div(
        nav,
        view_toggle,
    )

    cal, _ = get_month_calendar(year, month)
    month_events = get_events_for_month(year, month)
    content = get_calendar_content(
        year, month, view, cal, month_events
    )

    calendar_container = Div(
        logo_title_container,  # Add logo and title container here
        header,
        content,
        create_footer(),
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

    # Add calendar-specific styles that don't come from config
    calendar_specific_styles = Style(
        """
        .calendar-nav { display: flex; justify-content: space-between; align-items: center; margin-bottom: var(--spacing); }
        .calendar-nav h2 { margin: 0; }
        .view-toggle { display: flex; justify-content: center; gap: 10px; margin-bottom: var(--spacing); }
        .calendar-table { width: 100%; table-layout: fixed; }
        .calendar-table th { text-align: center; font-weight: bold; }
        .calendar-cell { height: 100px; vertical-align: top; padding: 5px !important; }
        .day-number { font-weight: bold; margin-bottom: 5px; }
        .event-link { display: block; margin-bottom: 2px; font-size: 0.8em; }
        .agenda-list { list-style-type: none; padding: 0; }
        .agenda-list li { margin-bottom: var(--spacing); }
        .footer-container { margin-top: var(--spacing); }
        #about-dialog-content { padding: var(--spacing); }
        """
    )
    
    return (
        Title(app.config.start_page.apps.calendar.title),
        Container(
            calendar_specific_styles,
            calendar_container,
            event_dialog,
            about_dialog,
        ),
    )


@rt("/calendar")
def get(req):
    today = datetime.now()
    view = req.query_params.get("view", "calendar")
    # Get error message if present
    error_message = req.query_params.get("error")
    error_div = Div(error_message, cls="error-message") if error_message else ""

    return (
        Title(app.config.start_page.apps.calendar.title),
        Container(
            styles,
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
    view: str = "calendar",
    direction: str = None,
):
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
            show_main_layout(year, month, view)
        )
    )


# The toggle_location route has been removed as we're showing all events regardless of location


def get_calendar_content(year, month, view, cal, month_events):
    if view == "calendar":
        weekdays = [calendar.day_abbr[i] for i in range(7)]
        weekday_headers = [Th(day) for day in weekdays]

        calendar_body = []
        for week in cal:
            week_row = []
            for day in week:
                if day == 0:
                    week_row.append(Td(""))
                else:
                    day_events = [
                        e
                        for e in month_events
                        if e.date == f"{year}-{month:02d}-{day:02d}"
                    ]
                    day_content = [
                        Div(str(day), cls="day-number"),
                        *[
                            A(
                                f"{e.title} ({e.location})",
                                href=f"/calendar/event/{e.id}",
                                cls="event-link",
                            )
                            for e in day_events
                        ],
                    ]
                    week_row.append(Td(*day_content, cls="calendar-cell"))
            calendar_body.append(Tr(*week_row))

        return Table(
            Thead(Tr(*weekday_headers)), Tbody(*calendar_body), cls="calendar-table"
        )
    else:  # Agenda view
        start_date = datetime(year, month, 1).date()
        end_date = (start_date.replace(day=28) + timedelta(days=4)).replace(
            day=1
        ) - timedelta(days=1)
        upcoming_events = get_upcoming_events(
            start_date=start_date, end_date=end_date
        )
        return Ul(
            *[
                Li(
                    H4(e.date),
                    A(f"{e.title} ({e.location})", href=f"/calendar/event/{e.id}"),
                )
                for e in upcoming_events
            ],
            cls="agenda-list",
        )


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
            logo_title_container,
            Article(
                H3(event.title),
                P(f"Date: {event.date}"),
                P(f"Location: {event.location}"),
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
