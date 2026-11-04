import copy
import math

import pytest
from hydra.utils import instantiate
from omegaconf import OmegaConf

from open_apps import config_dir
from open_apps.tasks.tasks import SavePlaceTask


@pytest.mark.parametrize("target,observed,tolerance,expected", [
    (49.09999, 49.10001, 1.0, True),   # about 2 metres across a former rounding cell
    (49.001, 49.099, 1.0, False),      # about 10.9 km within one former cell
    (-0.049, 0.049, 1.0, False),       # truncation previously merged both sides of zero
    (0.001, 0.095, None, False),       # about 10.4 km exceeds the default 10 km
    (49.0, 49.005, 1.0, True),         # about 0.56 km is inside the selected radius
    (49.0, 49.02, 1.0, False),         # about 2.22 km is outside it
])
def test_save_place_scores_the_unrounded_coordinate_distance(target, observed, tolerance, expected):
    initial = {"todo": [], "calendar": [], "messenger": [],
               "map": [{"name": "Existing place", "coords": [10.0, 10.0]}]}
    task = SavePlaceTask(goal="save a landmark", name="Landmark",
                         latitude=target, longitude=0.0, tolerance_km=tolerance)
    current = task.get_target_state(initial)
    current["map"][-1]["coords"][0] = observed
    original = copy.deepcopy(current)
    # Independent spherical-distance oracle; cases stay clear of the boundary.
    delta_lat = math.radians(observed - target)
    distance_km = 2 * 6371.0 * math.asin(abs(math.sin(delta_lat / 2)))
    assert (distance_km <= (10.0 if tolerance is None else tolerance)) is expected
    assert task.check_if_task_is_complete(initial, current) is expected
    assert current == original


def test_existing_eiffel_task_rejects_a_pin_over_six_kilometres_away():
    configs = OmegaConf.load(config_dir() / "tasks/original_tasks.yaml")
    task = instantiate(configs["save_eiffel_tower_to_my_favorite_places"])
    initial = {"todo": [], "calendar": [], "messenger": [],
               "map": [{"name": "Existing place", "coords": [10.0, 10.0]}]}
    current = task.get_target_state(initial)
    current["map"][-1]["coords"][0] = 48.801
    original = copy.deepcopy(current)
    distance_km = 2 * 6371.0 * math.asin(abs(math.sin(math.radians(task.latitude - 48.801) / 2)))
    assert distance_km > 6.0 > task.tolerance_km
    assert not task.check_if_task_is_complete(initial, current)
    assert current == original


@pytest.mark.parametrize("target_lon,observed_lon,expected", [
    (2.29999, 2.30001, True),
    (2.201, 2.299, False),
])
def test_landmark_longitude_precision_uses_the_same_radius(target_lon, observed_lon, expected):
    latitude = 48.8584
    initial = {"todo": [], "calendar": [], "messenger": [],
               "map": [{"name": "Existing place", "coords": [10.0, 10.0]}]}
    task = SavePlaceTask(goal="save a landmark", name="Landmark", latitude=latitude,
                         longitude=target_lon, tolerance_km=0.5)
    current = task.get_target_state(initial)
    current["map"][-1]["coords"][1] = observed_lon
    delta_lon = math.radians(observed_lon - target_lon)
    haversine = math.cos(math.radians(latitude)) ** 2 * math.sin(delta_lon / 2) ** 2
    distance_km = 2 * 6371.0 * math.asin(math.sqrt(haversine))
    assert (distance_km <= 0.5) is expected
    assert task.check_if_task_is_complete(initial, current) is expected
