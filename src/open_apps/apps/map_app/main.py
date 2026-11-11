"""
Copyright (c) Meta Platforms, Inc. and affiliates.
All rights reserved.
This source code is licensed under the license found in the
LICENSE file in the root directory of this source tree.
"""
from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
import uvicorn
import os
import json
import requests
from fasthtml.common import *
import requests
import json
from open_apps.i18n import html_lang, language_of, t
from open_apps.theme import render_theme_css, resolve_theme, theme_asset
from datetime import datetime, timezone
import subprocess
import time


@dataclass
class Landmark:
    name: str
    lat: float
    lng: float
    icon: str = "map-marker"
    color: str = "blue"


# Get the current directory path
current_dir = os.path.dirname(os.path.abspath(__file__))
app = FastAPI()
landmarks = None
otp_process = None # Added: To store the OTP process
OTP_SERVER_DIR = current_dir
OTP_JAR_NAME = "otp-2.6.0-shaded.jar"
OTP_DATA_DIR_NAME = "otp-data" # This will be relative to OTP_SERVER_DIR for the --load command

OTP_JAR_PATH = os.path.join(OTP_SERVER_DIR, OTP_JAR_NAME)
OTP_STARTUP_COMMAND_LIST = [
    "java", "-Xmx8G",
    "-jar", OTP_JAR_PATH,
    "--load", OTP_DATA_DIR_NAME, # Path for --load is relative to cwd of the subprocess
    "--port", "8080"
]


# TODO: Replace this with the actual command to start your OTP server.
OTP_STARTUP_COMMAND = "java -Xmx8G -jar otp-2.6.0-shaded.jar --load otp-data/ --port 8080"
def stop_otp_server(): # Added
    global otp_process
    if otp_process:
        print(f"Stopping OTP server (PID: {otp_process.pid})...")
        otp_process.terminate() # Send SIGTERM
        try:
            otp_process.wait(timeout=10) # Wait up to 10 seconds for graceful shutdown
            print("OTP server process terminated gracefully.")
        except subprocess.TimeoutExpired:
            print("OTP server process did not terminate gracefully, killing...")
            otp_process.kill() # Send SIGKILL
            otp_process.wait()
            print("OTP server process killed.")
        otp_process = None

def set_environment(config):
    global app, landmarks, otp_process, OTP_STARTUP_COMMAND_LIST, OTP_SERVER_DIR
    app.config = config
    db = database(config.maps.database_path)
    landmarks = db.create(Landmark, pk="name")
    # populate landmarks from config
    for landmark in config.maps.saved_places:
        landmarks.insert(Landmark(**landmark))

    if hasattr(app, 'config') and hasattr(app.config, 'maps') and app.config.maps.allow_planning:
        try:
            print(f"- map planning is not supported yet; the calculate route button is a dummy...")
        except Exception as e:
            print(f"Failed to start OTP server: {e}")
            if otp_process: 
                otp_process.terminate()
                otp_process.wait()
                otp_process = None



templates = Jinja2Templates(directory=os.path.join(current_dir, "templates"))

@app.on_event("startup") # Added
async def startup_event():
    # The set_environment function is usually called before FastAPI's own startup events
    # if app.config is available. If not, OTP server might not start if allow_planning check fails.
    # Ensure app.config is populated before this event if relying on it here.
    # Alternatively, move the start_otp_server call to the end of set_environment if config is ready there.
    start_otp_server()

@app.on_event("shutdown") # Added
async def shutdown_event():
    stop_otp_server()

def current_tile_layer() -> str:
    """Leaflet basemap for the active theme.

    Tiles are raster images served from a CDN, so no design token can recolor
    them. A theme names a `tone`; `layer_by_tone` may map that onto one of the
    layers defined in templates/map.html, and anything unmapped keeps the
    configured `default_layer`. The shipped config maps nothing: dark and mono
    darken or grey the default layer with `filter_by_tone` instead (see
    :func:`current_tile_filter`), so a themed map shows the same streets and
    labels as the light one and loads from the same tile server.
    """
    cfg = app.config.maps
    tone = theme_asset(app.config, "maps", "tone", "light")
    by_tone = getattr(cfg, "layer_by_tone", None)
    if by_tone is not None and tone in by_tone:
        return by_tone[tone]
    return cfg.default_layer


