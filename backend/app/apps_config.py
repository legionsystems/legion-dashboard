"""Configured LEGION apps that the control plane can manage.

This list is authoritative: an app must appear here to be addressable via the
apps router. The DB row holds runtime state (status, last_action, last_result)
but is rehydrated from this list on startup so removing an entry here removes
the app from the operations console.
"""
from dataclasses import dataclass
from typing import List


@dataclass(frozen=True)
class AppDefinition:
    app_id: str
    name: str
    repo: str
    compose_project: str
    compose_path: str


APPS_CONFIG: List[AppDefinition] = [
    AppDefinition(
        app_id="legion-dashboard",
        name="LEGION Dashboard",
        repo="/srv/repo/legion-dashboard",
        compose_project="legion-dashboard",
        compose_path="/srv/repo/legion-dashboard/docker-compose.yml",
    ),
    AppDefinition(
        app_id="lgn-hub",
        name="LEGION Hub",
        repo="/srv/repo/lgn-hub",
        compose_project="lgn-hub",
        compose_path="/srv/repo/lgn-hub/docker-compose.yml",
    ),
    AppDefinition(
        app_id="lgn-bar-assistant",
        name="Bar Assistant",
        repo="/srv/repo/lgn-bar-assistant",
        compose_project="lgn-bar-assistant",
        compose_path="/srv/repo/lgn-bar-assistant/docker-compose.yml",
    ),
    AppDefinition(
        app_id="lgn-business-tracker",
        name="Business Tracker",
        repo="/srv/repo/lgn-business-tracker",
        compose_project="lgn-business-tracker",
        compose_path="/srv/repo/lgn-business-tracker/docker-compose.yml",
    ),
    AppDefinition(
        app_id="lgn-home-inventory",
        name="Home Inventory",
        repo="/srv/repo/lgn-home-inventory",
        compose_project="lgn-home-inventory",
        compose_path="/srv/repo/lgn-home-inventory/docker-compose.yml",
    ),
    AppDefinition(
        app_id="lgn-investment-planner",
        name="Investment Planner",
        repo="/srv/repo/lgn-investment-planner",
        compose_project="lgn-investment-planner",
        compose_path="/srv/repo/lgn-investment-planner/docker-compose.yml",
    ),
    AppDefinition(
        app_id="lgn-pantry-assistant",
        name="Pantry Assistant",
        repo="/srv/repo/lgn-pantry-assistant",
        compose_project="lgn-pantry-assistant",
        compose_path="/srv/repo/lgn-pantry-assistant/docker-compose.yml",
    ),
    AppDefinition(
        app_id="lgn-study-buddy",
        name="Study Buddy",
        repo="/srv/repo/lgn-study-buddy",
        compose_project="lgn-study-buddy",
        compose_path="/srv/repo/lgn-study-buddy/docker-compose.yml",
    ),
    AppDefinition(
        app_id="lgn-travel-planner",
        name="Travel Planner",
        repo="/srv/repo/lgn-travel-planner",
        compose_project="lgn-travel-planner",
        compose_path="/srv/repo/lgn-travel-planner/docker-compose.yml",
    ),
    AppDefinition(
        app_id="lgn-wealth-tracker",
        name="Wealth Tracker",
        repo="/srv/repo/lgn-wealth-tracker",
        compose_project="lgn-wealth-tracker",
        compose_path="/srv/repo/lgn-wealth-tracker/docker-compose.yml",
    ),
    AppDefinition(
        app_id="lgn-workload-assistant",
        name="Workload Assistant",
        repo="/srv/repo/lgn-workload-assistant",
        compose_project="lgn-workload-assistant",
        compose_path="/srv/repo/lgn-workload-assistant/docker-compose.yml",
    ),
]


def app_definitions() -> List[AppDefinition]:
    return list(APPS_CONFIG)
