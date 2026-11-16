"""Private text project and persisted progress; no provider/network calls."""
import sqlite3

from fastapi.testclient import TestClient

from studynavigator.config import Settings
from studynavigator.main import create_app


class NoModel:
    mode = "offline"
    model = None

    async def complete(self, *args, **kwargs):
        raise AssertionError("Model must not be called")

    async def analyze(self, *args, **kwargs):
        raise AssertionError("Model must not be called")


def create_project(browser):
    response = browser.post("/api/assignments/from-text", json={
        "title": "Team workshop", "text": "Prepare a workshop. Deliver slides and a feedback form."})
    assert response.status_code == 201, response.text
    assignment = response.json()["assignment"]
    card = assignment["card"]
    card["deliverable"] = {"text": "Slides and feedback form"}
    card["requirements"] = [{"id": "slides", "text": "Create slides"}]
    card["tasks"] = [{"id": "draft", "title": "Draft slides", "minutes": 90,
                      "requirement_ids": ["slides"], "expected_result": "Slide deck",
                      "done_when": "All topics covered", "estimate_basis": "Personal estimate",
                      "min_minutes": 60, "max_minutes": 120}]
    confirmed = browser.post(f"/api/assignments/{assignment['assignment_id']}/confirm",
                             json={"revision": assignment["card_revision"], "card": card})
    assert confirmed.status_code == 200, confirmed.text
    return confirmed.json()["assignment"]


def plan_body(assignment, revision=0):
    return {"revision": revision, "card_revision": assignment["card_revision"],
            "availability": [{"day": 1, "minutes": 0}, {"day": 2, "minutes": 60}],
            "tasks": [{**assignment["card"]["tasks"][0], "completed": False, "actual_minutes": None}],
            "start_date": None, "reserve_percent": 0}


def test_text_project_sources_and_delete(tmp_path):
    settings = Settings(database_path=tmp_path / "study.sqlite", rate_limit_per_minute=1000)
    with TestClient(create_app(settings=settings, provider=NoModel())) as browser:
        assignment = create_project(browser)
        identifier = assignment["assignment_id"]
        source = assignment["sources"][0]
        page = browser.get(source["url"])
        assert page.status_code == 200
        assert "Prepare a workshop" in page.text
        assert "personal-project.pdf" not in page.text
        assert "Открыть страницу" not in page.text
        assert browser.get(f"/api/assignments/{identifier}/plan").json() == {"plan": None}
        matches = browser.get(f"/api/assignments/{identifier}/materials", params={"q": "workshop"})
        assert matches.status_code == 200 and matches.json()["matches"]
        assert matches.json()["matches"][0]["kind"] == "text"
        fetched = browser.get(f"/api/assignments/{identifier}").json()["assignment"]
        assert fetched["input_kind"] == "text"
        assert fetched["sources"][0]["kind"] == "text"
        assert browser.get(f"/api/sources/{source['source_id']}").json()["kind"] == "text"
        result = browser.put(f"/api/assignments/{identifier}/plan", json=plan_body(assignment))
        assert result.status_code == 200, result.text
        assert browser.delete(f"/api/assignments/{identifier}").status_code == 204
        assert browser.get(source["url"]).status_code == 404
        with sqlite3.connect(settings.database_path) as connection:
            for table in ("assignment_briefs", "assignment_plans", "sources", "material_fts", "requirements"):
                assert connection.execute(f"SELECT COUNT(*) FROM {table} WHERE assignment_id=?", (identifier,)).fetchone()[0] == 0


