"""Append-only materials, honest recorded analytics and explicit proposal commits."""
import sqlite3

from fastapi.testclient import TestClient

from studynavigator.config import Settings
from studynavigator.main import create_app
from test_project_workspace import NoModel, create_project, plan_body
from test_upload_api import text_pdf


def test_append_materials_preserves_progress_isolates_sources_and_limits(tmp_path):
    settings = Settings(database_path=tmp_path / "study.db", uploads_path=tmp_path / "uploads", rate_limit_per_minute=1000)
    app = create_app(settings=settings, provider=NoModel())
    with TestClient(app) as browser, TestClient(app) as stranger:
        assignment = create_project(browser)
        identifier = assignment["assignment_id"]
        url = f"/api/assignments/{identifier}"
        body = plan_body(assignment)
        body["tasks"][0].update(actual_minutes=17)
        before = browser.put(url + "/plan", json=body).json()
        appended = browser.post(url + "/materials", json={"title": "Research article", "text": "Useful external workshop evidence.", "external_url": "https://example.org/article"})
        assert appended.status_code == 201, appended.text
        updated = appended.json()["assignment"]
        assert updated["card"] == assignment["card"]
        assert updated["card_revision"] == assignment["card_revision"]
        source = updated["sources"][-1]
        detail = browser.get(f"/api/sources/{source['source_id']}").json()
        assert detail["external_url"] == "https://example.org/article"
        assert detail["summary"] == detail["preview"] == source["excerpt"]
        assert detail["file_url"] is None
        assert stranger.get(f"/api/sources/{source['source_id']}").status_code == 404
        assert stranger.post(url + "/materials", json={"title": "Other", "text": "No"}).status_code == 404
        assert browser.get(url + "/plan").json() == before
        assert browser.post(url + "/materials", json={"title": "Bad", "text": "text", "external_url": "file:///etc/passwd"}).status_code == 422
        uploaded = browser.post(url + "/materials", files={"files": ("extra.pdf", text_pdf("Additional evidence"), "application/pdf")})
        assert uploaded.status_code == 201, uploaded.text
        pdf_source = uploaded.json()["assignment"]["sources"][-1]
        assert pdf_source["kind"] == "pdf" and pdf_source["file_url"]
        assert browser.get(pdf_source["file_url"]).status_code == 200
        assert stranger.get(pdf_source["file_url"]).status_code == 404
        assert browser.get(url + "/plan").json() == before
        assert browser.post(url + "/materials", files=[("files", (f"{i}.pdf", text_pdf("extra"), "application/pdf")) for i in range(3)]).status_code == 422
        assert len(browser.get(url).json()["assignment"]["documents"]) == 1
        assert len(list(settings.uploads_path.glob("*.pdf"))) == 1
        assert browser.delete(url).status_code == 204
        assert not list(settings.uploads_path.glob("*.pdf"))


def test_recorded_history_deltas_corrections_and_reload(tmp_path):
    settings = Settings(database_path=tmp_path / "study.db", rate_limit_per_minute=1000)
    with TestClient(create_app(settings=settings, provider=NoModel())) as browser:
        assignment = create_project(browser)
        url = f"/api/assignments/{assignment['assignment_id']}"
        assert browser.get(url + "/analytics").json()["analytics"]["has_history"] is False
        body = plan_body(assignment)
        assert browser.put(url + "/plan", json=body).status_code == 200
        first = browser.get(url + "/analytics").json()["analytics"]
        assert first["totals"]["actual_minutes"] is None
        assert first["tasks"][0]["actual_minutes"] is None
        assert first["history"][0]["actual_delta_minutes"] is None
        body["revision"] = 1
        body["tasks"][0].update(completed=True, actual_minutes=105)
        assert browser.put(url + "/plan", json=body).status_code == 200
        body["revision"] = 2
        body["tasks"][0].update(completed=False, actual_minutes=70)
        assert browser.put(url + "/plan", json=body).status_code == 200
        assert browser.put(url + "/plan", json=body).status_code == 409
        analytics = browser.get(url + "/analytics").json()["analytics"]
        assert len(analytics["history"]) == 3
        assert analytics["totals"]["actual_minutes"] == 70
        assert analytics["history"][-1]["actual_delta_minutes"] == -35
        assert analytics["history"][-1]["completed_delta_count"] == -1
        assert analytics["history"][-1]["correction"] is True
        assert analytics["tasks"][0]["delta_minutes"] == -20
        with TestClient(create_app(settings=settings, provider=NoModel())) as reloaded:
            reloaded.cookies.update(browser.cookies)
            assert reloaded.get(url + "/analytics").json()["analytics"] == analytics
            assert reloaded.get(url).json()["assignment"]["project_context"]["analytics"] == analytics
        assert browser.delete(url).status_code == 204
        with sqlite3.connect(settings.database_path) as connection:
            assert connection.execute("SELECT COUNT(*) FROM progress_history").fetchone()[0] == 0