# A CSS filter value may not close the declaration, the rule or the <style>.
_UNSAFE_FILTER_CHARS = set("<>;{}\"'\\")


def current_tile_filter() -> str:
    """CSS `filter` drawn over the street-map tiles for the active tone, or "".

    The robust way to theme raster tiles: `invert(1) hue-rotate(180deg)` turns
    OpenStreetMap's light cartography into a dark map with its hues roughly
    intact (water stays blue, parks green), and `grayscale(1)` drains it for
    `mono`. It needs no keyed style CDN -- the dark CartoDB layer rendered
    every tile as "API KEY REQUIRED" -- and, unlike a separate dark canvas,
    it keeps the place labels, so the dark map carries the same information
    as the light one. The template applies it to the tile layers only, so
    markers, popups and controls (other panes) are never inverted, and the
    imagery/canvas layers in the switcher opt out (an inverted satellite
    photo is noise, not a dark theme).
    """
    by_tone = getattr(app.config.maps, "filter_by_tone", None)
    if not by_tone:
        return ""
    tone = theme_asset(app.config, "maps", "tone", "light")
    value = str(by_tone.get(tone, "") or "").strip()
    if any(c in _UNSAFE_FILTER_CHARS for c in value):
        return ""
    return value


# The page's own strings: pack key -> English. Each is `apps.maps.<key>` from
# the content pack when set (german.yaml / mandarin.yaml translate them), else
# the English here, so a pack that predates a key still renders. `{name}` /
# `{n}` placeholders are filled in by the page script.
LABELS: dict[str, str] = {
    "page_title_label": "World Map",
    "search_placeholder_label": "Search location...",
    "current_location_label": "Current Location Info",
    "current_location_empty_label": "Click anywhere on the map to see its address and coordinates.",
    "route_planning_label": "Route Planning",
    "from_placeholder_label": "From location",
    "to_placeholder_label": "To location",
    "calculate_route_label": "Calculate Route",
    "select_route_option_label": "Select Route Option:",
    "select_an_option_label": "Select an option",
    "saved_locations_label": "Saved Locations",
    "location_label": "Location:",
    "coordinates_label": "Coordinates:",
    "save_location_aria_label": "Save location: {name}",
    "marker_aria_label": "Marker for {name}",
    "delete_place_aria_label": "Delete {name}",
    "searching_label": "Searching...",
    "no_results_label": "No results found.",
    "search_failed_label": "Search failed.",
    "error_label": "Error:",
    "select_both_label": "Please select both locations",
    "no_route_legs_label": "No route found or route has no legs.",
    "no_route_label": "No route found.",
    "route_hint_label": "Check locations or try different modes/times.",
    "route_failed_label": "Failed to fetch or process route. Check console for details.",
    "route_details_label": "Route Details:",
    "departure_label": "Departure:",
    "arrival_label": "Arrival:",
    "line_label": "Line:",
    "total_duration_label": "Total Duration:",
    "total_distance_label": "Total Distance:",
    "minutes_label": "minutes",
    "option_label": "Option {n}",
    "details_unavailable_label": "Details unavailable",
    "customize_marker_label": "Customize Marker",
    "icon_label": "Icon:",
    "color_label": "Color:",
    "location_not_found_label": "Location not found",
    "route_unsupported_label": "route planning not supported, stay tuned!",
}

