"""Utilities for interacting with the Notion API."""

from __future__ import annotations

import logging
from datetime import datetime, timedelta
from typing import Any, Dict, Iterable, Iterator, List, Optional

try:  # pragma: no cover - optional dependency for local tests
    from notion_client import Client
    from notion_client.helpers import iterate_paginated_api
    from notion_client.errors import APIResponseError
except Exception:  # pragma: no cover - optional dependency missing
    Client = None  # type: ignore
    iterate_paginated_api = None  # type: ignore
    APIResponseError = Exception  # type: ignore

from .models import Task
from .utils import parse_iso_datetime, format_iso

logger = logging.getLogger(__name__)

COMPLETED_STATUS_GROUP = "complete"
COMPLETED_STATUS_KEYWORDS: tuple[str, ...] = (
    "done",
    "complete",
    "completed",
    "archive",
    "archived",
)


class NotionTaskManager:
    """Encapsulates read/write operations on the Notion databases."""

    def __init__(self, config, duration_config) -> None:
        if Client is None:
            raise RuntimeError(
                "notion-client is not installed. Please add it to your environment"
            )

        self.client = Client(auth=config.token, notion_version="2025-09-03")
        self.tasks_database_id = config.tasks_database_id
        self.projects_database_id = config.projects_database_id
        self.properties = config.properties
        self.duration_map = dict(duration_config.durations)
        self._data_source_cache: Dict[str, str] = {}

        self.tasks_data_source_id = self._resolve_data_source_id(
            self.tasks_database_id, config.tasks_data_source_id
        )
        self._property_types: Dict[str, str] = {}
        self._status_options: Dict[str, List[str]] = {}
        self._completed_status_options: List[str] = []
        self._completed_status_names: set[str] = set()
        self._refresh_property_metadata()
        if self.projects_database_id:
            self.projects_data_source_id = self._resolve_data_source_id(
                self.projects_database_id, config.projects_data_source_id
            )
        else:
            self.projects_data_source_id = None

    # ------------------------ Retrieval helpers ------------------------
    def get_tasks_to_schedule(self, look_ahead_days: int) -> List[Task]:
        """Return tasks that should be auto-scheduled."""

        now = datetime.utcnow()

        filters = self._build_tasks_to_schedule_filter(now, include_status_filters=True)
        sorts = [
            {
                "property": self.properties["due"],
                "direction": "ascending",
            },
            {"timestamp": "created_time", "direction": "ascending"},
        ]

        logger.debug("Querying Notion for tasks to schedule")
        try:
            results = self._collect_schedulable_tasks(
                self._query_data_source(
                    self.tasks_data_source_id,
                    filter=filters,
                    sorts=sorts,
                    in_trash=False,
                )
            )
        except APIResponseError as exc:
            if not self._is_missing_status_option_error(exc):
                raise
            logger.warning(
                "Notion status schema changed while querying tasks; retrying without "
                "status-based query filters: %s",
                exc,
            )
            self._refresh_property_metadata()
            results = self._collect_schedulable_tasks(
                self._query_data_source(
                    self.tasks_data_source_id,
                    filter=self._build_tasks_to_schedule_filter(
                        now, include_status_filters=False
                    ),
                    sorts=sorts,
                    in_trash=False,
                )
            )
        logger.info("Fetched %s tasks to schedule", len(results))
        return results

    def get_tasks_by_ids(self, task_ids: Iterable[str]) -> Dict[str, Task]:
        """Retrieve Task objects for the given Notion page ids."""

        unique_ids = {task_id for task_id in task_ids if task_id and not task_id.startswith("virtual::")}
        if not unique_ids:
            return {}

        tasks: Dict[str, Task] = {}
        for task_id in unique_ids:
            try:
                page = self.client.pages.retrieve(page_id=task_id)
            except APIResponseError as exc:
                logger.warning("Unable to retrieve dependency task %s: %s", task_id, exc)
                continue
            tasks[task_id] = self._task_from_page(page)
        return tasks

    def get_incomplete_tasks(self) -> List[Task]:
        """Return scheduled tasks that were not completed before their block ended."""

        now = datetime.utcnow().isoformat()
        filters = {
            "and": [
                {
                    "property": self.properties["scheduled"],
                    "checkbox": {"equals": True},
                },
                {
                    "property": self.properties["range"],
                    "date": {"before": now},
                },
            ]
        }

        results: List[Task] = []
        for page in self._query_data_source(
            self.tasks_data_source_id,
            filter=filters,
            in_trash=False,
        ):
            task = self._task_from_page(page)
            if not self._is_completed_status_name(task.status):
                results.append(task)
        return results

    def get_recurring_templates(self) -> List[Task]:
        """Fetch recurring task templates to generate new instances."""

        filters = {
            "property": self.properties["recurrence_template"],
            "checkbox": {"equals": True},
        }

        results: List[Task] = []
        for page in self._query_data_source(
            self.tasks_data_source_id,
            filter=filters,
            in_trash=False,
        ):
            results.append(self._task_from_page(page))
        return results

    def get_scheduled_blocks(
        self,
        start: datetime,
        end: datetime,
    ) -> List[Dict[str, datetime]]:
        """Return scheduled Notion blocks within the given window."""

        range_prop = self.properties.get("range")
        scheduled_prop = self.properties.get("scheduled")
        if not range_prop or not scheduled_prop:
            return []

        filters = {
            "and": [
                {
                    "property": scheduled_prop,
                    "checkbox": {"equals": True},
                },
                {
                    "property": range_prop,
                    "date": {"is_not_empty": True},
                },
            ]
        }

        blocks: List[Dict[str, datetime]] = []
        for page in self._query_data_source(
            self.tasks_data_source_id,
            filter=filters,
            in_trash=False,
        ):
            props = page.get("properties", {})
            range_prop_value = props.get(range_prop)
            if not range_prop_value:
                continue
            range_start, range_end = _parse_date_range(range_prop_value)
            if not range_start or not range_end:
                continue
            if range_end <= start or range_start >= end:
                continue
            blocks.append({"id": page["id"], "start": range_start, "end": range_end})
        return blocks

    # ------------------------ Mutations ------------------------
    def update_task_schedule(
        self,
        task_id: str,
        start: datetime,
        end: datetime,
        calendar_event_id: Optional[str] = None,
    ) -> None:
        """Update Notion with the scheduled time block for a task."""

        properties: Dict[str, Dict] = {
            self.properties["scheduled"]: {"checkbox": True},
            self.properties["range"]: {
                "date": {
                    "start": start.isoformat(),
                    "end": end.isoformat(),
                }
            },
            self.properties["last_scheduled"]: {
                "date": {"start": datetime.utcnow().isoformat()}
            },
        }
        if calendar_event_id and self.properties.get("scheduled_block_id"):
            properties[self.properties["scheduled_block_id"]] = {
                "rich_text": [{"text": {"content": calendar_event_id}}]
            }

        try:
            self.client.pages.update(page_id=task_id, properties=properties)
        except APIResponseError as exc:
            key = self.properties.get("scheduled_block_id")
            message = str(exc).lower()
            if key and key in properties and "not a property that exists" in message:
                logger.warning(
                    "Notion property '%s' missing; disabling event sync for it", key
                )
                self.properties = dict(self.properties)
                self.properties.pop("scheduled_block_id", None)
                properties.pop(key, None)
                self.client.pages.update(page_id=task_id, properties=properties)
            else:
                raise

    # ------------------------ Helpers ------------------------
    def _query_data_source(
        self,
        data_source_id: str,
        **kwargs: Any,
    ) -> Iterable[Dict[str, Any]]:
        """Yield data source query results across SDK versions."""

        data_sources_endpoint = getattr(getattr(self.client, "data_sources", None), "query", None)
        if callable(data_sources_endpoint) and iterate_paginated_api is not None:
            return iterate_paginated_api(  # type: ignore[arg-type, return-value]
                data_sources_endpoint,
                data_source_id=data_source_id,
                **kwargs,
            )
        return self._query_data_source_manual(data_source_id, **kwargs)

    def _query_data_source_manual(
        self,
        data_source_id: str,
        **kwargs: Any,
    ) -> Iterator[Dict[str, Any]]:
        static_kwargs = {k: v for k, v in kwargs.items() if k != "start_cursor"}
        start_cursor: Optional[str] = kwargs.get("start_cursor")
        while True:
            response = self._data_source_query_raw(
                data_source_id,
                **{**static_kwargs, "start_cursor": start_cursor},
            )
            for result in response.get("results", []):
                yield result

            if not response.get("has_more"):
                break
            start_cursor = response.get("next_cursor")

    def _data_source_query_raw(
        self,
        data_source_id: str,
        **kwargs: Any,
    ) -> Dict[str, Any]:
        data_sources_endpoint = getattr(getattr(self.client, "data_sources", None), "query", None)
        if callable(data_sources_endpoint):
            return data_sources_endpoint(data_source_id=data_source_id, **kwargs)

        body_keys = (
            "filter",
            "sorts",
            "start_cursor",
            "page_size",
            "archived",
            "in_trash",
            "result_type",
        )
        query_keys = ("filter_properties",)
        body = {key: kwargs[key] for key in body_keys if key in kwargs and kwargs[key] is not None}
        query_params = {
            key: kwargs[key] for key in query_keys if key in kwargs and kwargs[key] is not None
        }
        return self.client.request(
            path=f"data_sources/{data_source_id}/query",
            method="POST",
            body=body or None,
            query=query_params or None,
            auth=kwargs.get("auth"),
        )

    def _refresh_property_metadata(self) -> None:
        (
            self._property_types,
            self._status_options,
            self._completed_status_options,
        ) = self._load_property_metadata(
            self.tasks_data_source_id,
            self.properties.get("status"),
        )
        self._completed_status_names = {
            status_name.strip().lower()
            for status_name in self._completed_status_options
            if isinstance(status_name, str) and status_name.strip()
        }
        status_property = self.properties.get("status")
        available_statuses = self._status_options.get(status_property or "", [])
        if status_property and available_statuses and not self._completed_status_names:
            logger.warning(
                "Unable to infer completed status options for property '%s'. "
                "Available options: %s. Falling back to Python-side keyword matching.",
                status_property,
                ", ".join(available_statuses),
            )

    def _load_property_metadata(
        self,
        data_source_id: str,
        status_property_name: Optional[str],
    ) -> tuple[Dict[str, str], Dict[str, List[str]], List[str]]:
        try:
            data_source = self._retrieve_data_source(data_source_id)
        except Exception as exc:  # pragma: no cover - network failure path
            logger.warning(
                "Unable to load property metadata for data source %s: %s",
                data_source_id,
                exc,
            )
            return {}, {}, []

        properties = data_source.get("properties", {})
        types: Dict[str, str] = {}
        status_options: Dict[str, List[str]] = {}
        completed_status_options: List[str] = []
        for name, meta in properties.items():
            prop_type = meta.get("type")
            prop_id = meta.get("id") if isinstance(meta.get("id"), str) else None
            if isinstance(prop_type, str):
                for key in (name, prop_id):
                    if key:
                        types[key] = prop_type
            if prop_type != "status":
                continue

            status_meta = meta.get("status") or {}
            option_names_by_id: Dict[str, str] = {}
            option_names: List[str] = []
            for option in status_meta.get("options") or []:
                if not isinstance(option, dict):
                    continue
                option_name = option.get("name")
                option_id = option.get("id")
                if not isinstance(option_name, str) or not option_name:
                    continue
                option_names.append(option_name)
                if isinstance(option_id, str) and option_id:
                    option_names_by_id[option_id] = option_name

            unique_option_names = _unique_strings(option_names)
            for key in (name, prop_id):
                if key:
                    status_options[key] = list(unique_option_names)

            if status_property_name not in {name, prop_id}:
                continue

            completed_from_groups: List[str] = []
            for group in status_meta.get("groups") or []:
                if not isinstance(group, dict):
                    continue
                group_name = group.get("name")
                if not isinstance(group_name, str):
                    continue
                if group_name.strip().lower() != COMPLETED_STATUS_GROUP:
                    continue
                for option_id in group.get("option_ids") or []:
                    option_name = option_names_by_id.get(option_id)
                    if option_name:
                        completed_from_groups.append(option_name)

            completed_status_options = _unique_strings(completed_from_groups)
            if not completed_status_options:
                completed_status_options = [
                    option_name
                    for option_name in unique_option_names
                    if self._looks_like_completed_status(option_name)
                ]
        return types, status_options, completed_status_options

    def _retrieve_data_source(self, data_source_id: str) -> Dict[str, Any]:
        retrieve_fn = getattr(getattr(self.client, "data_sources", None), "retrieve", None)
        if callable(retrieve_fn):
            return retrieve_fn(data_source_id=data_source_id)
        return self.client.request(
            path=f"data_sources/{data_source_id}",
            method="GET",
        )

    def _resolve_data_source_id(
        self,
        database_id: str,
        explicit_id: Optional[str] = None,
    ) -> str:
        if explicit_id:
            self._data_source_cache[database_id] = explicit_id
            return explicit_id

        if database_id in self._data_source_cache:
            return self._data_source_cache[database_id]

        details = self.client.databases.retrieve(database_id=database_id)
        data_sources = details.get("data_sources") or []
        if not data_sources:
            raise RuntimeError(
                f"No data sources found for database '{database_id}'. "
                "Ensure the integration has access to the database and that it has at least one data source."
            )

        data_source_id = data_sources[0].get("id")
        if not data_source_id:
            raise RuntimeError(
                f"Unable to determine data source id for database '{database_id}'."
            )
        if len(data_sources) > 1:
            names = ", ".join(source.get("name", "Unnamed") for source in data_sources)
            logger.warning(
                "Database %s has multiple data sources (%s); defaulting to %s",
                database_id,
                names,
                data_source_id,
            )
        self._data_source_cache[database_id] = data_source_id
        return data_source_id

    def _build_select_filter(self, property_name: str, value: str) -> Dict[str, Any]:
        prop_type = self._property_types.get(property_name)
        if prop_type == "status":
            return {"property": property_name, "status": {"equals": value}}
        if prop_type == "multi_select":
            return {"property": property_name, "multi_select": {"contains": value}}
        return {"property": property_name, "select": {"equals": value}}

    def _build_tasks_to_schedule_filter(
        self,
        now: datetime,
        *,
        include_status_filters: bool,
    ) -> Dict[str, Any]:
        filters: List[Dict[str, Any]] = []
        if include_status_filters:
            filters.extend(self._build_completed_status_exclusion_filters())
        filters.extend(
            [
                {
                    "property": self.properties["auto_schedule"],
                    "checkbox": {"equals": True},
                },
                {
                    "property": self.properties["recurrence_template"],
                    "checkbox": {"equals": False},
                },
                {
                    "or": [
                        {
                            "property": self.properties["scheduled"],
                            "checkbox": {"equals": False},
                        },
                        {
                            "property": self.properties["range"],
                            "date": {"before": now.isoformat()},
                        },
                    ]
                },
            ]
        )
        return {"and": filters}

    def _build_completed_status_exclusion_filters(self) -> List[Dict[str, Any]]:
        status_property = self.properties.get("status")
        if not status_property:
            return []
        return [
            {
                "property": status_property,
                "status": {"does_not_equal": status_name},
            }
            for status_name in self._completed_status_options
        ]

    def _collect_schedulable_tasks(self, pages: Iterable[Dict[str, Any]]) -> List[Task]:
        results: List[Task] = []
        for page in pages:
            task = self._task_from_page(page)
            if (
                task.auto_schedule
                and not task.recurrence_template
                and not self._is_completed_status_name(task.status)
            ):
                results.append(task)
        return results

    def _is_completed_status_name(self, status_name: Optional[str]) -> bool:
        if not isinstance(status_name, str) or not status_name.strip():
            return False
        normalized = status_name.strip().lower()
        if self._completed_status_names:
            return normalized in self._completed_status_names
        return self._looks_like_completed_status(status_name)

    @staticmethod
    def _looks_like_completed_status(status_name: str) -> bool:
        normalized = status_name.strip().lower()
        return any(keyword in normalized for keyword in COMPLETED_STATUS_KEYWORDS)

    @staticmethod
    def _is_missing_status_option_error(exc: Exception) -> bool:
        message = str(exc).lower()
        return "status option" in message and "not found for property" in message

    def mark_unscheduled(self, task_id: str) -> None:
        properties = {
            self.properties["scheduled"]: {"checkbox": False},
            self.properties["range"]: {"date": None},
        }
        self.client.pages.update(page_id=task_id, properties=properties)

    def create_recurring_instance(self, template: Task, due: datetime) -> Optional[str]:
        """Create a new task instance from a recurring template."""

        if self.properties.get("instance_of") is None:
            logger.debug("Recurring relation not configured; skipping instance creation")
            return None

        properties = {}
        for prop_name, value in template.properties.items():
            if prop_name == self.properties["recurrence_template"]:
                properties[prop_name] = {"checkbox": False}
            elif prop_name == self.properties["scheduled"]:
                properties[prop_name] = {"checkbox": False}
            elif prop_name == self.properties["range"]:
                properties[prop_name] = {"date": None}
            elif prop_name == self.properties["due"]:
                properties[prop_name] = {"date": {"start": due.isoformat()}}
            elif prop_name == self.properties["last_scheduled"]:
                continue
            else:
                properties[prop_name] = value

        properties[self.properties["instance_of"]] = {
            "relation": [{"id": template.id}]
        }

        created = self.client.pages.create(
            parent={"type": "data_source_id", "data_source_id": self.tasks_data_source_id},
            properties=properties,
        )
        return created.get("id")

    def instance_exists(self, template_id: str, due: datetime) -> bool:
        if self.properties.get("instance_of") is None:
            return False
        filters = {
            "and": [
                {
                    "property": self.properties["instance_of"],
                    "relation": {"contains": template_id},
                },
                {
                    "property": self.properties["due"],
                    "date": {"equals": due.isoformat()},
                },
            ]
        }
        response = self._data_source_query_raw(
            self.tasks_data_source_id,
            filter=filters,
            page_size=1,
            in_trash=False,
        )
        return len(response.get("results", [])) > 0

    def update_template_next_due(self, template_id: str, next_due: datetime) -> None:
        if self.properties.get("next_due") is None:
            return
        self.client.pages.update(
            page_id=template_id,
            properties={
                self.properties["next_due"]: {"date": {"start": format_iso(next_due)}}
            },
        )

    # ------------------------ Parsing helpers ------------------------
    def _task_from_page(self, page: Dict) -> Task:
        props = page.get("properties", {})
        title_prop = props.get("Name") or props.get("Task")
        name = _extract_rich_text(title_prop)

        due_prop = props.get(self.properties["due"])
        due = _parse_date_property(due_prop)

        range_prop = props.get(self.properties["range"])
        range_start, range_end = _parse_date_range(range_prop)

        duration_label = _get_select_name(props.get(self.properties["duration_estimate"]))
        duration_minutes = self.duration_map.get(duration_label or "", 0)
        if not duration_minutes:
            duration_minutes = self.duration_map.get("M", 60)

        priority = _get_select_name(props.get(self.properties["priority"]))
        energy_level = _get_select_name(props.get(self.properties["energy_level"]))
        status = props.get(self.properties["status"], {}).get("status", {}).get("name")
        auto_schedule = props.get(self.properties["auto_schedule"], {}).get("checkbox", False)
        scheduled = props.get(self.properties["scheduled"], {}).get("checkbox", False)
        recurrence_template = props.get(self.properties["recurrence_template"], {}).get(
            "checkbox", False
        )
        instance_of_rel = props.get(self.properties["instance_of"])
        instance_of = None
        if instance_of_rel and instance_of_rel.get("relation"):
            instance_of = instance_of_rel["relation"][0]["id"]

        next_due_prop = props.get(self.properties.get("next_due"))
        next_due = _parse_date_property(next_due_prop) if next_due_prop else None

        recur_interval_prop = props.get(self.properties.get("recur_interval"))
        recur_interval = None
        if recur_interval_prop and recur_interval_prop.get("number") is not None:
            try:
                recur_interval = int(recur_interval_prop["number"])
            except (TypeError, ValueError):
                recur_interval = None

        recur_unit_prop = props.get(self.properties.get("recur_unit"))
        recur_unit = _get_select_name(recur_unit_prop) if recur_unit_prop else None

        blocking_prop = props.get(self.properties.get("blocking"))
        blocked_by_prop = props.get(self.properties.get("blocked_by"))
        blocking_ids = _extract_relation_ids(blocking_prop)
        blocked_by_ids = _extract_relation_ids(blocked_by_prop)

        return Task(
            id=page["id"],
            name=name or page.get("id", "Unnamed Task"),
            due=due,
            duration_label=duration_label,
            duration_minutes=duration_minutes,
            priority=priority,
            energy_level=energy_level,
            auto_schedule=auto_schedule,
            scheduled=scheduled,
            status=status,
            range_start=range_start,
            range_end=range_end,
            recurrence_template=recurrence_template,
            instance_of=instance_of,
            next_due=next_due,
            recur_interval=recur_interval,
            recur_unit=recur_unit,
            properties=props,
            blocking_ids=blocking_ids,
            blocked_by_ids=blocked_by_ids,
        )


