from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
import time
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def find_template(repo_root: Path, project_code: str) -> Path:
    candidates: list[Path] = []
    for relative in ("data/input/templates", "data/input/emplates"):
        directory = repo_root / relative
        if directory.exists():
            candidates.extend(sorted(directory.glob(f"*{project_code}*.xlsx")))
            candidates.extend(sorted(directory.glob("*.xlsx")))
    unique = []
    seen = set()
    for candidate in candidates:
        resolved = candidate.resolve()
        if resolved not in seen:
            seen.add(resolved)
            unique.append(resolved)
    if not unique:
        raise FileNotFoundError(
            "Не найден XLSX-шаблон в data/input/templates или data/input/emplates"
        )
    return unique[0]


def load_json(path: Path, default: Any) -> Any:
    if not path.exists():
        return default
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return default


def meta_answer(question: dict[str, Any], answers: dict[str, Any]) -> tuple[bool, Any]:
    meta = answers.get("_meta") if isinstance(answers.get("_meta"), dict) else {}
    field = question.get("field")
    if field in {"lighting_in_cabinet", "heating_needed"}:
        return field in meta, meta.get(field)
    if field == "heating_items":
        heating = meta.get("heating") if isinstance(meta.get("heating"), dict) else {}
        items = heating.get("items") if isinstance(heating.get("items"), list) else []
        lines = []
        for item in items:
            if isinstance(item, dict) and item.get("name") and item.get("meters") is not None:
                lines.append(f"{item['name']};{item['meters']}")
        return bool(lines), "\n".join(lines)
    if field == "roof_len_m":
        heating = meta.get("heating") if isinstance(meta.get("heating"), dict) else {}
        roof = heating.get("roof") if isinstance(heating.get("roof"), dict) else {}
        value = roof.get("roof_len_m")
        return value is not None, value
    if field == "cabinets":
        cabinets = meta.get("cabinets") if isinstance(meta.get("cabinets"), list) else []
        lines = []
        for item in cabinets:
            if isinstance(item, dict) and item.get("tag") and item.get("p_kw") is not None:
                lines.append(f"{item['tag']};{item['p_kw']}")
        return True, "\n".join(lines)
    return False, None


def resolve_answer(question: dict[str, Any], answers: dict[str, Any]) -> tuple[bool, Any]:
    tag = str(question.get("tag") or "")
    field = str(question.get("field") or "")
    if tag == "_meta":
        return meta_answer(question, answers)
    values = answers.get(tag)
    if isinstance(values, dict) and field in values:
        return True, values[field]
    return False, None