# Marker dialog options: value (Font Awesome icon / awesome-markers colour,
# stored in the landmark row) -> English caption. Values never change; packs
# translate only the captions, via `marker_icon_labels` / `marker_color_labels`.
MARKER_ICONS: dict[str, str] = {
    "map-marker": "Default Marker",
    "utensils": "Restaurant",
    "tree": "Park",
    "hospital": "Hospital",
    "graduation-cap": "School",
    "shopping-cart": "Shop",
    "coffee": "Cafe",
    "bed": "Hotel",
    "dumbbell": "Gym",
    "book": "Library",
    "landmark": "Museum",
    "plane": "Airport",
    "train": "Train Station",
    "bus": "Bus Station",
    "ship": "Port/Harbor",
    "parking": "Parking",
    "film": "Theater/Cinema",
    "futbol": "Stadium",
    "swimming-pool": "Swimming Pool",
    "money-bill": "Bank/ATM",
    "envelope": "Post Office",
    "prescription-bottle-alt": "Pharmacy",
}
MARKER_COLORS: dict[str, str] = {
    "red": "Red",
    "blue": "Blue",
    "green": "Green",
    "orange": "Orange",
    "purple": "Purple",
    "darkred": "Dark Red",
    "darkblue": "Dark Blue",
    "darkgreen": "Dark Green",
    "darkpurple": "Dark Purple",
    "cadetblue": "Cadet Blue",
}

# Locale for the route planner's clock times.
_TIME_LOCALE = {"english": "en-US", "german": "de-DE", "mandarin": "zh-CN"}


def current_language() -> str:
    """The maps pack's `content_language` (English when unset)."""
    config = getattr(app, "config", None)
    return language_of(getattr(config, "maps", None) if config is not None else None)


def page_labels() -> dict:
    """Every string the page renders, in the pack's language.

    One dict for both consumers: Jinja reads it as `L`, and the page script
    gets the same dict as JSON, so a string never has two sources.
    """
    cfg = app.config.maps
    lang = language_of(cfg)
    labels: dict = {key: str(cfg.get(key) or default) for key, default in LABELS.items()}
    for key in ("return_to_apps", "search", "cancel", "confirm"):
        labels[key] = t(key, lang)
    icon_names = cfg.get("marker_icon_labels") or {}
    color_names = cfg.get("marker_color_labels") or {}
    labels["marker_icons"] = {k: str(icon_names.get(k) or v) for k, v in MARKER_ICONS.items()}
    labels["marker_colors"] = {k: str(color_names.get(k) or v) for k, v in MARKER_COLORS.items()}
    labels["time_locale"] = _TIME_LOCALE.get(lang, "en-US")
    return labels


def _label(key: str) -> str:
    """One of :data:`LABELS` in the pack's language, for server responses."""
    cfg = getattr(getattr(app, "config", None), "maps", None)
    return str((cfg.get(key) if cfg is not None else None) or LABELS[key])


def current_layout() -> str:
    """The active structure variant from `config/apps/maps/layout/`.

    Emitted as a `layout-<name>` class on <body>. map.html also emits the
    panel in each layout's visual order (so the accessibility tree reads the
    way the page looks) and `map_controls` places Leaflet's controls, but the
    map's ids, labels and routes are identical across layouts and rewards --
    which read /maps/landmarks, not the DOM -- are unaffected.
    """
    config = getattr(app, "config", None)
    if config is None:
        return "default"
    return getattr(config.maps, "layout", "default")


# Where Leaflet's zoom bar and basemap switcher sit in each layout, after the
# product that layout imitates. `default` keeps the basemap list expanded at the
# top right: the UI-understanding questions built from its screenshot
# (tests/ui_questions) ask about those visible radio options. The other layouts
# fold it into a layers button, as real maps do -- Google Maps desktop puts it
# bottom-left with zoom bottom-right. Collapsed, the radios appear on hover or
# click of the button; tasks only read /maps/landmarks, never the basemap.
MAP_CONTROLS = {
    "default": {"zoom_position": "topleft", "layers_position": "topright", "layers_collapsed": False},
    "sidebar_left": {"zoom_position": "bottomright", "layers_position": "bottomleft", "layers_collapsed": True},
    "bottom_sheet": {"zoom_position": "topleft", "layers_position": "topright", "layers_collapsed": True},
}


