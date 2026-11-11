from __future__ import annotations

from types import SimpleNamespace

import httpx

from notion_client.errors import APIErrorCode, APIResponseError

import notion_task_scheduler.notion_manager as notion_manager_module
from notion_task_scheduler.config import NotionConfig, TaskDurationConfig
from notion_task_scheduler.models import Task


DEFAULT_PROPERTIES = {
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


class FakeClient:
    def __init__(self, auth=None, notion_version=None):
        self.auth = auth
        self.notion_version = notion_version
        self.query_calls: list[dict] = []
        self.databases = SimpleNamespace(retrieve=self._retrieve_database)
        self.data_sources = SimpleNamespace(
            retrieve=self._retrieve_data_source,
            query=self._query_data_source,
        )
        self.pages = SimpleNamespace(retrieve=lambda **kwargs: {}, update=lambda **kwargs: {}, create=lambda **kwargs: {})

    def _retrieve_database(self, database_id: str):
        return {"data_sources": [{"id": "tasks-ds"}]}

    def _retrieve_data_source(self, data_source_id: str):
        return {
            "properties": {
                "Status": {
                    "id": "status-id",
                    "type": "status",
                    "status": {
                        "options": [
                            {"id": "todo", "name": "To Do"},
                            {"id": "doing", "name": "Doing"},
                            {"id": "done", "name": "Done"},
                            {"id": "complete", "name": "Complete"},
                        ],
                        "groups": [
                            {"name": "To-do", "option_ids": ["todo"]},
                            {"name": "In progress", "option_ids": ["doing"]},
                            {"name": "Complete", "option_ids": ["done", "complete"]},
                        ],
                    },
                }
            }
        }

    def _query_data_source(self, data_source_id: str, **kwargs):
        self.query_calls.append(kwargs)
        return {"results": [], "has_more": False, "next_cursor": None}


def make_manager(monkeypatch) -> notion_manager_module.NotionTaskManager:
    monkeypatch.setattr(notion_manager_module, "Client", FakeClient)
    config = NotionConfig(
        token="fake-token",
        tasks_database_id="tasks-db",
        properties=DEFAULT_PROPERTIES,
    )
    durations = TaskDurationConfig(durations={"M": 60})
    return notion_manager_module.NotionTaskManager(config, durations)


def make_task(task_id: str, status: str) -> Task:
    return Task(
        id=task_id,
        name=f"Task {task_id}",
        due=None,
        duration_label="M",
        duration_minutes=60,
        priority="High",
        energy_level=None,
        auto_schedule=True,
        scheduled=False,
        status=status,
        range_start=None,
        range_end=None,
        recurrence_template=False,
        instance_of=None,
    )


def test_get_tasks_to_schedule_uses_schema_complete_statuses(monkeypatch):
    manager = make_manager(monkeypatch)

    manager.get_tasks_to_schedule(7)

    assert manager.client.query_calls
    query = manager.client.query_calls[0]
    assert query["in_trash"] is False

    status_filters = [
        condition
        for condition in query["filter"]["and"]
        if condition.get("property") == DEFAULT_PROPERTIES["status"] and "status" in condition
    ]
    excluded_statuses = {
        condition["status"]["does_not_equal"]
        for condition in status_filters
    }

    assert excluded_statuses == {"Done", "Complete"}
    assert "Archived" not in excluded_statuses


def test_get_tasks_to_schedule_retries_without_status_filters(monkeypatch):
    manager = make_manager(monkeypatch)
    calls: list[dict] = []

    error = APIResponseError(
        httpx.Response(
            400,
            request=httpx.Request("POST", "https://api.notion.com/v1/data_sources/tasks-ds/query"),
        ),
        'status option "Archived" not found for property "Status".',
        APIErrorCode.ValidationError,
    )

    def fake_query(*args, **kwargs):
        calls.append(kwargs)
        if len(calls) == 1:
            def failing():
                raise error
                yield {}

            return failing()
        return []

    monkeypatch.setattr(manager, "_query_data_source", fake_query)

    manager.get_tasks_to_schedule(7)

    assert len(calls) == 2
    first_status_filters = [
        condition
        for condition in calls[0]["filter"]["and"]
        if condition.get("property") == DEFAULT_PROPERTIES["status"] and "status" in condition
    ]
    second_status_filters = [
        condition
        for condition in calls[1]["filter"]["and"]
        if condition.get("property") == DEFAULT_PROPERTIES["status"] and "status" in condition
    ]

    assert first_status_filters
    assert not second_status_filters


def test_get_tasks_to_schedule_filters_completed_tasks_in_python(monkeypatch):
    manager = make_manager(monkeypatch)
    pages = [{"id": "1"}, {"id": "2"}, {"id": "3"}]
    tasks = {
        "1": make_task("1", "To Do"),
        "2": make_task("2", "Done"),
        "3": make_task("3", "Complete"),
    }

    monkeypatch.setattr(manager, "_query_data_source", lambda *args, **kwargs: pages)
    monkeypatch.setattr(manager, "_task_from_page", lambda page: tasks[page["id"]])

    results = manager.get_tasks_to_schedule(7)

    assert [task.id for task in results] == ["1"]