def upload_group(client: Any, project_id: str, category: str, paths: list[Path]) -> None:
    files = []
    for path in paths:
        content_type = (
            "application/pdf" if path.suffix.lower() == ".pdf"
            else "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
        )
        files.append(("files", (path.name, path.read_bytes(), content_type)))
    response = client.post(
        f"/api/projects/{project_id}/files?category={category}", files=files
    )
    assert response.status_code == 200, response.text


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Сквозная проверка FastAPI/HITL на реальных файлах проекта"
    )
    parser.add_argument("--source-project", default="25-05")
    parser.add_argument("--test-project", default="web-real-25-05")
    parser.add_argument("--expected-items", type=int, default=35)
    parser.add_argument("--timeout", type=int, default=1800)
    parser.add_argument("--clean", action="store_true")
    parser.add_argument("--skip-norms", action="store_true")
    args = parser.parse_args()

    repo_root = ROOT
    source_project = args.source_project
    schemes_dir = repo_root / "data" / "input" / "schemes" / source_project
    passports_dir = repo_root / "data" / "input" / "passports" / source_project
    template_path = find_template(repo_root, source_project)
    answers_path = repo_root / "data" / "output" / "runs" / source_project / "user_inputs.json"

    if not schemes_dir.exists():
        raise FileNotFoundError(f"Не найдена папка схем: {schemes_dir}")
    if not passports_dir.exists():
        raise FileNotFoundError(f"Не найдена папка паспортов: {passports_dir}")
    scheme_files = sorted(schemes_dir.glob("*.pdf"))
    passport_files = sorted(passports_dir.glob("*.pdf"))
    if not scheme_files:
        raise RuntimeError(f"В {schemes_dir} нет PDF-схем")
    if not passport_files:
        raise RuntimeError(f"В {passports_dir} нет PDF-паспортов")

    test_root = repo_root / ".tmp" / "web-real"
    projects_root = test_root / "projects"
    runs_root = test_root / "runs"
    project_root = projects_root / args.test_project
    run_root = runs_root / args.test_project
    shutil.rmtree(project_root, ignore_errors=True)
    shutil.rmtree(run_root, ignore_errors=True)

    os.environ["BOILER_ELEC_PIPELINE_MODE"] = "real"
    os.environ["BOILER_ELEC_PROJECTS_ROOT"] = str(projects_root)
    os.environ["BOILER_ELEC_RUNS_ROOT"] = str(runs_root)
    os.environ["BOILER_ELEC_DEMO_RUNS_ROOT"] = str(repo_root / "demo_data" / "runs")
    if args.skip_norms:
        os.environ["BOILER_ELEC_NORMS_DIR"] = str(test_root / "no-norms")
    else:
        os.environ["BOILER_ELEC_NORMS_DIR"] = str(repo_root / "data" / "norms")

    # Import only after environment variables are fixed: app.py constructs its
    # repositories and services at import time.
    from fastapi.testclient import TestClient
    from src.web.app import app

    client = TestClient(app)
    project_id = args.test_project
    create = client.post(
        "/api/projects", json={"project_id": project_id, "overwrite": True}
    )
    assert create.status_code == 201, create.text

    upload_group(client, project_id, "schemes", scheme_files)
    scan = client.post(f"/api/projects/{project_id}/scan-schemes")
    assert scan.status_code == 200, scan.text
    assert scan.json().get("scheme_scan_completed") is True, scan.json()
    upload_group(client, project_id, "passports", passport_files)
    upload_group(client, project_id, "template", [template_path])

    preprocess = client.post(f"/api/projects/{project_id}/preprocess")
    assert preprocess.status_code == 200, preprocess.text
    preprocess_state = preprocess.json()
    assert preprocess_state.get("pipeline_mode") == "real", preprocess_state

    answers = load_json(answers_path, {})
    if not isinstance(answers, dict):
        answers = {}
    unresolved_required: list[str] = []
    answered = 0
    skipped = 0

    while True:
        payload = client.get(f"/api/projects/{project_id}/questions/next")
        assert payload.status_code == 200, payload.text
        question = payload.json()["question"]
        if question is None:
            break
        found, value = resolve_answer(question, answers)
        if not found:
            if question.get("required"):
                unresolved_required.append(question["id"])
                break
            response = client.post(
                f"/api/projects/{project_id}/questions/{question['id']}/answer",
                json={"action": "skip", "value": None},
            )
            skipped += 1
        else:
            response = client.post(
                f"/api/projects/{project_id}/questions/{question['id']}/answer",
                json={"action": "answer", "value": value},
            )
            answered += 1
        assert response.status_code == 200, response.text

    if unresolved_required:
        raise AssertionError(
            "В эталонном user_inputs.json отсутствуют обязательные ответы: "
            + ", ".join(unresolved_required)
            + f". Файл ответов: {answers_path}"
        )

    preflight = client.get(f"/api/projects/{project_id}/preflight")
    assert preflight.status_code == 200, preflight.text
    check = preflight.json()
    assert check["ok"], json.dumps(check, ensure_ascii=False, indent=2)

    run = client.post(f"/api/projects/{project_id}/run")
    assert run.status_code == 202, run.text

    deadline = time.monotonic() + args.timeout
    state: dict[str, Any] = {}
    last_stage = None
    while time.monotonic() < deadline:
        response = client.get(f"/api/projects/{project_id}/status")
        assert response.status_code == 200, response.text
        state = response.json()
        stage = (state.get("stage"), state.get("progress"))
        if stage != last_stage:
            print(f"[{state.get('progress', 0):>3}%] {state.get('stage')}")
            last_stage = stage
        if state.get("status") in {"COMPLETED", "FAILED"}:
            break
        time.sleep(1.0)
    else:
        raise TimeoutError(f"Пайплайн не завершился за {args.timeout} с")

    if state.get("status") != "COMPLETED":
        error_log = run_root / "pipeline_error.log"
        details = error_log.read_text(encoding="utf-8") if error_log.exists() else ""
        raise AssertionError(
            "Реальный пайплайн завершился ошибкой:\n"
            + json.dumps(state, ensure_ascii=False, indent=2)
            + ("\n\nTraceback:\n" + details if details else "")
        )

    required_artifacts = [
        run_root / f"result_{project_id}.xlsx",
        run_root / "items_final.json",
        run_root / "requirements.json",
        run_root / "shortlist.json",
        run_root / "run_result.json",
        run_root / "preflight.json",
    ]
    missing = [str(path) for path in required_artifacts if not path.exists()]
    assert not missing, "Не сформированы артефакты: " + ", ".join(missing)

    equipment = client.get(f"/api/projects/{project_id}/equipment")
    assert equipment.status_code == 200, equipment.text
    total = equipment.json()["counters"]["total"]
    if args.expected_items >= 0:
        assert total == args.expected_items, equipment.json()

    detail = client.get(f"/api/projects/{project_id}/equipment/К6?limit=8")
    assert detail.status_code == 200, detail.text

    downloads = client.get(f"/api/projects/{project_id}/downloads")
    assert downloads.status_code == 200, downloads.text
    assert downloads.json().get("groups"), downloads.json()
    assert all(row.get("display_name") for row in downloads.json().get("files", []))

    print(
        "OK: real web flow completed; "
        f"answered={answered}; skipped={skipped}; equipment tags={total}"
    )
    print(f"Schemes: {schemes_dir}")
    print(f"Passports: {passports_dir}")
    print(f"Template: {template_path}")
    print(f"Answers: {answers_path}")
    print(f"Artifacts: {run_root}")
    if check.get("warnings"):
        print("Preflight warnings:")
        for warning in check["warnings"]:
            print(f"  - {warning}")

    if args.clean:
        shutil.rmtree(project_root, ignore_errors=True)
        shutil.rmtree(run_root, ignore_errors=True)
        print("Test artifacts removed (--clean).")


if __name__ == "__main__":
    main()