def test_assistant_token_explicit_apply_one_use_and_atomic_versions(tmp_path, monkeypatch):
    from studynavigator.agent import StudyAgent
    async def proposal(*args, **kwargs):
        return {"status": "ok", "trace": [], "action_proposal": {"summary": "Завершить действие", "operations": [{"op": "complete", "task_id": "draft", "actual_minutes": 42}]}}
    monkeypatch.setattr(StudyAgent, "run", proposal)
    settings = Settings(database_path=tmp_path / "study.db", rate_limit_per_minute=1000)
    app = create_app(settings=settings, provider=NoModel())
    with TestClient(app) as browser, TestClient(app) as stranger:
        assignment = create_project(browser)
        identifier = assignment["assignment_id"]
        url = f"/api/assignments/{identifier}"
        assert browser.put(url + "/plan", json=plan_body(assignment)).status_code == 200
        response = browser.post("/api/chat", json={"assignment_id": identifier, "message": "Отметь задачу draft завершённой, потрачено 42 минуты"})
        assert response.status_code == 200, response.text
        action = response.json()["action_proposal"]
        payload = {k: action[k] for k in ("proposal_id", "card_revision", "plan_revision")}
        assert browser.get(url + "/plan").json()["plan"]["input"]["tasks"][0]["completed"] is False
        assert browser.post(url + "/assistant-actions", json={**payload, "apply": False}).status_code == 422
        assert stranger.post(url + "/assistant-actions", json={**payload, "apply": True}).status_code == 404
        applied = browser.post(url + "/assistant-actions", json={**payload, "apply": True})
        assert applied.status_code == 200, applied.text
        result = applied.json()
        assert result["plan"]["input"]["tasks"][0]["actual_minutes"] == 42
        assert result["assignment"]["card_revision"] == assignment["card_revision"] + 1
        assert result["plan"]["stale"] is False
        assert browser.post(url + "/assistant-actions", json={**payload, "apply": True}).status_code == 409
        fresh = browser.post("/api/chat", json={"assignment_id": identifier, "message": "Отметь draft завершённой"}).json()["action_proposal"]
        changed = result["plan"]["input"]
        changed.update(revision=result["plan"]["revision"], card_revision=result["assignment"]["card_revision"])
        changed["tasks"][0]["actual_minutes"] = 50
        assert browser.put(url + "/plan", json=changed).status_code == 200
        stale = {k: fresh[k] for k in ("proposal_id", "card_revision", "plan_revision")}
        assert browser.post(url + "/assistant-actions", json={**stale, "apply": True}).status_code == 409
        unchanged = browser.get(url).json()["assignment"]
        assert unchanged["card_revision"] == result["assignment"]["card_revision"]
        assert unchanged["project_context"]["plan"]["input"]["tasks"][0]["actual_minutes"] == 50


