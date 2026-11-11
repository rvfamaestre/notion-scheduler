from __future__ import annotations

import json
from pathlib import Path

from notion_task_scheduler.config import load_config
from notion_task_scheduler.config_ui import _apply_editor_state, _build_editor_state


def make_config_payload() -> dict:
    return {
        "notion": {
            "token": "secret_token",
            "tasks_database_id": "tasks_db",
            "projects_database_id": "projects_db",
            "properties": {
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
                "blocked_by": "Blocked By",
            },
        },
        "scheduling": {
            "work_hours_start": 8,
            "work_hours_end": 18,
            "default_task_duration_minutes": 60,
            "buffer_minutes": 10,
            "timezone": "Europe/Madrid",
            "look_ahead_days": 7,
            "priority_weights": {"High": 75},
            "urgency_scores": {"due_today": 150},
            "energy_windows": {"Deep Work": [[8, 12]]},
        },
        "google_calendar": {
            "busy_calendar_ids": ["primary"],
            "service_account_file": "service-account.json",
            "create_events": True,
            "event_calendar_id": "primary",
        },
        "task_durations": {"S": 30, "M": 60},
    }


def test_config_ui_round_trip_updates_behavior_settings(tmp_path: Path):
    config_path = tmp_path / "config.json"
    config_path.write_text(json.dumps(make_config_payload()), encoding="utf-8")

    initial = load_config(config_path)
    editor_state = _build_editor_state(initial, config_path)
    assert len(editor_state["scheduling"]["routine_blocks"]) == 2

    editor_state["scheduling"]["look_ahead_days"] = 14
    editor_state["scheduling"]["work_hours_end"] = 21
    editor_state["google_calendar"]["create_events"] = False
    editor_state["google_calendar"]["busy_calendar_ids"] = ["primary", "team@example.com"]
    editor_state["task_durations"]["XL"] = 180
    editor_state["scheduling"]["routine_blocks"].append(
        {
            "id": "gym",
            "label": "Gym",
            "summary": "Gym (auto)",
            "default_start": "20:00",
            "earliest": "19:00",
            "latest": "22:00",
            "duration_minutes": 60,
            "enabled": True,
            "priority": "High",
            "description": "Training block",
        }
    )

    _apply_editor_state(config_path, editor_state)

    updated = load_config(config_path)
    assert updated.scheduling.look_ahead_days == 14
    assert updated.scheduling.work_hours_end == 21
    assert updated.google_calendar.create_events is False
    assert updated.google_calendar.busy_calendar_ids == ["primary", "team@example.com"]
    assert updated.task_durations.durations["XL"] == 180
    assert any(block.id == "gym" for block in updated.scheduling.routine_blocks or [])
    assert json.loads(config_path.read_text(encoding="utf-8"))["notion"]["token"] == "secret_token"
    assert config_path.with_suffix(".json.bak").exists()
