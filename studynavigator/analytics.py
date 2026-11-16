"""Recorded cumulative values, never inferred work dates or estimated actuals."""
from datetime import datetime
from zoneinfo import ZoneInfo


def summarize(tasks):
    actuals = [t["actual_minutes"] for t in tasks if t.get("actual_minutes") is not None]
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
                       "actual_minutes": t.get("actual_minutes"), "completed": bool(t.get("completed")),
                       "delta_minutes": t["actual_minutes"] - t["minutes"] if t.get("actual_minutes") is not None else None,
                       "baseline_delta_minutes": t["actual_minutes"] - baselines[t["id"]] if t.get("actual_minutes") is not None and t["id"] in baselines else None}
                      for t in tasks], "history": history,
            "history_note": "Даты Europe/Moscow фиксируют сохранение накопленного факта, а не день выполнения работы. Первая запись — исходный снимок без дневного прироста; отрицательные изменения — исправления записей. Неуказанное время остаётся неизвестным."}
