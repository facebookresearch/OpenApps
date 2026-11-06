# Maps

The map app ("OpenMaps" on the start page) is a Leaflet map with a side panel:
place search (OpenStreetMap's Nominatim), the address and coordinates of
whatever you click, and a list of saved places. Saving and removing saved
places is the state tasks score. It is on by default:

```bash
uv run launch.py            # then open http://localhost:<port>/maps
```

**The map needs network.** Leaflet, Font Awesome and the marker plugin load
from unpkg / cdnjs, the tiles from the tile servers, and search and
reverse-geocoding call `nominatim.openstreetmap.org` from the browser. On a
node without egress the panel and the saved-places list still render (the
list is read from `/maps/landmarks`), but the map area stays blank and search
returns nothing. Route planning (`allow_planning`) is a placeholder: the
button calls an OpenTripPlanner server that is not started.

## Varying the map

It composes the same way as every other app:

```bash
uv run launch.py apps/theme=dark                       # any file in config/apps/theme/
uv run launch.py apps/theme=challenging_font
uv run launch.py apps.maps.theme=solarized             # pin this app only (beats apps/theme)
uv run launch.py apps/maps/layout=sidebar_left         # default | sidebar_left | bottom_sheet
uv run launch.py apps/maps/content=german              # default | german | mandarin | long_descriptions | adversarial_descriptions | misleading_descriptions
uv run launch.py device=phone                          # the shared device axis
```

| Override | Default | Effect |
| --- | --- | --- |
| `apps/theme=<name>` | `null` (the default theme) | Global theme: panel, controls, popups, and the tiles through the theme's `tone` (below) |
| `apps.maps.theme` | `null` | Per-app pin |
| `apps/maps/layout` | `default` | See [Layouts](#layouts) |
| `apps/maps/content` | `default` | Text and saved places, see [Content packs](#content-packs) |
| `apps.maps.enable_layer_control` | `True` | Show Leaflet's basemap switcher |
| `apps.maps.default_layer` | `OpenStreetMap` | Basemap on load; must name a layer defined in `templates/map.html` |
| `apps.maps.filter_by_tone` | `dark: invert(1) hue-rotate(180deg) brightness(0.95) contrast(0.9)`, `mono: grayscale(1)` | CSS `filter` drawn over the street-map tiles per theme tone |
| `apps.maps.layer_by_tone` | `{}` | Optional basemap per tone; wins over `default_layer` |
| `apps.maps.popup_display_rule` | `automatic` | `automatic` styles a saved marker from its name; `diy` asks with the Customize Marker dialog |
| `apps.maps.granularity` | `coarse` | `coarse` offers 11 marker icons, `fine` 22 |
| `apps.maps.zoom` | `12` | Initial zoom |
| `apps.maps.allow_planning` | `False` | Show the Route Planning section (placeholder backend) |
| `apps.maps.init_location` | NYC (set by the pack) | Initial map centre |
| `apps.maps.database_path` | `${databases_dir}/maps.db` | Where saved places live |

### Themes and the tiles

Tiles are raster images, so no design token can recolour them. Each theme
declares a `tone` (`light`, `dark` or `mono`), and the map applies
`filter_by_tone[tone]` as a CSS filter to the street-map tile layers only:

- `dark` inverts OpenStreetMap and rotates the hue back, so the ground goes
  dark while water stays blue and parks green, and every label of the light
  map is still there;
- `mono` drains the colour with `grayscale(1)`;
- light themes get no filter.

The filter sits on `.leaflet-tile-pane .leaflet-layer`, so markers, popups and
the zoom / layer / attribution controls (other panes) are never inverted; they
are themed with tokens like the panel. The ESRI imagery and grey canvas layers
in the switcher opt out (`className: 'no-tone-filter'`): an inverted satellite
photo is noise, not a dark theme.

To use a separate basemap for a tone instead, set `layer_by_tone`:

```bash
uv run launch.py apps/theme=dark +apps.maps.layer_by_tone.dark='ESRI Dark Gray'
```

(ESRI's grey canvases carry no place labels and stop at zoom 16, which is why
they are not the default.)

### Layouts

Ids, labels and routes are the same in every layout, and DOM order follows
visual order, so the accessibility tree reads the way the page looks.

| Layout | Looks like |
| --- | --- |
| `default` | Map on the left, panel on the right: Return to List of Apps, title, search, Current Location Info, Saved Locations. The basemap list is expanded at the top right (a UI question asks about its options) |
| `sidebar_left` | Google Maps desktop: the panel on the left with the search field pinned at its top; the basemap switcher folds into a layers button bottom-left and zoom moves bottom-right |
| `bottom_sheet` | A mobile maps sheet: the map fills the page and the panel rides up over its bottom edge with a grab handle; saved places become a row of chips |

### Content packs

| Pack | Changes |
| --- | --- |
| `default` | English UI; five NYC landmarks (Statue of Liberty, Central Park, Brooklyn Bridge, Times Square, Empire State Building), centred on NYC |
| `german` | German UI, `<html lang="de">`; appends 100 places in and around Germany and centres there |
| `mandarin` | Mandarin UI, `<html lang="zh-Hans">`; appends ten Chinese cities and centres on Beijing |
| `long_descriptions` | Appends 1000 places |
| `misleading_descriptions` | Appends ten NYC places and centres on Delhi |
| `adversarial_descriptions` | Appends a place named with the adversarial message (`apps.adversarial_message`) |

The UI strings are flat `*_label` keys (`saved_locations_label`,
`search_placeholder_label`, ...; the English defaults and the full list are
`LABELS` in `src/open_apps/apps/map_app/main.py`), plus
`marker_icon_labels` / `marker_color_labels` for the captions in the marker
dialog. The shared strings (Return to List of Apps, Search, Cancel, Confirm)
come from `open_apps.i18n`. One dict feeds both the markup and the page
script, so result messages, aria labels and route details are translated too.

Place names are data, not UI: the five default landmarks keep their English
names in every pack, because tasks such as `remove_times_square_from_favorites`
address them by name. Search results are named by Nominatim.

## Tasks

| Task | Goal | Scored on |
| --- | --- | --- |
| `save_paris_to_my_favorite_places`, `save_bockelwitz_to_my_favorite_places`, `save_eiffel_tower_to_my_favorite_places` (and the variants in `config/tasks/user_goal_variations.yaml`) | Search a place and save it | A new saved place whose coordinates are within `tolerance_km` (10 km by default) of the target (`SavePlaceTask`) |
| `remove_times_square_from_favorites` | Delete a saved place with its × button | The place gone, nothing else changed (`RemoveLandmarkTask`) |
| `navigate_from_map_to_todo`, `navigate_from_map_to_calendar`, `navigate_from_todo_to_map` | Leave through Return to List of Apps and open another app | The page URL (`NavigateToAppTask`) |

The save tasks need Nominatim, so they are unwinnable on a node without
egress. Run one with the do-nothing agent to check the plumbing:

```bash
uv run launch_agent.py agent=dummy task_name=remove_times_square_from_favorites
uv run launch_agent.py agent=dummy task_name=remove_times_square_from_favorites apps/theme=dark apps/maps/content=mandarin
```

## Inspecting the data

The scoring surface is `/maps/landmarks`, one row per saved place:

```bash
curl -s http://localhost:<port>/maps/landmarks | python -m json.tool | head -20
```

```json
[{"name": "Statue of Liberty", "coords": [40.6892, -74.0445],
  "markerStyle": {"icon": "map-marker", "color": "blue"}}, ...]
```

`open_apps.state.get_current_state(url)["map"]` returns the same list. The
rows live in the `landmark` table of the SQLite file at
`apps.maps.database_path`, re-seeded from the content pack on every launch and
reset; see [Inspecting databases](../inspecting_databases.md).