def map_controls(layout: str) -> dict:
    """Leaflet control placement for `layout`; unknown layouts get the default's."""
    return MAP_CONTROLS.get(layout, MAP_CONTROLS["default"])


@app.get("/maps", response_class=HTMLResponse)
async def map_page(request: Request):
    labels = page_labels()
    return templates.TemplateResponse(
        "map.html",
        {
            "request": request,
            "L": labels,
            # `</` escaped so a translated string can never close the <script>.
            "labels_json": json.dumps(labels, ensure_ascii=False).replace("</", "<\\/"),
            "html_lang": html_lang(current_language()),
            "tile_filter": current_tile_filter(),
            "layout": current_layout(),
            "controls": map_controls(current_layout()),
            "enable_layer_control": app.config.maps.enable_layer_control,
            "default_layer": current_tile_layer(),
            "popup_display_rule": app.config.maps.popup_display_rule,
            "title": app.config.maps.title,
            "init_location": app.config.maps.init_location,
            "zoom": app.config.maps.zoom,
            "granularity": app.config.maps.granularity,
            "allow_planning": app.config.maps.allow_planning,
            # Every color and font in the template is a var() into this block.
            # Resolved per-request so live `reconfigure` theme swaps take effect.
            "theme_css": render_theme_css(resolve_theme(app.config, "maps")),
        },
    )


@app.get("/maps/where")
async def where(q: str):
    """
    This function takes a query string `q` and returns the OSM ID of the location.
    """
    try:
        # Use the Nominatim API to geocode the location
        response = requests.get(
            f"https://nominatim.openstreetmap.org/search?q={q}&format=json&limit=1"
        )
        response.raise_for_status()  # Raise an exception for bad status codes
        data = response.json()

        if data:
            # Extract the OSM ID from the response
            osm_id = data[0].get("osm_id")
            return {"osm_id": osm_id}
        else:
            return {"error": _label("location_not_found_label")}
    except requests.exceptions.RequestException as e:
        return {"error": f"API request failed: {e}"}
    except Exception as e:
        return {"error": f"An error occurred: {e}"}


@app.get("/maps/landmarks")
def get_landmarks():
    return Response(
        json.dumps(
            [
                {
                    "name": l.name,
                    "coords": [l.lat, l.lng],
                    "markerStyle": {"icon": l.icon, "color": l.color},
                }
                for l in landmarks()
            ]
        ),
        media_type="application/json",
    )


@app.post("/maps/add_landmarks")
async def add_landmark(request: Request):
    try:
        data = await request.json()
        # first check if the landmark already exists
        try:
            existing_landmark = landmarks.get(data["name"])
        except:
            existing_landmark = None
        if existing_landmark:
            return Response(
                json.dumps({"error": f"Landmark '{data['name']}' already exists"}),
                status_code=409,
                media_type="application/json",
            )
        # Extract marker style if provided, otherwise use defaults
        marker_style = data.get("markerStyle", {})
        if marker_style is None:
            marker_style = {}
        landmarks.insert(
            Landmark(
                name=data["name"],
                lat=data["coords"][0],
                lng=data["coords"][1],
                icon=marker_style.get("icon", "map-marker"),
                color=marker_style.get("color", "blue"),
            )
        )
        return Response(
            json.dumps(
                {
                    "name": data["name"],
                    "coords": [data["coords"][0], data["coords"][1]],
                    "markerStyle": marker_style,
                }
            ),
            media_type="application/json",
        )
    except Exception as e:
        return Response(
            json.dumps({"error": str(e)}),
            status_code=400,
            media_type="application/json",
        )


@app.delete("/maps/landmarks/{name}")
def delete_landmark(name: str):
    try:
        landmarks.delete(name)
        return Response(status_code=204)
    except Exception as e:
        return Response(
            json.dumps({"error": str(e)}),
            status_code=400,
            media_type="application/json",
        )

