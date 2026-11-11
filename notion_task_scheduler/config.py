"""Configuration utilities for the task scheduler."""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from datetime import time
from pathlib import Path
from typing import Any, Dict, Iterable, List, Mapping, MutableMapping, Optional

DEFAULT_CONFIG_PATH = Path("config.json")
DEFAULT_LOCAL_CONFIG_SUFFIX = ".local"


class ConfigurationError(RuntimeError):
    """Raised when the configuration file is missing required keys."""


@dataclass
class NotionConfig:
    token: str
    tasks_database_id: str
    tasks_data_source_id: Optional[str] = None
    projects_database_id: Optional[str] = None
    projects_data_source_id: Optional[str] = None
    properties: Mapping[str, str] = field(default_factory=dict)


@dataclass
class RoutineBlockConfig:
    id: str
    label: str
    default_start: time
    earliest: time
    latest: time
    duration_minutes: int
    enabled: bool = True
    priority: str = "Critical"
    description: Optional[str] = None
    summary: Optional[str] = None


@dataclass
class SchedulingConfig:
    work_hours_start: int
    work_hours_end: int
    default_task_duration_minutes: int
    buffer_minutes: int
    timezone: str
    look_ahead_days: int
    routine_blocks: Optional[List[RoutineBlockConfig]] = None
    priority_weights: Mapping[str, int] = field(default_factory=dict)
    urgency_scores: Mapping[str, int] = field(default_factory=dict)
    energy_windows: Mapping[str, list[tuple[int, int]]] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.routine_blocks is None:
            self.routine_blocks = default_routine_blocks()


@dataclass
class GoogleCalendarConfig:
    busy_calendar_ids: List[str]
    service_account_file: Optional[str] = None
    create_events: bool = False
    event_calendar_id: Optional[str] = None


@dataclass
class TaskDurationConfig:
    durations: Mapping[str, int]


@dataclass
class AppConfig:
    notion: NotionConfig
    scheduling: SchedulingConfig
    google_calendar: GoogleCalendarConfig
    task_durations: TaskDurationConfig

    @property
    def priority_weights(self) -> Mapping[str, int]:
        return self.scheduling.priority_weights


DEFAULT_PRIORITY_WEIGHTS: Mapping[str, int] = {
    "Critical": 100,
    "High": 75,
    "Medium": 50,
    "Low": 25,
    "Someday": 10,
}

DEFAULT_URGENCY_SCORES: Mapping[str, int] = {
    "overdue": 200,
    "due_today": 150,
    "due_tomorrow": 100,
    "due_this_week": 75,
    "future": 50,
    "no_due_date": 25,
}

DEFAULT_ENERGY_WINDOWS: Mapping[str, Iterable[Any]] = {
    "Deep Work": [(8, 13)],
    "Moderate Focus": [(10, 18)],
    "Light Work": [(13, 22)],
}

DEFAULT_ROUTINE_BLOCKS: tuple[dict[str, Any], ...] = (
    {
        "id": "lunch",
        "label": "Lunch Break",
        "summary": "Lunch Break (auto)",
        "default_start": "11:45",
        "earliest": "11:30",
        "latest": "14:00",
        "duration_minutes": 45,
        "priority": "Critical",
        "description": "Auto-scheduled lunch break",
        "enabled": True,
    },
    {
        "id": "dinner",
        "label": "Dinner Break",
        "summary": "Dinner Break (auto)",
        "default_start": "18:15",
        "earliest": "18:00",
        "latest": "20:45",
        "duration_minutes": 45,
        "priority": "Critical",
        "description": "Auto-scheduled dinner break",
        "enabled": True,
    },
)


REQUIRED_CONFIG_KEYS = {
    "notion": {"token", "tasks_database_id"},
    "scheduling": {
        "work_hours_start",
        "work_hours_end",
        "default_task_duration_minutes",
        "buffer_minutes",
        "timezone",
        "look_ahead_days",
    },
    "google_calendar": {"busy_calendar_ids"},
    "task_durations": set(),
}

DEFAULT_URGENCY_RULE_ORDER: tuple[str, ...] = (
    "overdue",
    "due_today",
    "due_tomorrow",
    "due_this_week",
    "future",
    "no_due_date",
)


def _ensure_keys(section: str, data: MutableMapping[str, Any]) -> None:
    missing = REQUIRED_CONFIG_KEYS.get(section, set()) - data.keys()
    if missing:
        raise ConfigurationError(
            f"Missing required config values in '{section}': {', '.join(sorted(missing))}"
        )


def _merge_nested_dicts(base: MutableMapping[str, Any], override: Mapping[str, Any]) -> MutableMapping[str, Any]:
    for key, value in override.items():
        if (
            key in base
            and isinstance(base[key], MutableMapping)
            and isinstance(value, Mapping)
        ):
            _merge_nested_dicts(base[key], value)
        else:
            base[key] = value
    return base


def _local_overlay_path(config_path: Path) -> Path:
    return config_path.with_name(
        f"{config_path.stem}{DEFAULT_LOCAL_CONFIG_SUFFIX}{config_path.suffix}"
    )


