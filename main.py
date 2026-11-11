"""Entry point for the Notion task scheduler."""

from __future__ import annotations

import argparse
import logging
from pathlib import Path

from notion_task_scheduler import (
    CalendarManager,
    NotionTaskManager,
    TaskScheduler,
    load_config,
)
from notion_task_scheduler.config import ConfigurationError
from notion_task_scheduler.config_ui import launch_config_ui

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run the Notion task scheduler")
    parser.add_argument(
        "command",
        nargs="?",
        default="run",
        choices=("run", "config-ui"),
        help="Run the scheduler or launch the visual config editor",
    )
    parser.add_argument(
        "--config",
        type=Path,
        default=Path("config.json"),
        help="Path to the scheduler configuration file",
    )
    parser.add_argument(
        "--host",
        default="127.0.0.1",
        help="Host used by the visual config editor",
    )
    parser.add_argument(
        "--port",
        type=int,
        default=8765,
        help="Port used by the visual config editor",
    )
    parser.add_argument(
        "--open-browser",
        action="store_true",
        help="Open the config editor in the default browser",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    try:
        if args.command == "config-ui":
            launch_config_ui(
                args.config,
                host=args.host,
                port=args.port,
                open_browser=args.open_browser,
            )
            return
        config = load_config(args.config)
        notion = NotionTaskManager(config.notion, config.task_durations)
        calendar = CalendarManager(config.google_calendar)
        scheduler = TaskScheduler(notion, calendar, config)
        scheduler.run_daily_schedule()
    except ConfigurationError as exc:
        raise SystemExit(
            f"Configuration error: {exc}\n"
            "Use --config with an existing file, for example: python main.py config-ui --config config.json --open-browser"
        ) from exc


if __name__ == "__main__":
    main()
