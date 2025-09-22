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
            print(f"- map planning is not supported yet, turning off...")
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

@app.get("/maps", response_class=HTMLResponse)
async def map_page(request: Request):
    return templates.TemplateResponse(
        "map.html",
        {
            "request": request,
            "enable_layer_control": app.config.maps.enable_layer_control,
            "default_layer": app.config.maps.default_layer,
            "popup_display_rule": app.config.maps.popup_display_rule,
            "title": app.config.maps.title,
            "init_location": app.config.maps.init_location,
            "zoom": app.config.maps.zoom,
            "granularity": app.config.maps.granularity,
            "font_family": app.config.maps.font_family,
            "base_font_size": app.config.maps.base_font_size,
            "search_button_color": app.config.maps.search_button_color,
            "delete_button_color": app.config.maps.delete_button_color,
            "return_button_color": app.config.maps.return_button_color,
            "calculate_button_color": app.config.maps.calculate_button_color,
            "delete_button_hover_color": getattr(app.config.maps, "delete_button_hover_color", "#ffecec"),
            "search_button_hover_color": getattr(app.config.maps, "search_button_hover_color", "#2980b9"),
            "calculate_button_hover_color": getattr(app.config.maps, "calculate_button_hover_color", "#2980b9"),
            "sidebar_background_color": getattr(app.config.maps, "sidebar_background_color", "#f8f9fa"),
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
            return {"error": "Location not found"}
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
        print(f"Error querying OTP GraphQL API: {e}")
        if e.response is not None:
            print(f"Response status code: {e.response.status_code}")
            print(f"Response content: {e.response.text}")
        return {"error": f"Route planning failed: {e}", "details": str(e.response.text if e.response is not None else "No response text")}
    except Exception as e:
        print(f"An unexpected error occurred: {e}")
        return {"error": f"An unexpected error occurred: {e}"}


def get_map_routes():
    return app.routes



if __name__ == "__main__":
    pass