def _set_nested(raw: MutableMapping[str, Any], path: tuple[str, ...], value: Any) -> None:
    current: MutableMapping[str, Any] = raw
    for part in path[:-1]:
        next_value = current.get(part)
        if not isinstance(next_value, MutableMapping):
            next_value = {}
            current[part] = next_value
        current = next_value
    current[path[-1]] = value


def _split_env_list(value: str) -> list[str]:
    items = [item.strip() for item in value.replace("\r", "\n").replace(",", "\n").split("\n")]
    return [item for item in items if item]


def _apply_env_overrides(raw: MutableMapping[str, Any]) -> None:
    env_map: tuple[tuple[tuple[str, ...], str, Any], ...] = (
        (("notion", "token"), "NOTION_TOKEN", str),
        (("notion", "tasks_database_id"), "NOTION_TASKS_DATABASE_ID", str),
        (("notion", "tasks_data_source_id"), "NOTION_TASKS_DATA_SOURCE_ID", str),
        (("notion", "projects_database_id"), "NOTION_PROJECTS_DATABASE_ID", str),
        (("notion", "projects_data_source_id"), "NOTION_PROJECTS_DATA_SOURCE_ID", str),
        (("scheduling", "timezone"), "SCHEDULING_TIMEZONE", str),
        (("scheduling", "look_ahead_days"), "SCHEDULING_LOOK_AHEAD_DAYS", int),
        (("google_calendar", "service_account_file"), "GOOGLE_SERVICE_ACCOUNT_FILE", str),
        (("google_calendar", "event_calendar_id"), "GOOGLE_EVENT_CALENDAR_ID", str),
    )
    for path, env_name, caster in env_map:
        raw_value = os.environ.get(env_name)
        if raw_value is None or raw_value == "":
            continue
        _set_nested(raw, path, caster(raw_value))

    busy_calendar_ids = os.environ.get("GOOGLE_BUSY_CALENDAR_IDS")
    if busy_calendar_ids:
        _set_nested(raw, ("google_calendar", "busy_calendar_ids"), _split_env_list(busy_calendar_ids))


def _load_raw_config(config_path: Path) -> Dict[str, Any]:
    if not config_path.exists():
        raise ConfigurationError(f"Configuration file not found: {config_path}")

    with config_path.open("r", encoding="utf-8") as fh:
        raw: Dict[str, Any] = json.load(fh)

    local_overlay = _local_overlay_path(config_path)
    if local_overlay.exists():
        with local_overlay.open("r", encoding="utf-8") as fh:
            overlay = json.load(fh)
        _merge_nested_dicts(raw, overlay)

    _apply_env_overrides(raw)
    return raw


def _normalize_energy_windows(
    raw_windows: Mapping[str, Iterable[Any]]
) -> Dict[str, list[tuple[int, int]]]:
    """Convert energy window definitions into minute-based intervals."""

    normalized: Dict[str, list[tuple[int, int]]] = {}
    for level, windows in raw_windows.items():
        intervals: list[tuple[int, int]] = []
        for entry in windows:
            if isinstance(entry, (list, tuple)):
                if len(entry) != 2:
                    continue
                start = float(entry[0])
                end = float(entry[1])
                intervals.extend(_expand_window(start, end))
            else:
                hour = float(entry)
                intervals.extend(_expand_window(hour, hour + 1))
        if intervals:
            normalized[level] = intervals
    return normalized


def _expand_window(start_hour: float, end_hour: float) -> list[tuple[int, int]]:
    """Translate hour-based windows into minute pairs, handling wrap-around."""

    start_minute = int(max(0, min(24 * 60, round(start_hour * 60))))
    end_minute = int(max(0, min(24 * 60, round(end_hour * 60))))
    if end_minute <= start_minute:
        if start_minute == end_minute:
            return []
        return [(start_minute, 24 * 60), (0, end_minute)]
    return [(start_minute, end_minute)]


def _parse_clock(raw_value: Any, *, field_name: str) -> time:
    if isinstance(raw_value, time):
        return raw_value
    if not isinstance(raw_value, str):
        raise ConfigurationError(f"Invalid time for {field_name}: {raw_value!r}")
    value = raw_value.strip()
    parts = value.split(":")
    if len(parts) != 2:
        raise ConfigurationError(f"Invalid time for {field_name}: {raw_value!r}")
    try:
        hour = int(parts[0])
        minute = int(parts[1])
    except ValueError as exc:
        raise ConfigurationError(f"Invalid time for {field_name}: {raw_value!r}") from exc
    if not (0 <= hour <= 23 and 0 <= minute <= 59):
        raise ConfigurationError(f"Invalid time for {field_name}: {raw_value!r}")
    return time(hour=hour, minute=minute)


def default_routine_blocks() -> List[RoutineBlockConfig]:
    return _normalize_routine_blocks(DEFAULT_ROUTINE_BLOCKS)


