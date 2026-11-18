"""Recorded cumulative values, never inferred work dates or estimated actuals."""
from datetime import datetime
from zoneinfo import ZoneInfo


def actual_value(task):
    """An explicit parent total takes precedence; otherwise sum known children once."""
    if task.get("actual_minutes") is not None:
        return task["actual_minutes"]
    known = [s["actual_minutes"] for s in task.get("substeps", []) if s.get("actual_minutes") is not None]
    return sum(known) if known else None


def summarize(tasks):
    actuals = [actual_value(t) for t in tasks if actual_value(t) is not None]
    return {"estimated_minutes": sum(t.get("minutes", 0) for t in tasks),
            "actual_minutes": sum(actuals) if actuals else None,
            "recorded_actual_minutes": sum(actuals), "actual_known_count": len(actuals),
            "actual_unknown_count": len(tasks) - len(actuals),
            "completed_count": sum(bool(t.get("completed")) for t in tasks), "task_count": len(tasks)}


def build_analytics(tasks, records):
    history, previous, baselines = [], None, {}
    for record in records:
        for task in record["tasks"]:
            baselines.setdefault(task["id"], task.get("minutes"))
        totals = summarize(record["tasks"])
        actual_delta = (totals["recorded_actual_minutes"] - previous["recorded_actual_minutes"]) if previous else None
        completed_delta = totals["completed_count"] - previous["completed_count"] if previous else None
        recorded = datetime.fromisoformat(record["recorded_at"])
        if recorded.tzinfo is None:
            raise ValueError("Recorded history timestamp must include timezone")
        history.append({"revision": record["revision"], "recorded_at": record["recorded_at"],
                        "date": recorded.astimezone(ZoneInfo("Europe/Moscow")).date().isoformat(), **totals,
                        "baseline": previous is None,
                        "actual_delta_minutes": actual_delta, "completed_delta_count": completed_delta,
                        "correction": (actual_delta is not None and actual_delta < 0) or (completed_delta is not None and completed_delta < 0)})
        previous = totals
    return {"recorded_since": history[0]["recorded_at"] if history else None,
            "has_history": bool(history), "timezone": "Europe/Moscow", "totals": summarize(tasks),
            "tasks": [{"task_id": t["id"], "title": t["title"], "estimated_minutes": t["minutes"],
                       "baseline_estimated_minutes": baselines.get(t["id"]),
                       "actual_minutes": actual_value(t), "completed": bool(t.get("completed")),
                       "actual_source": (t.get("actual_source", "user") if t.get("actual_minutes") is not None else "substeps" if actual_value(t) is not None else "unknown"),
                       "actual_partial": t.get("actual_minutes") is None and any(step.get("actual_minutes") is None for step in t.get("substeps", [])),
                       "substeps": [{"substep_id": step["id"], "title": step["title"],
                                     "estimated_minutes": step["minutes"], "actual_minutes": step.get("actual_minutes"),
                                     "actual_source": step.get("actual_source", "user" if step.get("actual_minutes") is not None else "unknown"),
                                     "completed": bool(step.get("completed")),
                                     "measured_minutes": step.get("measured_minutes", 0)} for step in t.get("substeps", [])],
                       "delta_minutes": actual_value(t) - t["minutes"] if actual_value(t) is not None else None,
                       "baseline_delta_minutes": actual_value(t) - baselines[t["id"]] if actual_value(t) is not None and t["id"] in baselines else None}
                      for t in tasks], "history": history,
            "history_note": "Даты Europe/Moscow фиксируют сохранение накопленного факта, а не день выполнения работы. Первая запись — исходный снимок без дневного прироста; отрицательные изменения — исправления записей. Неуказанное время остаётся неизвестным."}