def _parse_date_property(prop: Optional[Dict]) -> Optional[datetime]:
    if not prop:
        return None
    date_value = prop.get("date")
    if not date_value:
        return None
    start = date_value.get("start")
    if not start:
        return None
    return parse_iso_datetime(start)


def _parse_date_range(prop: Optional[Dict]) -> tuple[Optional[datetime], Optional[datetime]]:
    if not prop or not prop.get("date"):
        return None, None
    date_value = prop["date"]
    start = date_value.get("start")
    end = date_value.get("end")
    return (
        parse_iso_datetime(start) if start else None,
        parse_iso_datetime(end) if end else None,
    )


def _extract_rich_text(prop: Optional[Dict]) -> str:
    if not prop:
        return ""
    if "title" in prop:
        parts = prop["title"]
    else:
        parts = prop.get("rich_text", [])
    return "".join(part.get("plain_text", "") for part in parts)


def _get_select_name(prop: Optional[Dict]) -> Optional[str]:
    if not prop:
        return None
    if "select" in prop and prop["select"]:
        return prop["select"].get("name")
    if "multi_select" in prop:
        values = prop.get("multi_select") or []
        return values[0]["name"] if values else None
    return None


def _extract_relation_ids(prop: Optional[Dict]) -> List[str]:
    if not prop:
        return []
    relation = prop.get("relation")
    if not isinstance(relation, list):
        return []
    ids: List[str] = []
    for entry in relation:
        entry_id = entry.get("id") if isinstance(entry, dict) else None
        if entry_id:
            ids.append(entry_id)
    return ids


def _unique_strings(values: Iterable[str]) -> List[str]:
    seen: set[str] = set()
    result: List[str] = []
    for value in values:
        if value in seen:
            continue
        seen.add(value)
        result.append(value)
    return result
