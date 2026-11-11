from __future__ import annotations

from datetime import datetime, timedelta, time
from typing import Dict, List
import sys
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

sys.path.append(str(Path(__file__).resolve().parents[1]))

from notion_task_scheduler.config import (
    AppConfig,
    GoogleCalendarConfig,
    NotionConfig,
    SchedulingConfig,
    TaskDurationConfig,
    DEFAULT_ENERGY_WINDOWS,
    DEFAULT_PRIORITY_WEIGHTS,
    DEFAULT_URGENCY_SCORES,
    _normalize_energy_windows,
)
from notion_task_scheduler.models import Task
from notion_task_scheduler.scheduler import TaskScheduler


class DummyNotion:
    def __init__(self, tasks, scheduled_blocks=None, extra_tasks=None):
        self.tasks = tasks
        self.updated = {}
        self.scheduled_blocks = scheduled_blocks or []
        self.unscheduled = []
        self._task_store = {task.id: task for task in tasks}
        if extra_tasks:
            for task in extra_tasks:
                self._task_store[task.id] = task

    def get_tasks_to_schedule(self, look_ahead_days: int):
        return list(self.tasks)

    def get_recurring_templates(self):
        return []

    def get_incomplete_tasks(self):
        return []

    def mark_unscheduled(self, task_id: str):
        self.unscheduled.append(task_id)
        self.scheduled_blocks = [
            block for block in self.scheduled_blocks if block.get("id") != task_id
        ]

    def update_task_schedule(self, task_id, start, end, event_id=None):
        self.updated[task_id] = (start, end, event_id)
        task = self._task_store.get(task_id)
        if task:
            task.range_start = start
            task.range_end = end
            task.scheduled = True

    def create_recurring_instance(self, template, due):
        return None

    def instance_exists(self, template_id, due):
        return False

    def update_template_next_due(self, template_id, next_due):
        pass

    def get_scheduled_blocks(self, start, end):
        blocks = []
        for block in self.scheduled_blocks:
            block_start = block["start"]
            block_end = block["end"]
            if block_end <= start or block_start >= end:
                continue
            blocks.append(
                {
                    "id": block.get("id", ""),
                    "start": block_start,
                    "end": block_end,
                }
            )
        return blocks

    def get_tasks_by_ids(self, task_ids):
        result = {}
        for task_id in task_ids:
            task = self._task_store.get(task_id)
            if task:
                result[task_id] = task
        return result


class DummyCalendar:
    def __init__(self, busy):
        self.busy = busy
        self.created_events: List[Dict[str, datetime]] = []
        self.deleted_events: List[str] = []
        self.create_events = False

    def get_busy_blocks(self, start, end):
        return list(self.busy)

    def create_event(self, title, start, end, description=None, attendees=None):
        if not self.create_events:
            return None
        event_id = f"event-{len(self.created_events)+1}"
        self.created_events.append(
            {
                "id": event_id,
                "summary": title,
                "start": start,
                "end": end,
                "description": description,
            }
        )
        return event_id

    def find_events(self, summary, start, end):
        if not self.create_events:
            return []
        matches = []
        for event in self.created_events:
            if event["summary"] != summary:
                continue
            if event["start"] == start and event["end"] == end:
                matches.append(
                    {
                        "id": event["id"],
                        "summary": event["summary"],
                        "start": {"dateTime": event["start"].isoformat()},
                        "end": {"dateTime": event["end"].isoformat()},
                    }
                )
        return matches

    def delete_event(self, event_id: str) -> None:
        if not self.create_events:
            return
        self.deleted_events.append(event_id)
        self.created_events = [event for event in self.created_events if event["id"] != event_id]

    def ensure_event(self, title, start, end, description=None):
        if not self.create_events:
            return None
        matches = self.find_events(title, start, end)
        if matches:
            return matches[0]["id"]
        return self.create_event(title, start, end, description=description)


@pytest.fixture
def config():
    return AppConfig(
        notion=NotionConfig(token="fake", tasks_database_id="db"),
        scheduling=SchedulingConfig(
            work_hours_start=8,
            work_hours_end=18,
            default_task_duration_minutes=60,
            buffer_minutes=15,
            timezone="Europe/Paris",
            look_ahead_days=3,
            priority_weights=DEFAULT_PRIORITY_WEIGHTS,
            urgency_scores=DEFAULT_URGENCY_SCORES,
            energy_windows=_normalize_energy_windows(DEFAULT_ENERGY_WINDOWS),
        ),
        google_calendar=GoogleCalendarConfig(busy_calendar_ids=["primary"]),
        task_durations=TaskDurationConfig(durations={"S": 30, "M": 60, "L": 120}),
    )