def test_saved_plan_conflicts_progress_and_isolation(tmp_path):
    settings = Settings(database_path=tmp_path / "study.sqlite", rate_limit_per_minute=1000)
    app = create_app(settings=settings, provider=NoModel())
    with TestClient(app) as browser, TestClient(app) as stranger:
        assignment = create_project(browser)
        identifier = assignment["assignment_id"]
        url = f"/api/assignments/{identifier}/plan"
        body = plan_body(assignment)
        body["tasks"][0]["expected_result"] = "Forged result"
        body["tasks"][0]["requirement_ids"] = ["forged"]
        saved = browser.put(url, json=body)
        assert saved.status_code == 200, saved.text
        plan = saved.json()["plan"]
        assert plan["revision"] == 1 and not plan["stale"]
        assert plan["input"]["tasks"][0]["expected_result"] == "Slide deck"
        assert plan["input"]["tasks"][0]["requirement_ids"] == ["slides"]
        assert plan["result"]["status"] == "insufficient_time"
        assert browser.put(url, json=body).status_code == 409
        assert stranger.get(url).status_code == 404
        assert stranger.put(url, json=body).status_code == 404
        assert stranger.get(assignment["sources"][0]["url"]).status_code == 404
        body = plan_body(assignment, revision=1)
        body["tasks"][0].update(completed=True, actual_minutes=105)
        updated = browser.put(url, json=body)
        assert updated.status_code == 200, updated.text
        assert updated.json()["plan"]["result"]["required_minutes"] == 0
        # A new app/store reads exactly the persisted input; no browser memory involved.
        with TestClient(create_app(settings=settings, provider=NoModel())) as reopened:
            reopened.cookies.update(browser.cookies)
            assert reopened.get(url).json()["plan"]["input"]["tasks"][0]["actual_minutes"] == 105
        edited = browser.post(f"/api/assignments/{identifier}/confirm", json={
            "revision": assignment["card_revision"], "card": assignment["card"]})
        assert edited.status_code == 200
        assert browser.get(url).json()["plan"]["stale"] is True
        assert browser.put(url, json=plan_body(assignment, 2)).status_code == 409


def test_invalid_text_and_public_seed_plan(tmp_path):
    settings = Settings(database_path=tmp_path / "study.sqlite", rate_limit_per_minute=1000)
    with TestClient(create_app(settings=settings, provider=NoModel())) as browser:
        for text in (" ", "x" * 100001, "bad\u0000text"):
            assert browser.post("/api/assignments/from-text", json={"title": "Project", "text": text}).status_code == 422
        assert browser.get("/api/assignments/personal-project/plan").json() == {"plan": None}


def test_full_length_unicode_text_is_accepted_and_rendered_safely(tmp_path):
    settings = Settings(database_path=tmp_path / "study.sqlite", rate_limit_per_minute=1000)
    with TestClient(create_app(settings=settings, provider=NoModel())) as browser:
        response = browser.post("/api/assignments/from-text", json={"title": "Проект", "text": "я" * 100000})
        assert response.status_code == 201, response.text
        response = browser.post("/api/assignments/from-text", json={
            "title": "<script>alert(1)</script>", "text": "<script>alert(2)</script>"})
        source = response.json()["assignment"]["sources"][0]
        page = browser.get(source["url"])
        assert "<script>" not in page.text
        assert "&lt;script&gt;" in page.text


def test_user_can_override_duration_outside_previous_range(tmp_path):
    settings = Settings(database_path=tmp_path / "study.sqlite", rate_limit_per_minute=1000)
    with TestClient(create_app(settings=settings, provider=NoModel())) as browser:
        assignment = create_project(browser)
        body = plan_body(assignment)
        body["tasks"][0]["minutes"] = 180
        response = browser.put(f"/api/assignments/{assignment['assignment_id']}/plan", json=body)
        assert response.status_code == 200, response.text
        task = response.json()["plan"]["input"]["tasks"][0]
        assert task["minutes"] == 180
        assert task["min_minutes"] is task["max_minutes"] is None
        assert task["estimate_source"] == "user"


def test_100000_unicode_characters_and_material_request_byte_bounds(tmp_path):
    import json
    from studynavigator.limits import MAX_TEXT_REQUEST_BYTES

    settings = Settings(database_path=tmp_path / "study.sqlite", rate_limit_per_minute=1000)
    with TestClient(create_app(settings=settings, provider=NoModel())) as browser:
        # Both ordinary UTF-8 (four bytes/character) and escaped JSON are valid.
        for escaped in (False, True):
            body = json.dumps({"title": "Project", "text": "😀" * 100_000}, ensure_ascii=escaped)
            response = browser.post("/api/assignments/from-text", content=body.encode(), headers={"Content-Type": "application/json"})
            assert response.status_code == 201, response.text
            identifier = response.json()["assignment"]["assignment_id"]
            response = browser.post(f"/api/assignments/{identifier}/materials", json={"title": "Extra", "text": "x"})
            assert response.status_code == 422
            assert response.json()["error"]["code"] == "text_too_large"
        created = browser.post("/api/assignments/from-text", json={"title": "Small", "text": "x"}).json()["assignment"]
        response = browser.post(f"/api/assignments/{created['assignment_id']}/materials", json={"title": "Extra", "text": "😀" * 99_999})
        assert response.status_code == 201, response.text
        response = browser.post("/api/assignments/from-text", content=b" " * (MAX_TEXT_REQUEST_BYTES + 1), headers={"Content-Type": "application/json"})
        assert response.status_code == 413
