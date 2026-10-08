"""Config Schema v1 definitions and validation."""

from typing import Any, Dict, List, Optional


CONFIG_SCHEMA_VERSION: int = 1


def get_default_core_profile() -> Dict[str, Any]:
    """Default structure for core_profile.json."""
    return {
        "v": CONFIG_SCHEMA_VERSION,
        "user_title": "Señor",
        "posture_limit_min": 60,
        "pomodoro_deep_work_min": 50,
        "pomodoro_short_break_min": 10,
        "pomodoro_agile_work_min": 25,
        "pomodoro_agile_break_min": 5,
        "active_courses": [],
        "critical_subjects": [],
        "tech_stack": [".NET", "C#", "Python", "Linux"]
    }


def get_default_active_agenda() -> Dict[str, Any]:
    """Default structure for active_agenda.json."""
    return {
        "v": CONFIG_SCHEMA_VERSION,
        "critical_task": None,
        "secondary_tasks": [],
        "backlog": []
    }


def validate_core_profile(data: Dict[str, Any]) -> None:
    """Validate core_profile.json against Config Schema v1."""
    if not isinstance(data, dict):
        raise ValueError("core_profile must be a JSON object")
    if data.get("v") != CONFIG_SCHEMA_VERSION:
        raise ValueError(f"Invalid core_profile version: {data.get('v')}")

    if "user_title" in data:
        if not isinstance(data["user_title"], str) or not data["user_title"].strip():
            raise ValueError("user_title must be a non-empty string")

    if not isinstance(data.get("posture_limit_min"), (int, float)) or data["posture_limit_min"] <= 0:
        raise ValueError("posture_limit_min must be a positive number")

    for timer_field in [
        "pomodoro_deep_work_min",
        "pomodoro_short_break_min",
        "pomodoro_agile_work_min",
        "pomodoro_agile_break_min",
    ]:
        if timer_field in data:
            val = data[timer_field]
            if not isinstance(val, (int, float)) or val <= 0:
                raise ValueError(f"{timer_field} must be a positive number")

    if not isinstance(data.get("active_courses"), list):
        raise ValueError("active_courses must be a list")
    if not all(isinstance(c, str) for c in data["active_courses"]):
        raise ValueError("active_courses must contain only strings")

    if "critical_subjects" in data:
        if not isinstance(data["critical_subjects"], list) or not all(isinstance(s, str) for s in data["critical_subjects"]):
            raise ValueError("critical_subjects must be a list of strings")

    if "tech_stack" in data:
        if not isinstance(data["tech_stack"], list) or not all(isinstance(t, str) for t in data["tech_stack"]):
            raise ValueError("tech_stack must be a list of strings")


def validate_active_agenda(data: Dict[str, Any]) -> None:
    """Validate active_agenda.json against Config Schema v1."""
    if not isinstance(data, dict):
        raise ValueError("active_agenda must be a JSON object")
    if data.get("v") != CONFIG_SCHEMA_VERSION:
        raise ValueError(f"Invalid active_agenda version: {data.get('v')}")

    crit = data.get("critical_task")
    if crit is not None:
        if not isinstance(crit, dict):
            raise ValueError("critical_task must be an object or null")
        if "title" in crit and not isinstance(crit["title"], str):
            raise ValueError("critical_task title must be a string")

    sec = data.get("secondary_tasks")
    if not isinstance(sec, list) or len(sec) > 2:
        raise ValueError("secondary_tasks must be a list with at most 2 tasks")
    for t in sec:
        if not isinstance(t, dict):
            raise ValueError("Each secondary_task must be an object")
        if "title" in t and not isinstance(t["title"], str):
            raise ValueError("secondary_task title must be a string")

    backlog = data.get("backlog")
    if not isinstance(backlog, list):
        raise ValueError("backlog must be a list")
    for b in backlog:
        if not isinstance(b, dict):
            raise ValueError("Each backlog item must be an object")
        if "title" in b and not isinstance(b["title"], str):
            raise ValueError("backlog task title must be a string")
