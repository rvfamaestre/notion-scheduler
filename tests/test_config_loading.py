from __future__ import annotations

import json
from pathlib import Path

from notion_task_scheduler.config import load_config


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


def test_load_config_supports_local_overlay_and_env_overrides(tmp_path: Path, monkeypatch):
    base_payload = make_config_payload()
    base_payload["notion"].pop("token")
    base_payload["google_calendar"].pop("service_account_file")

    base_path = tmp_path / "config.base.json"
    base_path.write_text(json.dumps(base_payload), encoding="utf-8")

    overlay_payload = {
        "notion": {"token": "local_secret"},
        "google_calendar": {"service_account_file": "local-service.json"},
    }
    (tmp_path / "config.base.local.json").write_text(
        json.dumps(overlay_payload),
        encoding="utf-8",
    )

    monkeypatch.setenv("NOTION_TOKEN", "env_secret")
    monkeypatch.setenv("GOOGLE_EVENT_CALENDAR_ID", "team@example.com")
    monkeypatch.setenv("GOOGLE_BUSY_CALENDAR_IDS", "primary\nfocus@example.com")

    config = load_config(base_path)

    assert config.notion.token == "env_secret"
    assert config.google_calendar.service_account_file == "local-service.json"
    assert config.google_calendar.event_calendar_id == "team@example.com"
    assert config.google_calendar.busy_calendar_ids == ["primary", "focus@example.com"]