def make_task(
    task_id: str,
    name: str,
    due_offset_hours: int,
    tz: ZoneInfo,
    *,
    energy_level: str = "Deep Work",
    range_start: datetime | None = None,
    range_end: datetime | None = None,
    blocked_by: List[str] | None = None,
) -> Task:
    due = datetime.now(tz) + timedelta(hours=due_offset_hours)
    blocked_by_ids = list(blocked_by) if blocked_by else []
    return Task(
        id=task_id,
        name=name,
        due=due,
        duration_label="M",
        duration_minutes=60,
        priority="High",
        energy_level=energy_level,
        auto_schedule=True,
        scheduled=False,
        status="Not Started",
        range_start=range_start,
        range_end=range_end,
        recurrence_template=False,
        instance_of=None,
        blocking_ids=[],
        blocked_by_ids=blocked_by_ids,
    )


def test_scheduler_allocates_task_into_first_available_slot(config):
    tz = ZoneInfo(config.scheduling.timezone)
    base_now = datetime(2024, 1, 1, 9, 0, tzinfo=tz)
    task = make_task("1", "Write report", 24, tz)
    notion = DummyNotion([task])
    busy_blocks = [
        {"start": datetime(2024, 1, 1, 8, 0, tzinfo=tz), "end": datetime(2024, 1, 1, 10, 0, tzinfo=tz)}
    ]
    calendar = DummyCalendar(busy_blocks)

    scheduler = TaskScheduler(notion, calendar, config, now_fn=lambda: base_now)
    scheduled = scheduler.run_daily_schedule()

    assert scheduled == 1
    start, end, event_id = notion.updated["1"]
    assert start == datetime(2024, 1, 1, 10, 15, tzinfo=tz)
    assert end == datetime(2024, 1, 1, 11, 15, tzinfo=tz)
    assert event_id is None


def test_scheduler_respects_buffer_between_slots(config):
    tz = ZoneInfo(config.scheduling.timezone)
    base_now = datetime(2024, 1, 1, 8, 0, tzinfo=tz)
    task1 = make_task("1", "Task A", 24, tz)
    task2 = make_task("2", "Task B", 24, tz)
    notion = DummyNotion([task1, task2])
    calendar = DummyCalendar([])

    scheduler = TaskScheduler(notion, calendar, config, now_fn=lambda: base_now)
    scheduled = scheduler.run_daily_schedule()

    assert scheduled == 2
    start1, end1, _ = notion.updated["1"]
    start2, _, _ = notion.updated["2"]
    assert start2 >= end1 + timedelta(minutes=config.scheduling.buffer_minutes)


def test_scheduler_aligns_to_energy_window_start(config):
    tz = ZoneInfo(config.scheduling.timezone)
    base_now = datetime(2024, 1, 1, 6, 0, tzinfo=tz)
    task = make_task("1", "Write report", 24, tz, energy_level="Deep Work")
    notion = DummyNotion([task])
    calendar = DummyCalendar([])

    scheduler = TaskScheduler(notion, calendar, config, now_fn=lambda: base_now)
    scheduled = scheduler.run_daily_schedule()

    assert scheduled == 1
    start, end, _ = notion.updated["1"]
    assert start == datetime(2024, 1, 1, 8, 0, tzinfo=tz)
    assert end == datetime(2024, 1, 1, 9, 0, tzinfo=tz)


def test_scheduler_respects_task_range_window(config):
    tz = ZoneInfo(config.scheduling.timezone)
    base_now = datetime(2024, 1, 1, 9, 0, tzinfo=tz)
    range_start = datetime(2024, 1, 1, 15, 0, tzinfo=tz)
    range_end = datetime(2024, 1, 1, 17, 0, tzinfo=tz)
    task = make_task(
        "1",
        "Prepare slides",
        24,
        tz,
        energy_level="Moderate Focus",
        range_start=range_start,
        range_end=range_end,
    )
    notion = DummyNotion([task])
    calendar = DummyCalendar([])

    scheduler = TaskScheduler(notion, calendar, config, now_fn=lambda: base_now)
    scheduled = scheduler.run_daily_schedule()

    assert scheduled == 1
    start, end, _ = notion.updated["1"]
    assert start == range_start
    assert end == range_start + timedelta(minutes=60)