def _normalize_routine_blocks(raw_blocks: Iterable[Mapping[str, Any]]) -> List[RoutineBlockConfig]:
    blocks: List[RoutineBlockConfig] = []
    for index, raw_block in enumerate(raw_blocks):
        block_id = str(raw_block.get("id") or f"routine-{index + 1}").strip()
        label = str(raw_block.get("label") or block_id.replace("-", " ").title()).strip()
        if not block_id:
            raise ConfigurationError(f"Routine block at index {index} is missing an id")
        if not label:
            raise ConfigurationError(f"Routine block '{block_id}' is missing a label")
        duration_minutes = int(raw_block.get("duration_minutes", 45))
        if duration_minutes <= 0:
            raise ConfigurationError(
                f"Routine block '{block_id}' must use a positive duration_minutes"
            )
        earliest = _parse_clock(
            raw_block.get("earliest", "09:00"),
            field_name=f"routine_blocks[{index}].earliest",
        )
        latest = _parse_clock(
            raw_block.get("latest", "10:00"),
            field_name=f"routine_blocks[{index}].latest",
        )
        default_start = _parse_clock(
            raw_block.get("default_start", "09:00"),
            field_name=f"routine_blocks[{index}].default_start",
        )
        blocks.append(
            RoutineBlockConfig(
                id=block_id,
                label=label,
                summary=str(raw_block.get("summary") or "").strip() or None,
                default_start=default_start,
                earliest=earliest,
                latest=latest,
                duration_minutes=duration_minutes,
                enabled=bool(raw_block.get("enabled", True)),
                priority=str(raw_block.get("priority") or "Critical"),
                description=str(raw_block.get("description") or "").strip() or None,
            )
        )
    return blocks


def load_config(path: Path | str = DEFAULT_CONFIG_PATH) -> AppConfig:
    """Load the scheduler configuration from *path*."""

    config_path = Path(path)
    raw = _load_raw_config(config_path)

    for section, data in raw.items():
        if isinstance(data, dict):
            _ensure_keys(section, data)

    notion_raw: Dict[str, Any] = raw["notion"]
    scheduling_raw: Dict[str, Any] = raw["scheduling"]
    calendar_raw: Dict[str, Any] = raw["google_calendar"]
    durations_raw: Dict[str, Any] = raw.get("task_durations", {})

    properties = notion_raw.get("properties", {})
    if not properties:
        # Provide sensible defaults that match the roadmap naming.
        properties = {
            "status": "Status",
            "auto_schedule": "Auto-Schedule",
            "scheduled": "Scheduled",
            "energy_level": "Energy Level",
            "duration_estimate": "Duration Estimate",
            "due": "Due",
            "range": "Range",
            "last_scheduled": "Last Scheduled",
            "scheduled_block_id": "Scheduled Block ID",
            "priority": "Priority",
            "recurrence_template": "Recurrence Template",
            "instance_of": "Instance Of",
            "next_due": "Next Due",
            "recur_interval": "Recur Interval",
            "recur_unit": "Recur Unit",
            "blocking": "Blocking",
            "blocked_by": "Blocked by",
        }
    notion_cfg = NotionConfig(
        token=notion_raw["token"],
        tasks_database_id=notion_raw["tasks_database_id"],
        tasks_data_source_id=notion_raw.get("tasks_data_source_id"),
        projects_database_id=notion_raw.get("projects_database_id"),
        projects_data_source_id=notion_raw.get("projects_data_source_id"),
        properties=properties,
    )

    priority_weights = scheduling_raw.get("priority_weights") or DEFAULT_PRIORITY_WEIGHTS
    urgency_scores = scheduling_raw.get("urgency_scores") or DEFAULT_URGENCY_SCORES
    energy_windows = _normalize_energy_windows(
        scheduling_raw.get("energy_windows") or DEFAULT_ENERGY_WINDOWS
    )
    routine_blocks = _normalize_routine_blocks(
        scheduling_raw.get("routine_blocks") or DEFAULT_ROUTINE_BLOCKS
    )

    scheduling_cfg = SchedulingConfig(
        work_hours_start=scheduling_raw["work_hours_start"],
        work_hours_end=scheduling_raw["work_hours_end"],
        default_task_duration_minutes=scheduling_raw["default_task_duration_minutes"],
        buffer_minutes=scheduling_raw["buffer_minutes"],
        timezone=scheduling_raw["timezone"],
        look_ahead_days=scheduling_raw["look_ahead_days"],
        routine_blocks=routine_blocks,
        priority_weights=priority_weights,
        urgency_scores=urgency_scores,
        energy_windows=energy_windows,
    )

    calendar_cfg = GoogleCalendarConfig(
        busy_calendar_ids=calendar_raw["busy_calendar_ids"],
        service_account_file=calendar_raw.get("service_account_file"),
        create_events=calendar_raw.get("create_events", False),
        event_calendar_id=calendar_raw.get("event_calendar_id"),
    )

    if not durations_raw:
        raise ConfigurationError("task_durations cannot be empty")

    task_duration_cfg = TaskDurationConfig(durations=durations_raw)

    return AppConfig(
        notion=notion_cfg,
        scheduling=scheduling_cfg,
        google_calendar=calendar_cfg,
        task_durations=task_duration_cfg,
    )
