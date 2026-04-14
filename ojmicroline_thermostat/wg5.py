"""Implementation of OJMicrolineAPI for WG5-series thermostats."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Any

import async_timeout
from aiohttp import ClientSession
from yarl import URL

from .const import (
    REGULATION_COMFORT,
    REGULATION_FROST_PROTECTION,
    REGULATION_MANUAL,
    REGULATION_SCHEDULE,
    REGULATION_VACATION,
    WG5_MODE_HOLD,
    WG5_MODE_SCHEDULE,
)
from .exceptions import OJMicrolineAuthError, OJMicrolineError
from .models import Thermostat

if TYPE_CHECKING:
    from .ojmicroline import RequestFunc

WG5_SCOPES = (
    "ugw.zones ugw.users ugw.profile ugw.buildings ugw.schedules "
    "ugw.thermostats ugw.firmware ugw.linking openid role offline_access"
)


@dataclass
class WG5API:
    """Controls OJ Microline WG5-series thermostats."""

    request: RequestFunc
    client_id: str = "mobile_app_client"

    def __init__(
        self,
        username: str,
        password: str,
        host: str = "user-api.ojmicroline.com",
        identity_host: str = "identity.ojmicroline.com",
    ) -> None:
        """Create a new instance of the API object.

        Args:
        ----
            username: The username to log in with.
            password: The password for the username.
            host: The host name used for API requests.
            identity_host: The host name used for OAuth2 authentication.

        """
        self.username = username
        self.password = password
        self.host = host
        self.identity_host = identity_host
        self._access_token: str | None = None
        self._refresh_token: str | None = None
        self._token_expiry: datetime | None = None

    async def login(self) -> None:
        """Authenticate via OAuth2 Resource Owner Password Credentials grant.

        Raises
        ------
            OJMicrolineAuthError: Authentication failed.

        """
        if (
            self._access_token
            and self._token_expiry
            and self._token_expiry > datetime.now(tz=UTC)
        ):
            return

        async with ClientSession() as session:
            url = URL.build(
                scheme="https", host=self.identity_host, path="/connect/token"
            )
            async with async_timeout.timeout(30):
                response = await session.post(
                    url,
                    data={
                        "grant_type": "password",
                        "username": self.username,
                        "password": self.password,
                        "client_id": self.client_id,
                        "scope": WG5_SCOPES,
                    },
                    headers={
                        "Content-Type": "application/x-www-form-urlencoded",
                        "Accept": "application/json",
                    },
                    ssl=True,
                )

            if response.status != 200:
                msg = "Unable to authenticate, wrong username or password."
                raise OJMicrolineAuthError(msg)

            data = await response.json()

        self._access_token = data["access_token"]
        self._refresh_token = data.get("refresh_token")
        self._token_expiry = datetime.now(tz=UTC) + timedelta(
            seconds=data["expires_in"]
        )

    async def _auth_request(
        self,
        uri: str,
        *,
        method: str = "GET",
        body: dict[str, Any] | None = None,
    ) -> Any:
        """Make an authenticated request with Bearer token."""
        return await self.request(
            uri,
            method=method,
            body=body,
            headers={"Authorization": f"Bearer {self._access_token}"},
        )

    async def _is_away_active(self, building_id: str) -> bool:
        """Check if away mode is active for a building."""
        data = await self._auth_request(f"awaymode/{building_id}")
        return "data" in data

    async def _fetch_thermostat(
        self,
        thermostat_id: str,
        building_id: str,
        zone_name: str,
        *,
        away_active: bool,
    ) -> Thermostat:
        """Fetch and enrich a single thermostat."""
        control_data = await self._auth_request(
            f"thermostats/{thermostat_id}/control",
        )
        detail_data = await self._auth_request(
            f"thermostats/{thermostat_id}",
        )
        schedule_id = control_data["data"].get("scheduleData", {}).get("scheduleId")
        schedule_data = None
        if schedule_id:
            resp = await self._auth_request(f"schedules/{schedule_id}")
            schedule_data = resp.get("data")
        thermostat = Thermostat.from_wg5_json(
            control_data["data"],
            building_id=building_id,
            zone_name=zone_name,
            away_active=away_active,
        )
        readouts = detail_data.get("data", {}).get("thermostatReadouts", {})
        thermostat.software_version = readouts.get("softwareVersionNumber", "")
        floor = readouts.get("floorTemperature")
        if floor is not None:
            thermostat.temperature_floor = round(float(floor) * 100)
        room = readouts.get("roomTemperature")
        if room is not None:
            thermostat.temperature_room = round(float(room) * 100)
        thermostat.schedule = schedule_data
        return thermostat

    async def get_thermostats(self) -> list[Thermostat]:
        """Fetch all thermostats across all buildings."""
        buildings_data = await self._auth_request("buildings")
        thermostats: list[Thermostat] = []

        for building in buildings_data["data"]:
            building_id = building["id"]
            away_active = await self._is_away_active(building_id)
            tree_data = await self._auth_request(f"buildings/{building_id}/tree")
            for zone in tree_data["data"]["zones"]:
                zone_name = zone["name"]
                for thermostat_stub in zone["thermostats"]:
                    thermostat = await self._fetch_thermostat(
                        thermostat_stub["id"],
                        building_id,
                        zone_name,
                        away_active=away_active,
                    )
                    thermostats.append(thermostat)

        return thermostats

    async def get_energy_usage(self, resource: Thermostat) -> list[float]:
        """Fetch energy usage for a thermostat."""
        if not resource.building_id:
            return []

        now = datetime.now(tz=UTC)
        start = now.replace(hour=0, minute=0, second=0, microsecond=0)

        data = await self._auth_request(
            f"energy/usage/building/{resource.building_id}",
            method="POST",
            body={
                "ThermostatIds": [resource.serial_number],
                "Start": start.strftime("%Y-%m-%dT%H:%M:%SZ"),
                "End": now.strftime("%Y-%m-%dT%H:%M:%SZ"),
                "Size": 0,
            },
        )

        return [
            float(bucket.get("consumedWattHours", 0))
            for bucket in data["data"]["histogram"]
        ]

    async def set_regulation_mode(
        self,
        resource: Thermostat,
        regulation_mode: int,
        temperature: int | None,
        duration: int,
    ) -> bool:
        """Set the regulation mode on a thermostat."""
        thermostat_id = resource.serial_number

        if regulation_mode == REGULATION_VACATION:
            await self._auth_request(
                "awaymode",
                method="POST",
                body={
                    "BuildingId": resource.building_id,
                    "TemperatureLimitMax": 5.0,
                    "StartTime": datetime.now(tz=UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
                    "EndTime": (datetime.now(tz=UTC) + timedelta(days=365)).strftime(
                        "%Y-%m-%dT%H:%M:%SZ"
                    ),
                    "IsPlanned": False,
                    "ChangeTimestamp": "0001-01-01T00:00:00",
                },
            )
            return True

        if regulation_mode == REGULATION_FROST_PROTECTION:
            await self._auth_request(
                f"thermostats/{thermostat_id}/standby/True",
                method="PUT",
            )
            return True

        # Disable away mode if currently active.
        if resource.vacation_mode:
            await self._auth_request(
                f"awaymode/{resource.building_id}",
                method="DELETE",
            )

        # Take out of standby if needed.
        if resource.is_in_standby:
            await self._auth_request(
                f"thermostats/{thermostat_id}/standby/False",
                method="PUT",
            )

        if regulation_mode == REGULATION_SCHEDULE:
            body: dict[str, Any] = {
                "ModeAction": WG5_MODE_SCHEDULE,
                "ChangeTimestamp": "0001-01-01T00:00:00",
            }
        elif regulation_mode in (REGULATION_MANUAL, REGULATION_COMFORT):
            setpoint = (temperature or resource.set_point_temperature or 0) / 100
            body = {
                "ModeAction": WG5_MODE_HOLD,
                "Setpoint": setpoint,
                "ChangeTimestamp": "0001-01-01T00:00:00",
            }
            if regulation_mode == REGULATION_COMFORT:
                hold_end = datetime.now(tz=UTC) + timedelta(minutes=duration)
                body["HoldUntilEndTime"] = hold_end.strftime("%Y-%m-%dT%H:%M:%SZ")
                body["HoldIsPermanent"] = False
            else:
                body["HoldUntilEndTime"] = None
                body["HoldIsPermanent"] = True
        else:
            msg = f"Unsupported regulation mode for WG5: {regulation_mode}"
            raise OJMicrolineError(msg)

        await self._auth_request(
            f"thermostats/{thermostat_id}/mode",
            method="PUT",
            body=body,
        )
        return True