def test_material_revision_blocks_stale_analysis_and_unicode_append(tmp_path, monkeypatch):
    settings = Settings(database_path=tmp_path / "study.db", rate_limit_per_minute=1000)
    with TestClient(create_app(settings=settings, provider=NoModel())) as browser:
        assignment = create_project(browser)
        identifier = assignment["assignment_id"]
        url = f"/api/assignments/{identifier}"
        async def interrupted_analysis(*args):
            with sqlite3.connect(settings.database_path) as connection:
                connection.execute("UPDATE assignments SET materials_revision=materials_revision+1 WHERE assignment_id=?", (identifier,))
            return {"card": assignment["card"]}
        monkeypatch.setattr("studynavigator.main.analyze_assignment", interrupted_analysis)
        response = browser.post(url + "/analyze", json={})
        assert response.status_code == 409, response.text
        assert browser.get(url).json()["assignment"]["card_revision"] == assignment["card_revision"]
        response = browser.post(url + "/materials", json={"title": "Дополнение", "text": "я" * 99000})
        assert response.status_code == 201, response.text
        assert browser.post(url + "/materials", json={"title": "Лимит", "text": "я" * 2000}).status_code == 422


def test_analytics_unknown_moscow_baseline_and_reestimate_are_honest():
    from studynavigator.analytics import build_analytics
    initial = {"id": "one", "title": "Work", "minutes": 60, "actual_minutes": None, "completed": False}
    logged = {**initial, "minutes": 90, "actual_minutes": 75}
    analytics = build_analytics([logged], [
        {"revision": 1, "recorded_at": "2026-11-14T22:30:00+00:00", "tasks": [initial]},
        {"revision": 2, "recorded_at": "2026-11-15T00:30:00+00:00", "tasks": [logged]}])
    first, second = analytics["history"]
    assert first["date"] == second["date"] == "2026-11-15"
    assert first["actual_minutes"] is None and first["baseline"] is True
    assert first["actual_delta_minutes"] is None
    assert second["actual_delta_minutes"] == 75
    task = analytics["tasks"][0]
    assert task["estimated_minutes"] == 90 and task["delta_minutes"] == -15
    assert task["baseline_estimated_minutes"] == 60 and task["baseline_delta_minutes"] == 15
    assert analytics["totals"]["actual_unknown_count"] == 0
    legacy = build_analytics([logged], [])
    assert legacy["tasks"][0]["baseline_estimated_minutes"] is None


def test_priority_reason_retained_and_material_append_invalidates_proposal(tmp_path, monkeypatch):
    from studynavigator.agent import StudyAgent
    async def proposal(*args, **kwargs):
        return {"status": "ok", "trace": [], "action_proposal": {"summary": "Завершить", "operations": [{"op": "complete", "task_id": "draft"}]}}
    monkeypatch.setattr(StudyAgent, "run", proposal)
    settings = Settings(database_path=tmp_path / "study.db", rate_limit_per_minute=1000)
    with TestClient(create_app(settings=settings, provider=NoModel())) as browser:
        assignment = create_project(browser)
        url = f"/api/assignments/{assignment['assignment_id']}"
        card = assignment["card"]
        card["tasks"][0].update(priority="high", priority_reason="Слайды нужны для следующей проверки")
        assignment = browser.post(url + "/confirm", json={"revision": assignment["card_revision"], "card": card}).json()["assignment"]
        saved = browser.put(url + "/plan", json=plan_body(assignment)).json()["plan"]
        task = saved["input"]["tasks"][0]
        assert task["priority"] == "high" and task["priority_reason"] == card["tasks"][0]["priority_reason"]
        response = browser.post("/api/chat", json={"assignment_id": assignment["assignment_id"], "message": "Отметь draft завершённой"})
        action = response.json()["action_proposal"]
        assert browser.post(url + "/materials", json={"title": "Новое", "text": "Материал появился после предложения"}).status_code == 201
        payload = {k: action[k] for k in ("proposal_id", "card_revision", "plan_revision")}
        applied = browser.post(url + "/assistant-actions", json={**payload, "apply": True})
        assert applied.status_code == 409, applied.text
        assert browser.get(url + "/plan").json()["plan"] == saved
        assert browser.get(url).json()["assignment"]["card_revision"] == assignment["card_revision"]