@app.get("/maps/route")
async def get_route(
    from_lat: float,
    from_lon: float,
    to_lat: float,
    to_lon: float,
    time: str = None,
    date: str = None,
    mode: str = "SUBWAY,WALK"
):
    """
    Get a route between two points using OpenTripPlanner
    """
    otp_graphql_url = f"{app.config.maps.otp_url}/otp/gtfs/v1"
    
    current_dt_utc = datetime.now(timezone.utc)
    if time is None:
        time_str = current_dt_utc.strftime("%H:%M:%S")
    else:
        # Ensure time is in HH:MM:SS, pad if necessary
        time_parts = time.split(':')
        if len(time_parts) == 2: # HH:MM
            time_str = f"{time_parts[0]}:{time_parts[1]}:00"
        elif len(time_parts) == 3: # HH:MM:SS
            time_str = time
        else: # Fallback to current time if format is unexpected
            time_str = current_dt_utc.strftime("%H:%M:%S")


    if date is None:
        date_str = current_dt_utc.strftime("%Y-%m-%d")
    else:
        date_str = date

    # OTPv2 dateTime typically expects ISO 8601 format, e.g., "YYYY-MM-DDTHH:MM:SS"
    # Timezone information might be needed depending on OTP server configuration.
    # Example: "2023-06-13T14:30:00" or "2023-06-13T14:30:00-07:00"
    # For simplicity, combining date and time. Add timezone if required.
    # dateTime_str = f"{date}T{time}"
    dateTime_str = f"{date_str}T{time_str}Z"

    graphql_query = """
    query PlanConnectionQuery(
        $dateTime: OffsetDateTime!, 
        $fromLat: CoordinateValue!, $fromLon: CoordinateValue!,
        $toLat: CoordinateValue!, $toLon: CoordinateValue!
    ) {
      planConnection(
        origin: { location: { coordinate: { latitude: $fromLat, longitude: $fromLon } } }
        destination: { location: { coordinate: { latitude: $toLat, longitude: $toLon } } }
        dateTime: { earliestDeparture: $dateTime }
        modes:{
            direct: [WALK]
            transit: {transit: [{mode: SUBWAY}]}
        }
      ) {
        edges {
          node {
            # These are example fields based on common OTPv2 responses and your example.
            # Adjust them to what your frontend needs.
            # start # Overall trip start time (epoch millis)
            # end   # Overall trip end time (epoch millis)
            legs {
              mode
              startTime # epoch milliseconds
              endTime   # epoch milliseconds
              duration  # seconds
              distance  # meters
              from {
                name
                lat
                lon
                # departure { scheduledTime estimated { time delay } } # Optional detailed times
              }
              to {
                name
                lat
                lon
                # arrival { scheduledTime estimated { time delay } } # Optional detailed times
              }
              legGeometry {
                points # Encoded polyline
              }
              route { # Present for transit legs
                # gtfsId
                shortName
                longName
              }
              # agency { name } # If needed and available
              # transitLeg # boolean, if needed
            }
          }
        }
      }
    }
    """

    # Prepare variables for the query
    variables = {
        "fromLat": from_lat, # Pass directly for the coordinate object
        "fromLon": from_lon,
        "toLat": to_lat,
        "toLon": to_lon,
        "dateTime": dateTime_str, # ISO 8601 with offset
    }
    
    try:
        response = requests.post(
            otp_graphql_url,
            json={"query": graphql_query, "variables": variables},
            headers={"Content-Type": "application/json"}
        )
        response.raise_for_status()
        # debug print
        # print(response.json()) 
        return response.json()
    except requests.exceptions.RequestException as e:
        return {"error": _label("route_unsupported_label")}
    except Exception as e:
        # The exception stays in the server log: its text can carry internals
        # (URLs, stack frames) that a page must not show.
        print(f"An error occurred during route planning: {e}")
        return {"error": _label("route_unsupported_label")}


def get_map_routes():
    return app.routes



if __name__ == "__main__":
    pass
