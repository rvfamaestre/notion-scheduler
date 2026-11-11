"""Google Calendar integration helpers."""

from __future__ import annotations

import json
import logging
from datetime import datetime
from typing import Dict, Iterable, List, Optional

try:  # pragma: no cover - optional dependency in tests
    from googleapiclient.discovery import build
    from googleapiclient.errors import HttpError
    from google.auth.exceptions import GoogleAuthError
    from google.oauth2 import service_account
except Exception:  # pragma: no cover - keep import optional for unit tests
    build = None  # type: ignore
    HttpError = Exception  # type: ignore
    GoogleAuthError = Exception  # type: ignore
    service_account = None  # type: ignore


from .utils import parse_iso_datetime

logger = logging.getLogger(__name__)

# Reminder: share any target calendars with the service account email.
SCOPES = [
    "https://www.googleapis.com/auth/calendar.readonly",
    "https://www.googleapis.com/auth/calendar.events",
]


class CalendarManager:
    """Wrapper around the Google Calendar API."""

    def __init__(self, config) -> None:
        self.busy_calendar_ids = list(config.busy_calendar_ids)
        self.service_account_file = config.service_account_file
        self.create_events = config.create_events
        self.event_calendar_id = config.event_calendar_id or (
            self.busy_calendar_ids[0] if self.busy_calendar_ids else "primary"
        )
        self.service = None
        if build is None:
            logger.warning("google-api-python-client not installed; calendar features disabled")
        else:
            self._authenticate()

    def _authenticate(self) -> None:
        if build is None or service_account is None:
            return

        if not self.service_account_file:
            raise RuntimeError(
                "Google Calendar service_account_file is required for authentication"
            )

        try:
            creds = service_account.Credentials.from_service_account_file(
                self.service_account_file,
                scopes=SCOPES,
            )
        except FileNotFoundError as exc:
            raise RuntimeError(
                f"Google Calendar service account file not found: {self.service_account_file}"
            ) from exc
        except GoogleAuthError as exc:
            raise RuntimeError(
                f"Failed to load Google Calendar service account credentials: {exc}"
            ) from exc

        self.service = build("calendar", "v3", credentials=creds)

    def _handle_http_error(self, action: str, exc: HttpError) -> None:  # pragma: no cover - network path
        """Log permission errors with additional guidance for service accounts."""

        permission_denied = getattr(getattr(exc, "resp", None), "status", None) in {401, 403}
        if not permission_denied:
            try:
                error_payload = json.loads(getattr(exc, "content", b"").decode("utf-8"))
            except (AttributeError, ValueError, UnicodeDecodeError):
                error_payload = {}

            status = (
                error_payload.get("error", {}).get("status")
                or error_payload.get("error", {}).get("code")
            )
            if status in {"PERMISSION_DENIED", 403}:
                permission_denied = True

        if permission_denied:
            logger.error(
                "%s: permission denied. Share the calendar with the service account and verify scopes.",
                action,
            )
        else:
            logger.error("%s: %s", action, exc)

    # ------------------------ Busy blocks ------------------------
    def get_busy_blocks(self, start: datetime, end: datetime) -> List[Dict[str, datetime]]:
        """Return busy blocks across the configured calendars."""

        if self.service is None:
            logger.debug("Calendar service unavailable; returning empty busy list")
            return []

        body = {
            "timeMin": start.isoformat(),
            "timeMax": end.isoformat(),
            "items": [{"id": calendar_id} for calendar_id in self.busy_calendar_ids],
        }

        try:
            response = self.service.freebusy().query(body=body).execute()
        except HttpError as exc:  # pragma: no cover - network error path
            self._handle_http_error("Failed to fetch busy blocks", exc)
            return []

        busy_blocks: List[Dict[str, datetime]] = []
        for calendar_id, calendar_data in response.get("calendars", {}).items():
            for block in calendar_data.get("busy", []):
                start_dt = parse_iso_datetime(block["start"])
                end_dt = parse_iso_datetime(block["end"])
                if not start_dt or not end_dt:
                    continue
                start_dt = start_dt.astimezone(start.tzinfo)
                end_dt = end_dt.astimezone(end.tzinfo)
                busy_blocks.append({
                    "calendar_id": calendar_id,
                    "start": start_dt,
                    "end": end_dt,
                })
        busy_blocks.sort(key=lambda block: block["start"])
        return busy_blocks

    # ------------------------ Event creation ------------------------
    def create_event(
        self,
        title: str,
        start: datetime,
        end: datetime,
        description: Optional[str] = None,
        attendees: Optional[Iterable[str]] = None,
    ) -> Optional[str]:
        """Create an event for a scheduled task if enabled."""

        if not self.create_events:
            return None
        if self.service is None:
            logger.warning("Calendar service unavailable; skipping event creation")
            return None

        event_body = {
            "summary": title,
            "description": description or "Auto-scheduled task",
            "start": {"dateTime": start.isoformat()},
            "end": {"dateTime": end.isoformat()},
        }
        if attendees:
            event_body["attendees"] = [{"email": email} for email in attendees]

        try:
            event = (
                self.service.events()
                .insert(calendarId=self.event_calendar_id, body=event_body)
                .execute()
            )
        except HttpError as exc:  # pragma: no cover - network path
            self._handle_http_error("Failed to create calendar event", exc)
            return None

        return event.get("id")

    def find_events(
        self,
        title: str,
        start: datetime,
        end: datetime,
    ) -> List[Dict[str, datetime]]:
        """Return matching events between *start* and *end*."""

        if not self.create_events or self.service is None:
            return []

        events: List[Dict[str, datetime]] = []
        time_min = start.isoformat()
        time_max = end.isoformat()
        page_token: Optional[str] = None
        while True:
            try:
                response = (
                    self.service.events()
                    .list(
                        calendarId=self.event_calendar_id,
                        timeMin=time_min,
                        timeMax=time_max,
                        q=title,
                        singleEvents=True,
                        orderBy="startTime",
                        pageToken=page_token,
                    )
                    .execute()
                )
            except HttpError as exc:  # pragma: no cover - network path
                self._handle_http_error("Failed to query events", exc)
                return []

            events.extend(response.get("items", []))
            page_token = response.get("nextPageToken")
            if not page_token:
                break
        return events

    def delete_event(self, event_id: str) -> None:
        """Delete the event with id *event_id* if possible."""

        if not self.create_events or self.service is None:
            return
        try:
            self.service.events().delete(
                calendarId=self.event_calendar_id,
                eventId=event_id,
            ).execute()
        except HttpError as exc:  # pragma: no cover - network path
            self._handle_http_error(f"Failed to delete calendar event {event_id}", exc)

    def ensure_event(
        self,
        title: str,
        start: datetime,
        end: datetime,
        description: Optional[str] = None,
    ) -> Optional[str]:
        """Return an event id, creating a new event if necessary."""

        if not self.create_events or self.service is None:
            return None

        for event in self.find_events(title, start, end):
            event_start = parse_iso_datetime(event.get("start", {}).get("dateTime"))
            event_end = parse_iso_datetime(event.get("end", {}).get("dateTime"))
            if event_start and event_end:
                if abs((event_start - start).total_seconds()) < 60 and abs(
                    (event_end - end).total_seconds()
                ) < 60:
                    return event.get("id")

        return self.create_event(title, start, end, description=description)


def merge_busy_blocks(blocks: List[Dict[str, datetime]]) -> List[Dict[str, datetime]]:
    """Merge overlapping busy blocks to simplify availability calculation."""

    if not blocks:
        return []

    sorted_blocks = sorted(blocks, key=lambda block: block["start"])
    merged: List[Dict[str, datetime]] = [sorted_blocks[0].copy()]

    for block in sorted_blocks[1:]:
        current = merged[-1]
        if block["start"] <= current["end"]:
            current["end"] = max(current["end"], block["end"])
        else:
            merged.append(block.copy())

    return merged