def test_scheduler_avoids_overlap_with_existing_notions(config):
    tz = ZoneInfo(config.scheduling.timezone)
    base_now = datetime(2024, 1, 1, 8, 0, tzinfo=tz)
    existing_start = datetime(2024, 1, 1, 10, 0, tzinfo=tz)
    existing_end = datetime(2024, 1, 1, 12, 0, tzinfo=tz)

    task = make_task("1", "Follow up", 24, tz)
    notion = DummyNotion(
        [task],
        scheduled_blocks=[{"id": "existing", "start": existing_start, "end": existing_end}],
    )
    calendar = DummyCalendar([])

    scheduler = TaskScheduler(notion, calendar, config, now_fn=lambda: base_now)
    scheduled = scheduler.run_daily_schedule()

    assert scheduled == 1
    start, end, _ = notion.updated["1"]
    buffer = timedelta(minutes=config.scheduling.buffer_minutes)
    assert end <= existing_start - buffer or start >= existing_end + buffer


def test_scheduler_includes_meal_breaks(config):
    tz = ZoneInfo(config.scheduling.timezone)
    base_now = datetime(2024, 1, 1, 10, 0, tzinfo=tz)
    notion = DummyNotion([])
    calendar = DummyCalendar([])

    scheduler = TaskScheduler(notion, calendar, config, now_fn=lambda: base_now)
    scheduled = scheduler.run_daily_schedule()

    assert scheduled == 0  # no Notion tasks
    meals = scheduler._virtual_schedules

    meals_today = [entry for entry in meals if entry[1].date() == base_now.date()]
    meals_tomorrow = [
        entry for entry in meals if entry[1].date() == base_now.date() + timedelta(days=1)
    ]

    assert len(meals_today) == 2
    assert len(meals_tomorrow) == 2

    lunch = next(entry for entry in meals_today if "lunch" in entry[0])
    dinner = next(entry for entry in meals_today if "dinner" in entry[0])

    assert lunch[1].time() >= time(11, 30)
    assert lunch[2].time() <= time(14, 0)
    assert dinner[1].time() >= time(18, 0)
    assert dinner[2].time() <= time(20, 45)


def test_scheduler_respects_task_dependencies(config):
    tz = ZoneInfo(config.scheduling.timezone)
    base_now = datetime(2024, 1, 1, 8, 0, tzinfo=tz)
    blocker = make_task("A", "Blocker", 12, tz)
    dependent = make_task("B", "Dependent", 12, tz, blocked_by=[blocker.id])
    notion = DummyNotion([blocker, dependent])
    calendar = DummyCalendar([])

    scheduler = TaskScheduler(notion, calendar, config, now_fn=lambda: base_now)
    scheduler.run_daily_schedule()

    assert blocker.id in notion.updated
    assert dependent.id in notion.updated
    _, blocker_end, _ = notion.updated[blocker.id]
    dependent_start, _, _ = notion.updated[dependent.id]
    assert dependent_start >= blocker_end


def test_scheduler_skips_when_dependency_not_ready(config):
    tz = ZoneInfo(config.scheduling.timezone)
    base_now = datetime(2024, 1, 1, 9, 0, tzinfo=tz)
    dependency = make_task("DEP", "Dependency", 12, tz)
    dependency.auto_schedule = False
    dependent = make_task("TASK", "Blocked Task", 12, tz, blocked_by=[dependency.id])
    notion = DummyNotion([dependent], extra_tasks=[dependency])
    calendar = DummyCalendar([])

    scheduler = TaskScheduler(notion, calendar, config, now_fn=lambda: base_now)
    scheduled = scheduler.run_daily_schedule()

    assert scheduled == 0
    assert dependent.id not in notion.updated


def test_lunch_not_rescheduled_after_window_passes(config):
    tz = ZoneInfo(config.scheduling.timezone)
    base_now = datetime(2024, 1, 1, 15, 0, tzinfo=tz)
    notion = DummyNotion([])
    calendar = DummyCalendar([])

    scheduler = TaskScheduler(notion, calendar, config, now_fn=lambda: base_now)
    scheduler.run_daily_schedule()

    today_key = f"{base_now.date().isoformat()}::lunch"
    assert all(today_key not in entry[0] for entry in scheduler._virtual_schedules)
