"""Core scheduling logic for the Notion task scheduler."""

from __future__ import annotations

import logging
from collections import defaultdict
from datetime import date, datetime, timedelta, time
from typing import Callable, Dict, Iterable, List, Optional

from zoneinfo import ZoneInfo

from .calendar_manager import CalendarManager, merge_busy_blocks
from .models import Task, TimeSlot
from .utils import add_months, add_years

logger = logging.getLogger(__name__)


class TaskScheduler:
    """Orchestrates recurring handling, rescheduling and slot assignment."""
    DONE_STATUS_KEYWORDS: tuple[str, ...] = ("done", "complete", "completed", "archive", "archived")

    def __init__(
        self,
        notion_manager,
        calendar_manager: CalendarManager,
        config,
        now_fn: Optional[Callable[[], datetime]] = None,
    ) -> None:
        self.notion = notion_manager
        self.calendar = calendar_manager
        self.config = config
        self.timezone = ZoneInfo(config.scheduling.timezone)
        self._now_fn = now_fn or (lambda: datetime.now(tz=self.timezone))
        self._virtual_schedules: List[tuple[str, datetime, datetime]] = []
        self._calendar_busy_cache: dict[tuple[str, str], List[Dict[str, datetime]]] = {}
        self._schedule_cache: List[Dict[str, datetime]] = []
        self._schedule_cache_range: Optional[tuple[datetime, datetime]] = None
        self._dependency_cache: Dict[str, Task] = {}

    # ------------------------ Public API ------------------------
    def run_daily_schedule(self) -> int:
        """Execute the full scheduling routine and return tasks scheduled."""

        logger.info("Starting daily scheduler run")
        self._virtual_schedules = []
        self._calendar_busy_cache = {}
        self._create_recurring_instances()
        self._reschedule_incomplete()

        now = self._now()
        planning_end = self._day_end(now, 1)
        self._schedule_cache = self.notion.get_scheduled_blocks(now, planning_end)
        self._schedule_cache_range = (now, planning_end)

        self._resolve_conflicts(now)
        self._reset_existing_schedule(now, planning_end)

        tasks = self.notion.get_tasks_to_schedule(self.config.scheduling.look_ahead_days)
        self._prepare_dependency_cache(tasks)
        today_routines = self._build_routine_tasks(now.date())
        tasks.extend(today_routines)
        for task in tasks:
            task.score = self._calculate_score(task, now)  # type: ignore[attr-defined]
        tasks.sort(key=lambda task: getattr(task, "score", 0), reverse=True)

        today_window = self._planning_window(now, 0)
        if today_window:
            slots_today = self._collect_slots(*today_window)
            self._schedule_batch(tasks, slots_today)

        remaining = [
            task
            for task in tasks
            if not getattr(task, "_scheduled", False)
            and not self._is_virtual_task_for_day(task, now.date())
        ]

        tomorrow = now.date() + timedelta(days=1)
        tomorrow_routines = self._build_routine_tasks(tomorrow)
        for routine in tomorrow_routines:
            routine.score = self._calculate_score(routine, now)  # type: ignore[attr-defined]
        remaining.extend(tomorrow_routines)
        if remaining:
            remaining.sort(key=lambda task: getattr(task, "score", 0), reverse=True)
            tomorrow_window = self._planning_window(now, 1)
            if tomorrow_window:
                slots_tomorrow = self._collect_slots(*tomorrow_window)
                self._schedule_batch(remaining, slots_tomorrow)

        notion_tasks = [task for task in tasks if not self._is_virtual_task(task)]
        scheduled_count = sum(1 for task in notion_tasks if getattr(task, "_scheduled", False))
        logger.info("Scheduled %s/%s tasks", scheduled_count, len(notion_tasks))
        return scheduled_count

    def _now(self) -> datetime:
        return self._now_fn()

    # ------------------------ Recurring & incomplete ------------------------
    def _create_recurring_instances(self) -> None:
        templates = self.notion.get_recurring_templates()
        if not templates:
            return

        now = self._now()
        look_ahead = now + timedelta(days=self.config.scheduling.look_ahead_days)
        for template in templates:
            if not template.next_due:
                continue
            next_due_local = self._ensure_timezone(template.next_due)
            if next_due_local > look_ahead:
                continue
            if template.recur_interval is None or not template.recur_unit:
                logger.warning(
                    "Recurring template %s is missing interval/unit information", template.name
                )
                continue
            if self.notion.instance_exists(template.id, template.next_due):
                continue

            instance_id = self.notion.create_recurring_instance(template, template.next_due)
            if not instance_id:
                continue
            new_next_due = _calculate_next_due(
                template.next_due,
                template.recur_interval,
                template.recur_unit,
            )
            if new_next_due:
                self.notion.update_template_next_due(template.id, new_next_due)
                logger.info(
                    "Created recurring instance %s for template %s; next due %s",
                    instance_id,
                    template.name,
                    new_next_due.isoformat(),
                )

    def _reschedule_incomplete(self) -> None:
        incomplete = self.notion.get_incomplete_tasks()
        for task in incomplete:
            self.notion.mark_unscheduled(task.id)
            logger.info("Marked task %s as unscheduled for re-planning", task.name)

    # ------------------------ Scheduling helpers ------------------------
    def _collect_slots(
        self,
        start: datetime,
        end: datetime,
        scheduled_blocks: Optional[List[Dict[str, datetime]]] = None,
    ) -> Dict[str, List[TimeSlot]]:
        if start >= end:
            return {}

        buffer_delta = timedelta(minutes=self.config.scheduling.buffer_minutes)
        calendar_busy_adjusted: List[Dict[str, datetime]] = []
        for block in self._get_calendar_busy(start, end):
            block_start = self._ensure_timezone(block["start"]) - buffer_delta
            block_end = self._ensure_timezone(block["end"]) + buffer_delta
            block_start = max(block_start, start)
            block_end = min(block_end, end)
            if block_end <= block_start:
                continue
            calendar_busy_adjusted.append({"start": block_start, "end": block_end})

        busy_blocks = merge_busy_blocks(calendar_busy_adjusted)

        scheduled_blocks = scheduled_blocks or self._scheduled_blocks_between(start, end)
        notion_busy = merge_busy_blocks(
            [
                {
                    "start": self._ensure_timezone(block["start"]),
                    "end": self._ensure_timezone(block["end"]),
                }
                for block in scheduled_blocks
            ]
        )
        busy_blocks.extend(notion_busy)
        busy_blocks = merge_busy_blocks(busy_blocks)

        slots_by_day: Dict[str, List[TimeSlot]] = defaultdict(list)
        current_day = start.date()
        while current_day <= end.date():
            day_start = datetime.combine(
                current_day, time(self.config.scheduling.work_hours_start, tzinfo=self.timezone)
            )
            day_end = datetime.combine(current_day, self._daily_end_time(), tzinfo=self.timezone)
            if current_day == start.date():
                day_start = max(day_start, start)
            if current_day == end.date():
                day_end = min(day_end, end)
            if day_end <= day_start:
                current_day += timedelta(days=1)
                continue

            relevant_busy = [
                block
                for block in busy_blocks
                if block["start"] < day_end and block["end"] > day_start
            ]
            day_slots = _build_daily_slots(day_start, day_end, relevant_busy)
            if day_slots:
                slots_by_day[current_day.isoformat()] = day_slots
            current_day += timedelta(days=1)
        return slots_by_day

    def _get_calendar_busy(self, start: datetime, end: datetime) -> List[Dict[str, datetime]]:
        key = (start.isoformat(), end.isoformat())
        if key not in self._calendar_busy_cache:
            self._calendar_busy_cache[key] = self.calendar.get_busy_blocks(start, end)
        return [block.copy() for block in self._calendar_busy_cache[key]]

    def _scheduled_blocks_between(
        self,
        start: datetime,
        end: datetime,
    ) -> List[Dict[str, datetime]]:
        if not self._schedule_cache:
            return []
        blocks: List[Dict[str, datetime]] = []
        for block in self._schedule_cache:
            block_start = self._ensure_timezone(block["start"])
            block_end = self._ensure_timezone(block["end"])
            if block_end <= start or block_start >= end:
                continue
            blocks.append(
                {
                    "id": block.get("id"),
                    "start": block_start,
                    "end": block_end,
                }
            )
        return blocks

    def _cleanup_routine_events(self, start: datetime, end: datetime) -> None:
        if not getattr(self.calendar, "create_events", False):
            return
        seen_summaries: set[str] = set()
        for block in self.config.scheduling.routine_blocks:
            if not block.enabled:
                continue
            summary = block.summary or block.label
            if summary in seen_summaries:
                continue
            seen_summaries.add(summary)
            for event in self.calendar.find_events(summary, start, end):
                event_id = event.get("id")
                if event_id:
                    self.calendar.delete_event(event_id)

    def _schedule_task(self, task: Task, slots_by_day: Dict[str, List[TimeSlot]]) -> bool:
        duration = self._get_task_duration(task)
        if self._attempt_schedule(task, slots_by_day, duration, enforce_energy=True):
            return True
        if task.energy_level:
            logger.info(
                "Relaxing energy requirement for task %s (energy level: %s)",
                task.name,
                task.energy_level,
            )
            return self._attempt_schedule(task, slots_by_day, duration, enforce_energy=False)
        logger.warning("Unable to schedule task %s (no suitable slot found)", task.name)
        return False

    def _attempt_schedule(
        self,
        task: Task,
        slots_by_day: Dict[str, List[TimeSlot]],
        duration: int,
        *,
        enforce_energy: bool,
    ) -> bool:
        energy_level = task.energy_level if enforce_energy else None
        use_buffer = self._should_apply_buffer(task, duration)
        buffer_delta = (
            timedelta(minutes=self.config.scheduling.buffer_minutes) if use_buffer else timedelta(0)
        )
        required_minutes = duration + (self.config.scheduling.buffer_minutes if use_buffer else 0)

        for day, slots in slots_by_day.items():
            for idx, slot in enumerate(list(slots)):
                if not self._can_fit(slot, required_minutes):
                    continue

                earliest_start, latest_end = self._get_task_bounds(task, slot)
                if earliest_start >= latest_end:
                    continue

                start_time = self._align_start_time(energy_level, earliest_start, latest_end, duration)
                if not start_time:
                    continue
                end_time = start_time + timedelta(minutes=duration)
                if end_time > latest_end:
                    continue
                if not self._matches_energy_time(energy_level, start_time, end_time):
                    continue

                if self._is_virtual_task(task):
                    self._record_virtual_schedule(task, start_time, end_time)
                else:
                    self.notion.update_task_schedule(task.id, start_time, end_time, None)
                self._register_scheduled_task(task, start_time, end_time)

                slots.pop(idx)
                pieces = []
                if slot.start < start_time:
                    pieces.append(TimeSlot(slot.start, start_time))
                post_start = end_time + buffer_delta
                if post_start < slot.end:
                    pieces.append(TimeSlot(post_start, slot.end))
                slots[idx:idx] = pieces
                logger.debug(
                    "Scheduled task %s on %s (energy enforced=%s, buffer=%s)",
                    task.name,
                    day,
                    bool(energy_level),
                    use_buffer,
                )
                task._scheduled = True  # type: ignore[attr-defined]
                return True
        return False

    def _calculate_score(self, task: Task, now: datetime) -> int:
        priority_weight = self.config.scheduling.priority_weights.get(task.priority or "", 0)
        urgency = self._urgency_score(task, now)
        duration_bonus = max(
            0, self.config.scheduling.default_task_duration_minutes - task.duration_minutes
        )

        horizon_multiplier = 1.0
        if task.due:
            due_local = self._ensure_timezone(task.due)
            days_ahead = (due_local.date() - now.date()).days
            if days_ahead > 0:
                horizon_multiplier = max(0.25, 1.0 - min(days_ahead, 120) / 120.0)

        score = priority_weight * horizon_multiplier + urgency + duration_bonus
        return int(round(score))

    def _urgency_score(self, task: Task, now: datetime) -> int:
        scores = self.config.scheduling.urgency_scores
        if not task.due:
            return scores.get("no_due_date", 0)
        due_local = self._ensure_timezone(task.due)
        delta_days = (due_local.date() - now.date()).days
        if delta_days < 0:
            return scores.get("overdue", 0)
        if delta_days == 0:
            return scores.get("due_today", 0)
        if delta_days == 1:
            return scores.get("due_tomorrow", 0)
        if delta_days <= 3:
            return scores.get("due_this_week", 0)
        return scores.get("future", 0)

    def _resolve_conflicts(self, now: datetime) -> None:
        start = now
        end = now + timedelta(days=self.config.scheduling.look_ahead_days)

        scheduled_blocks = self._scheduled_blocks_between(start, end)
        if not scheduled_blocks:
            return

        intervals: List[tuple[datetime, datetime, str]] = []
        for block in scheduled_blocks:
            block_start = self._ensure_timezone(block["start"])
            block_end = self._ensure_timezone(block["end"])
            if block_end <= block_start:
                continue
            intervals.append((block_start, block_end, block["id"]))
        if not intervals:
            return

        calendar_busy = merge_busy_blocks(self._get_calendar_busy(start, end))
        conflicts: set[str] = set()

        for task_start, task_end, task_id in intervals:
            for block in calendar_busy:
                busy_start = self._ensure_timezone(block["start"])
                busy_end = self._ensure_timezone(block["end"])
                if self._intervals_overlap(task_start, task_end, busy_start, busy_end):
                    conflicts.add(task_id)
                    break

        intervals.sort(key=lambda item: item[0])
        for (prev_start, prev_end, prev_id), (next_start, next_end, next_id) in zip(
            intervals, intervals[1:]
        ):
            if self._intervals_overlap(prev_start, prev_end, next_start, next_end):
                conflicts.add(prev_id)
                conflicts.add(next_id)

        if not conflicts:
            return

        for task_id in conflicts:
            self.notion.mark_unscheduled(task_id)
            logger.info("Unscheduling conflicting task %s for replanning", task_id)

        self._schedule_cache = [
            block for block in self._schedule_cache if block.get("id") not in conflicts
        ]

    def _reset_existing_schedule(self, start: datetime, end: datetime) -> None:
        self._cleanup_routine_events(start, end)

        scheduled_blocks = self._scheduled_blocks_between(start, end)
        if not scheduled_blocks:
            self._schedule_cache = []
            self._schedule_cache_range = None
            return

        cleared_ids: set[str] = set()
        for block in scheduled_blocks:
            task_id = block.get("id")
            if not task_id:
                continue
            self.notion.mark_unscheduled(task_id)
            cleared_ids.add(task_id)
            logger.info("Reset scheduled task %s for replanning", task_id)

        if cleared_ids:
            self._schedule_cache = [
                block for block in self._schedule_cache if block.get("id") not in cleared_ids
            ]
        else:
            self._schedule_cache = []
        if not self._schedule_cache:
            self._schedule_cache_range = None

    def _schedule_batch(self, tasks: List[Task], slots_by_day: Dict[str, List[TimeSlot]]) -> set[str]:
        scheduled_ids: set[str] = set()
        if not slots_by_day:
            return scheduled_ids
        pending: List[Task] = [task for task in tasks if not getattr(task, "_scheduled", False)]
        while pending:
            progress = False
            for task in list(pending):
                if getattr(task, "_scheduled", False):
                    pending.remove(task)
                    continue
                if not self._dependencies_satisfied(task, scheduled_ids):
                    continue
                if self._schedule_task(task, slots_by_day):
                    scheduled_ids.add(task.id)
                    pending.remove(task)
                    progress = True
                else:
                    pending.remove(task)
            if not progress:
                for task in pending:
                    if not getattr(task, "_scheduled", False) and not self._dependencies_satisfied(
                        task, scheduled_ids
                    ):
                        logger.info(
                            "Task %s remains blocked by dependencies: %s",
                            task.name,
                            ", ".join(task.blocked_by_ids),
                        )
                break
        return scheduled_ids

    def _prepare_dependency_cache(self, tasks: Iterable[Task]) -> None:
        """Initialize dependency cache with tasks to schedule and their blockers."""

        notion_tasks = [task for task in tasks if not self._is_virtual_task(task)]
        cache: Dict[str, Task] = {task.id: task for task in notion_tasks}
        dependency_ids: set[str] = set()
        for task in notion_tasks:
            dependency_ids.update(task.blocked_by_ids)
        dependency_ids.difference_update(cache.keys())
        if dependency_ids:
            fetch_fn = getattr(self.notion, "get_tasks_by_ids", None)
            if callable(fetch_fn):
                fetched = fetch_fn(dependency_ids)
                cache.update(fetched)
            else:
                logger.debug(
                    "Dependency task lookup unavailable; unresolved ids: %s",
                    ", ".join(sorted(dependency_ids)),
                )
        self._dependency_cache = cache

    def _register_scheduled_task(self, task: Task, start: datetime, end: datetime) -> None:
        """Store schedule details for dependency handling."""

        start_local = self._ensure_timezone(start)
        end_local = self._ensure_timezone(end)
        task.properties["scheduled_range"] = (start_local, end_local)
        task.range_start = start_local
        task.range_end = end_local
        task.scheduled = True
        if self._is_virtual_task(task):
            return
        existing = self._dependency_cache.get(task.id)
        if existing is not None and existing is not task:
            existing.properties["scheduled_range"] = (start_local, end_local)
            existing.range_start = start_local
            existing.range_end = end_local
            existing.scheduled = True
            self._dependency_cache[task.id] = existing
        else:
            self._dependency_cache[task.id] = task

    def _dependencies_satisfied(self, task: Task, scheduled_ids: set[str]) -> bool:
        if self._is_virtual_task(task) or not task.blocked_by_ids:
            return True

        for dep_id in task.blocked_by_ids:
            dep_task = self._dependency_cache.get(dep_id)
            if not dep_task:
                logger.debug("Task %s blocked by unknown dependency %s", task.name, dep_id)
                return False
            if self._is_task_completed(dep_task):
                continue
            scheduled_range = self._get_scheduled_range(dep_task)
            if scheduled_range:
                continue
            if dep_id in scheduled_ids:
                # should not happen, but avoid deadlock
                continue
            return False
        return True

    def _is_task_completed(self, task: Task) -> bool:
        if not task.status:
            return False
        normalized = task.status.strip().lower()
        return any(keyword in normalized for keyword in self.DONE_STATUS_KEYWORDS)

    def _get_scheduled_range(self, task: Task) -> Optional[tuple[datetime, datetime]]:
        scheduled_range = task.properties.get("scheduled_range")
        start: Optional[datetime] = None
        end: Optional[datetime] = None
        if isinstance(scheduled_range, (list, tuple)) and len(scheduled_range) == 2:
            start = scheduled_range[0]
            end = scheduled_range[1]
        if (start is None or end is None) and task.range_start and task.range_end:
            start = task.range_start
            end = task.range_end
        if start is None or end is None:
            return None
        return self._ensure_timezone(start), self._ensure_timezone(end)

    def _dependency_ready_time(self, task: Task) -> Optional[datetime]:
        if self._is_virtual_task(task) or not task.blocked_by_ids:
            return None
        latest_end: Optional[datetime] = None
        for dep_id in task.blocked_by_ids:
            dep_task = self._dependency_cache.get(dep_id)
            if not dep_task or self._is_task_completed(dep_task):
                continue
            scheduled_range = self._get_scheduled_range(dep_task)
            if not scheduled_range:
                continue
            _, dep_end = scheduled_range
            if latest_end is None or dep_end > latest_end:
                latest_end = dep_end
        return latest_end

    def _planning_window(self, now: datetime, day_offset: int) -> Optional[tuple[datetime, datetime]]:
        target_date = now.date() + timedelta(days=day_offset)
        day_start = datetime.combine(
            target_date, time(self.config.scheduling.work_hours_start, tzinfo=self.timezone)
        )
        day_end = datetime.combine(target_date, self._daily_end_time(), tzinfo=self.timezone)
        if day_end <= day_start:
            day_end = day_start + timedelta(hours=1)
        if day_offset == 0:
            day_start = max(day_start, now)
        if day_start >= day_end:
            return None
        return day_start, day_end

    def _day_end(self, now: datetime, day_offset: int) -> datetime:
        target_date = now.date() + timedelta(days=day_offset)
        return datetime.combine(target_date, self._daily_end_time(), tzinfo=self.timezone)

    def _daily_end_time(self) -> time:
        work_minutes = self.config.scheduling.work_hours_end * 60
        late_cutoff = 23 * 60 + 30
        routine_cutoff = 0
        for block in self.config.scheduling.routine_blocks:
            latest_minutes = block.latest.hour * 60 + block.latest.minute
            routine_cutoff = max(routine_cutoff, latest_minutes)
        end_minutes = max(work_minutes, routine_cutoff, late_cutoff)
        end_minutes = min(end_minutes, 23 * 60 + 59)
        return time(end_minutes // 60, end_minutes % 60)

    def _build_routine_tasks(self, block_day: date) -> List[Task]:
        tz = self.timezone
        tasks: List[Task] = []
        for block in self.config.scheduling.routine_blocks:
            if not block.enabled:
                continue
            start_dt = datetime.combine(block_day, block.earliest, tzinfo=tz)
            end_dt = datetime.combine(block_day, block.latest, tzinfo=tz)
            due_dt = datetime.combine(block_day, block.default_start, tzinfo=tz)
            description = block.description or f"Auto-scheduled {block.label.lower()}"
            tasks.append(
                Task(
                    id=f"virtual::routine::{block_day.isoformat()}::{block.id}",
                    name=block.label,
                    due=due_dt,
                    duration_label=block.label,
                    duration_minutes=block.duration_minutes,
                    priority=block.priority,
                    energy_level=None,
                    auto_schedule=True,
                    scheduled=False,
                    status="Planned",
                    range_start=start_dt,
                    range_end=end_dt,
                    recurrence_template=False,
                    instance_of=None,
                    properties={
                        "virtual": True,
                        "routine_day": block_day.isoformat(),
                        "routine_block_id": block.id,
                        "summary": block.summary or block.label,
                        "description": description,
                    },
                )
            )
        return tasks

    def _is_virtual_task(self, task: Task) -> bool:
        return task.id.startswith("virtual::")

    def _is_virtual_task_for_day(self, task: Task, day: date) -> bool:
        if not self._is_virtual_task(task):
            return False
        routine_day = task.properties.get("routine_day")
        return routine_day == day.isoformat()

    def _record_virtual_schedule(self, task: Task, start: datetime, end: datetime) -> None:
        task.properties["scheduled_range"] = (start, end)
        self._virtual_schedules.append((task.id, start, end))
        if getattr(self.calendar, "create_events", False):
            summary = task.properties.get("summary") or task.name
            description = task.properties.get("description") or "Auto-scheduled routine block"
            event_id = self.calendar.ensure_event(summary, start, end, description=description)
            if event_id:
                task.properties["event_id"] = event_id

    def _matches_energy_time(
        self, energy_level: Optional[str], start: datetime, end: datetime
    ) -> bool:
        if not energy_level:
            return True
        windows = self.config.scheduling.energy_windows.get(energy_level)
        if not windows:
            return True

        start_local = start.astimezone(self.timezone)
        end_local = end.astimezone(self.timezone)
        if start_local.date() != end_local.date():
            return False

        day_start = datetime.combine(start_local.date(), time(0, tzinfo=self.timezone))
        start_minutes = int((start_local - day_start).total_seconds() // 60)
        end_minutes = int((end_local - day_start).total_seconds() // 60)

        for window_start, window_end in windows:
            if window_start <= start_minutes and end_minutes <= window_end:
                return True
        return False

    @staticmethod
    def _intervals_overlap(
        start_a: datetime,
        end_a: datetime,
        start_b: datetime,
        end_b: datetime,
    ) -> bool:
        return start_a < end_b and end_a > start_b

    def _can_fit(self, slot: TimeSlot, required_minutes: int) -> bool:
        return slot.duration_minutes >= required_minutes

    def _get_task_bounds(self, task: Task, slot: TimeSlot) -> tuple[datetime, datetime]:
        now = self._now()
        earliest = max(slot.start, now)

        range_start = self._ensure_timezone(task.range_start) if task.range_start else None
        range_end = self._ensure_timezone(task.range_end) if task.range_end else None
        if range_end and range_end <= now:
            if self._is_virtual_task(task):
                return slot.end, slot.end
            range_start = None
            range_end = None

        if range_start:
            earliest = max(earliest, range_start)

        latest = slot.end
        if range_end:
            latest = min(latest, range_end)

        dependency_ready = self._dependency_ready_time(task)
        if dependency_ready:
            earliest = max(earliest, dependency_ready)
        return earliest, latest

    def _align_start_time(
        self,
        energy_level: Optional[str],
        earliest: datetime,
        latest: datetime,
        duration_minutes: int,
    ) -> Optional[datetime]:
        latest_start = latest - timedelta(minutes=duration_minutes)
        if earliest > latest_start:
            return None

        candidate = earliest
        if energy_level:
            candidate = self._next_energy_aligned_start(
                energy_level, candidate, latest_start, duration_minutes
            )
        return candidate

    def _next_energy_aligned_start(
        self,
        energy_level: str,
        candidate: datetime,
        latest_start: datetime,
        duration_minutes: int,
    ) -> Optional[datetime]:
        windows = self.config.scheduling.energy_windows.get(energy_level)
        if not windows:
            return candidate

        candidate_local = candidate.astimezone(self.timezone)
        latest_local = latest_start.astimezone(self.timezone)
        if candidate_local.date() != latest_local.date():
            latest_local = latest_local.replace(
                year=candidate_local.year,
                month=candidate_local.month,
                day=candidate_local.day,
            )

        day_start = datetime.combine(candidate_local.date(), time(0, tzinfo=self.timezone))
        candidate_minute = int((candidate_local - day_start).total_seconds() // 60)
        latest_minute = int((latest_local - day_start).total_seconds() // 60)

        for window_start, window_end in windows:
            if window_end <= window_start:
                continue
            earliest_start = max(window_start, candidate_minute)
            latest_allowed = min(window_end - duration_minutes, latest_minute)
            if earliest_start <= latest_allowed:
                return day_start + timedelta(minutes=earliest_start)
        return None

    def _get_task_duration(self, task: Task) -> int:
        if task.duration_minutes:
            return task.duration_minutes
        return self.config.scheduling.default_task_duration_minutes

    def _should_apply_buffer(self, task: Task, duration_minutes: int) -> bool:
        if task.range_start and task.range_end:
            end = self._ensure_timezone(task.range_end)
            if end <= self._now():
                return True
            start = self._ensure_timezone(task.range_start)
            span = int((end - start).total_seconds() // 60)
            if span <= duration_minutes:
                return False
        return True

    def _ensure_timezone(self, dt: datetime) -> datetime:
        if dt.tzinfo is None:
            return dt.replace(tzinfo=self.timezone)
        return dt.astimezone(self.timezone)


def _build_daily_slots(
    day_start: datetime, day_end: datetime, busy_blocks: Iterable[Dict[str, datetime]]
) -> List[TimeSlot]:
    """Return the available slots for a single day given busy blocks."""

    busy_sorted = sorted(busy_blocks, key=lambda block: block["start"])
    slots: List[TimeSlot] = []
    cursor = day_start
    for block in busy_sorted:
        block_start = max(block["start"], day_start)
        block_end = min(block["end"], day_end)
        if block_end <= day_start or block_start >= day_end:
            continue
        if block_start > cursor:
            slots.append(TimeSlot(cursor, block_start))
        cursor = max(cursor, block_end)
    if cursor < day_end:
        slots.append(TimeSlot(cursor, day_end))
    return [slot for slot in slots if slot.duration_minutes > 0]


def _calculate_next_due(current: datetime, interval: int, unit: str) -> Optional[datetime]:
    unit_lower = unit.lower()
    if interval <= 0:
        return None
    if unit_lower.startswith("day"):
        return current + timedelta(days=interval)
    if unit_lower.startswith("week"):
        return current + timedelta(weeks=interval)
    if unit_lower.startswith("month"):
        return add_months(current, interval)
    if unit_lower.startswith("year"):
        return add_years(current, interval)
    return current + timedelta(days=interval)
