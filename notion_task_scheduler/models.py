"""Data models used by the task scheduler."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Dict, List, Optional


@dataclass
class Task:
    """Internal representation of a Notion task."""

    id: str
    name: str
    due: Optional[datetime]
    duration_label: Optional[str]
    duration_minutes: int
    priority: Optional[str]
    energy_level: Optional[str]
    auto_schedule: bool
    scheduled: bool
    status: Optional[str]
    range_start: Optional[datetime]
    range_end: Optional[datetime]
    recurrence_template: bool
    instance_of: Optional[str]
    next_due: Optional[datetime] = None
    recur_interval: Optional[int] = None
    recur_unit: Optional[str] = None
    properties: Dict[str, Any] = field(default_factory=dict)
    blocking_ids: List[str] = field(default_factory=list)
    blocked_by_ids: List[str] = field(default_factory=list)


@dataclass
class TimeSlot:
    """Represents an available time slot on a specific day."""

    start: datetime
    end: datetime

    @property
    def duration_minutes(self) -> int:
        delta = self.end - self.start
        return int(delta.total_seconds() // 60)

    def split(self, other_start: datetime, other_end: datetime) -> list["TimeSlot"]:
        """Split the slot around an allocated block and return the remaining pieces."""

        pieces: list[TimeSlot] = []
        if other_start > self.start:
            pieces.append(TimeSlot(start=self.start, end=other_start))
        if other_end < self.end:
            pieces.append(TimeSlot(start=other_end, end=self.end))
        return pieces
