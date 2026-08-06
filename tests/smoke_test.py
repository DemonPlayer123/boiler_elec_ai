from __future__ import annotations

import json
import os
import shutil
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[0]
# When this file is placed in <repo>/tests/smoke_test.py, ROOT must be repo root.
if ROOT.name == "tests":
    ROOT = ROOT.parent
sys.path.insert(0, str(ROOT))


def find_demo_project() -> Path:
    """Find a completed run that can serve as a deterministic smoke-test fixture."""
    candidates = [
        ROOT / "data" / "output" / "runs" / "25-05",
        ROOT / "demo_data" / "runs" / "25-05",
    ]
    required_any = ("items_final.json", "items_final(1).json")
    shortlist_any = ("shortlist.json", "shortlist(2).json")

    for candidate in candidates:
        if not candidate.is_dir():
            continue
        has_items = any((candidate / name).is_file() for name in required_any)
        has_shortlist = any((candidate / name).is_file() for name in shortlist_any)
        if has_items and has_shortlist:
            return candidate.resolve()

    checked = "\n".join(f"  - {path}" for path in candidates)
    raise RuntimeError(
        "Не найден эталонный завершённый прогон 25-05 для demo smoke-test.\n"
        "Проверены каталоги:\n"
        f"{checked}\n"
        "Скопируйте результаты 25-05 в data/output/runs/25-05 "
        "либо используйте полный архив с demo_data/runs/25-05."
    )

def expected_demo_tag_count(project_dir: Path) -> int:
    """Count unique tags contained in the selected demo result fixture."""

    aliases = (
        ("items_final.json", "items_final(1).json"),
        ("requirements.json", "requirements(3).json"),
        ("shortlist.json", "shortlist(2).json"),
    )

    tags: set[str] = set()

    for filenames in aliases:
        source_file = next(
            (
                project_dir / filename
                for filename in filenames
                if (project_dir / filename).is_file()
            ),
            None,
        )

        if source_file is None:
            continue

        payload = json.loads(source_file.read_text(encoding="utf-8"))

        if not isinstance(payload, list):
            continue

        for row in payload:
            if not isinstance(row, dict):
                continue

            tag = str(row.get("tag") or "").strip().upper()
            if tag:
                tags.add(tag)

    if not tags:
        raise AssertionError(
            f"В демонстрационном прогоне не найдены теги: {project_dir}"
        )

    return len(tags)

DEMO_PROJECT_DIR = find_demo_project()
EXPECTED_DEMO_TAGS = expected_demo_tag_count(DEMO_PROJECT_DIR)
TEST_ROOT = ROOT / ".tmp" / "web-smoke"
PROJECTS_ROOT = TEST_ROOT / "projects"
RUNS_ROOT = TEST_ROOT / "runs"

# Direct assignment is intentional: the test must not inherit stale shell settings.
os.environ["BOILER_ELEC_PIPELINE_MODE"] = "demo"
os.environ["BOILER_ELEC_RUNS_ROOT"] = str(RUNS_ROOT)
os.environ["BOILER_ELEC_PROJECTS_ROOT"] = str(PROJECTS_ROOT)
os.environ["BOILER_ELEC_DEMO_RUNS_ROOT"] = str(DEMO_PROJECT_DIR.parent)
os.environ["BOILER_ELEC_DEMO_PROJECT"] = DEMO_PROJECT_DIR.name

from fastapi.testclient import TestClient  # noqa: E402
from src.web.app import app  # noqa: E402


