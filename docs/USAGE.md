# Setup and use

Run the commands below from the repository root.

## How it works

1. Loads configuration from JSON, with support for a local `*.local.json` overlay and environment variable overrides.
2. Fetches schedulable tasks and recurring templates from Notion.
3. Reads busy blocks from Google Calendar.
4. Builds available slots inside the configured workday, applying buffers, energy windows, dependencies, and routine blocks.
5. Updates Notion with the selected schedule and optionally mirrors the result into Google Calendar.

## Repository Layout

- `main.py`: CLI entry point.
- `notion_task_scheduler/`: configuration loading, Notion integration, calendar integration, scheduler, and config UI.
- `tests/`: unit tests for config loading, config UI, Notion querying, and scheduling behavior.
- `docs/automation-diagram.mmd` and `docs/automation-diagram.svg`: high-level flow diagram.

## Requirements

- Python 3.12+
- A Notion integration token and database IDs
- A Google service-account key if calendar reading or event creation is enabled

## Usage

```powershell
python -m venv .venv
.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
pip install -r requirements.txt
```

Copy `config.sample.json` to a local config file such as `config.json` and fill in your own values. Keep secrets and local machine-specific settings out of version control.

Run the scheduler:

```powershell
python main.py --config config.json
```

Open the configuration UI:

```powershell
python main.py config-ui --config config.json --open-browser
```

Run tests:

```powershell
pytest
```

[View the scheduling diagram](automation-diagram.svg).