def main() -> None:
    project_id = "web-smoke-test"
    client = TestClient(app)

    if TEST_ROOT.exists():
        shutil.rmtree(TEST_ROOT)
    PROJECTS_ROOT.mkdir(parents=True, exist_ok=True)
    RUNS_ROOT.mkdir(parents=True, exist_ok=True)

    health = client.get("/api/health")
    assert health.status_code == 200, health.text
    health_payload = health.json()
    assert health_payload["pipeline_mode"] == "demo", health_payload

    response = client.post(
        "/api/projects",
        json={"project_id": project_id, "overwrite": True},
    )
    assert response.status_code == 201, response.text

    # These are intentional transport stubs. In demo mode the test does not parse them.
    uploads = [
        ("schemes", "scheme.pdf", b"%PDF-1.4\n%%EOF"),
        ("passports", "passport.pdf", b"%PDF-1.4\npassport\n%%EOF"),
        ("template", "template.xlsx", b"PK\x03\x04demo"),
    ]
    for category, filename, content in uploads:
        response = client.post(
            f"/api/projects/{project_id}/files?category={category}",
            files=[("files", (filename, content, "application/octet-stream"))],
        )
        assert response.status_code == 200, response.text

    files_response = client.get(f"/api/projects/{project_id}/files")
    assert files_response.status_code == 200, files_response.text
    assert len(files_response.json()["files"]) == 3

    scan = client.post(f"/api/projects/{project_id}/scan-schemes")
    assert scan.status_code == 200, scan.text
    assert scan.json()["scheme_scan_completed"] is True
    assert scan.json()["detected_equipment"]

    response = client.post(f"/api/projects/{project_id}/preprocess")
    assert response.status_code == 200, response.text

    answered = 0
    editable_question: tuple[str, float] | None = None
    while True:
        response = client.get(f"/api/projects/{project_id}/questions/next")
        assert response.status_code == 200, response.text
        question = response.json()["question"]
        if question is None:
            break

        if question["type"] == "boolean":
            value = False
        elif question["type"] == "select":
            value = question["options"][0]
        elif question["type"] == "number":
            value = max(question.get("min") or 0, 1)
        else:
            value = ""

        answer = client.post(
            f"/api/projects/{project_id}/questions/{question['id']}/answer",
            json={"value": value, "action": "answer"},
        )
        if editable_question is None and question["type"] == "number" and question.get("tag") != "_meta":
            editable_question = (question["id"], float(value))
        assert answer.status_code == 200, answer.text
        answered += 1

    questions_response = client.get(f"/api/projects/{project_id}/questions")
    assert questions_response.status_code == 200, questions_response.text
    assert questions_response.json()["questions"]
    if editable_question is not None:
        question_id, old_value = editable_question
        replace_response = client.post(
            f"/api/projects/{project_id}/questions/{question_id}/answer",
            json={"value": old_value + 1, "action": "answer", "replace": True},
        )
        assert replace_response.status_code == 200, replace_response.text
        assert replace_response.json()["status"] == "READY_TO_RUN"

    response = client.post(f"/api/projects/{project_id}/run")
    assert response.status_code == 202, response.text

    state: dict = {}
    for _ in range(200):
        response = client.get(f"/api/projects/{project_id}/status")
        assert response.status_code == 200, response.text
        state = response.json()
        if state["status"] in {"COMPLETED", "FAILED"}:
            break
        time.sleep(0.05)

    if state.get("status") != "COMPLETED":
        raise AssertionError(
            "Demo pipeline did not complete:\n"
            + json.dumps(state, ensure_ascii=False, indent=2)
        )

    equipment = client.get(f"/api/projects/{project_id}/equipment")
    assert equipment.status_code == 200, equipment.text
    payload = equipment.json()
    assert payload["counters"]["total"] == EXPECTED_DEMO_TAGS, {
        "expected": EXPECTED_DEMO_TAGS,
        "actual": payload["counters"],
    }
    
    assert len(payload["items"]) == EXPECTED_DEMO_TAGS

    detail = client.get(f"/api/projects/{project_id}/equipment/К6?limit=8")
    assert detail.status_code == 200, detail.text
    assert len(detail.json().get("candidate_options") or []) <= 8

    downloads = client.get(f"/api/projects/{project_id}/downloads")
    assert downloads.status_code == 200, downloads.text
    download_rows = downloads.json()["files"]
    assert download_rows and all(row.get("display_name") for row in download_rows)
    assert downloads.json().get("groups")

    invite = client.post("/api/invites", json={"label": "Smoke share", "expires_hours": 2, "max_uses": 2})
    assert invite.status_code == 201, invite.text
    invite_token = invite.json()["token"]
    shared = client.post("/api/projects", json={"invite_token": invite_token})
    assert shared.status_code == 201, shared.text
    shared_payload = shared.json()
    shared_id = shared_payload["project_id"]
    shared_key = shared_payload["project_key"]
    denied = client.get(f"/api/projects/{shared_id}/dialog")
    assert denied.status_code == 403, denied.text
    allowed = client.get(f"/api/projects/{shared_id}/dialog", headers={"X-Project-Key": shared_key})
    assert allowed.status_code == 200, allowed.text

    user_inputs_path = RUNS_ROOT / project_id / "user_inputs.json"
    assert user_inputs_path.is_file(), user_inputs_path

    # A completed project can be rerun; the previous result is archived first.
    rerun = client.post(f"/api/projects/{project_id}/run")
    assert rerun.status_code == 202, rerun.text
    for _ in range(200):
        state = client.get(f"/api/projects/{project_id}/status").json()
        if state["status"] in {"COMPLETED", "FAILED"}:
            break
        time.sleep(0.05)
    assert state["status"] == "COMPLETED", state
    assert list((RUNS_ROOT / project_id).glob("previous_run_*.zip"))

    # Source-file changes invalidate the HITL/result state so stale output is not shown.
    files = client.get(f"/api/projects/{project_id}/files").json()["files"]
    passport_file = next(row for row in files if row["category"] == "passports")
    deleted = client.delete(f"/api/projects/{project_id}/files/{passport_file['id']}")
    assert deleted.status_code == 200, deleted.text
    invalidated = deleted.json()
    assert invalidated["status"] == "WAITING_FILES", invalidated
    assert invalidated["questions_total"] == 0, invalidated
    assert invalidated["stale_results"] is True, invalidated

    print(
        "OK: demo web flow completed; "
        f"questions answered={answered}; equipment tags={payload['counters']['total']}"
    )
    print(f"Demo source: {DEMO_PROJECT_DIR}")
    print(f"Test artifacts: {RUNS_ROOT / project_id}")


if __name__ == "__main__":
    main()
