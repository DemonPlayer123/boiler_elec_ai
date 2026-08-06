from __future__ import annotations

import asyncio
import json
import os
import secrets
import socket
import uuid
from pathlib import Path
from typing import Any

from fastapi import FastAPI, File, HTTPException, Query, Request, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field
from starlette.concurrency import run_in_threadpool

from .pipeline_bridge import PipelineBridge
from .project_service import (
    ProjectConflictError,
    ProjectNotFoundError as SessionProjectNotFoundError,
    ProjectService,
    ProjectValidationError,
)
from .repository import InvalidProjectIdError, ProjectNotFoundError, ProjectResultRepository
from .sharing import ShareError, ShareService

BASE_DIR = Path(__file__).resolve().parent
STATIC_DIR = BASE_DIR / "static"
RUNS_ROOT = Path(os.getenv("BOILER_ELEC_RUNS_ROOT", "data/output/runs"))
PROJECTS_ROOT = Path(os.getenv("BOILER_ELEC_PROJECTS_ROOT", "data/projects"))
DEMO_RUNS_ROOT = Path(os.getenv("BOILER_ELEC_DEMO_RUNS_ROOT", "demo_data/runs"))
PUBLIC_BASE_URL = os.getenv("BOILER_ELEC_PUBLIC_BASE_URL", "").strip().rstrip("/")
SHARE_ADMIN_TOKEN = os.getenv("BOILER_ELEC_SHARE_ADMIN_TOKEN", "").strip()

app = FastAPI(title="Boiler Elec AI Web", version="0.5.0")
repository = ProjectResultRepository(RUNS_ROOT)
project_service = ProjectService(PROJECTS_ROOT, RUNS_ROOT)
pipeline_bridge = PipelineBridge(project_service, DEMO_RUNS_ROOT)
share_service = ShareService(PROJECTS_ROOT)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["GET", "POST", "DELETE", "PATCH"],
    allow_headers=["*"],
)
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")


class ProjectCreateRequest(BaseModel):
    project_id: str | None = Field(default=None, max_length=80)
    overwrite: bool = False
    invite_token: str | None = Field(default=None, max_length=200)


class QuestionAnswerRequest(BaseModel):
    value: Any = None
    action: str = "answer"
    comment: str | None = Field(default=None, max_length=1000)
    replace: bool = False


class ChatRequest(BaseModel):
    message: str = Field(min_length=1, max_length=2000)


class InviteCreateRequest(BaseModel):
    label: str | None = Field(default=None, max_length=160)
    expires_hours: int = Field(default=168, ge=1, le=2160)
    max_uses: int = Field(default=25, ge=1, le=1000)


FRIENDLY_FILES: dict[str, tuple[str, str, str, int]] = {
    "result_25-05.xlsx": ("Итоговый расчёт нагрузок и выбор аппаратов.xlsx", "Основной итоговый Excel проекта", "Основные результаты", 0),
    "audit_log.csv": ("Журнал изменений и проверок.csv", "Аудит действий и записи в расчётный шаблон", "Основные результаты", 1),
    "run_result.json": ("Сводка выполнения проекта.json", "Количество обработанных сущностей и пути результатов", "Основные результаты", 2),
    "user_inputs.json": ("Ответы пользователя HITL.json", "Ручные уточнения, введённые инженером", "Исходные и расчётные данные", 10),
    "equipment_registry.json": ("Реестр оборудования по схемам.json", "Оборудование, обнаруженное в исходных схемах", "Исходные и расчётные данные", 11),
    "passports_parsed.json": ("Извлечённые данные паспортов.json", "Результаты разбора паспортов оборудования", "Исходные и расчётные данные", 12),
    "passports_items_final.json": ("Уточнённые паспортные данные.json", "Паспортные данные после ручных уточнений", "Исходные и расчётные данные", 13),
    "items_final.json": ("Итоговые параметры электроприёмников.json", "Расчётные параметры всех ЭП", "Исходные и расчётные данные", 14),
    "entity_links.json": ("Сопоставление схем и паспортов.json", "Связи между тегами и паспортами", "Проверки и классификация", 20),
    "classification_report.json": ("Классификация электроприёмников.json", "Классы и типы нагрузок", "Проверки и классификация", 21),
    "consistency_report.json": ("Проверка согласованности исходных данных.json", "Замечания по параметрам и источникам", "Проверки и классификация", 22),
    "ai_consistency_review.json": ("Инженерный обзор согласованности.json", "Расширенное объяснение замечаний", "Проверки и классификация", 23),
    "requirements.json": ("Требования к аппаратам защиты.json", "Расчётные номиналы, полюса, характеристики и классы", "Подбор аппаратов", 30),
    "candidates.json": ("Полный список кандидатов.json", "Все найденные аппараты до сокращения списка", "Подбор аппаратов", 31),
    "shortlist.json": ("Рекомендуемые аппараты по тегам.json", "Ранжированный список вариантов по каждому ЭП", "Подбор аппаратов", 32),
    "catalog_with_prices_grouped.json": ("Кандидаты с ценами и артикулами.json", "Shortlist, дополненный официальными ценами", "Подбор аппаратов", 33),
    "retrieved_chunks.json": ("Найденные нормативные фрагменты RAG.json", "Полные фрагменты нормативных документов", "Нормативное обоснование", 40),
    "rag_summary.json": ("Краткое нормативное обоснование.json", "Сводное объяснение выбора по каждому тегу", "Нормативное обоснование", 41),
    "normative_review.json": ("Нормативная проверка решений.json", "Инженерная проверка по нормативным фрагментам", "Нормативное обоснование", 42),
    "normative_review_llm.json": ("LLM-объяснение нормативной проверки.json", "Объяснение результатов нормативного анализа", "Нормативное обоснование", 43),
    "normative_review_openai.json": ("Расширенная проверка OpenAI.json", "Дополнительная проверка кандидатов и обоснований", "Нормативное обоснование", 44),
    "preflight.json": ("Предварительная проверка запуска.json", "Готовность входных данных и окружения", "Служебные файлы", 90),
    "pipeline_error.log": ("Журнал ошибки пайплайна.txt", "Полная техническая диагностика ошибки", "Служебные файлы", 91),
}


def _friendly_file(path: Path) -> dict[str, Any]:
    name = path.name
    key = name
    if name.startswith("result_") and path.suffix.lower() == ".xlsx":
        meta = ("Итоговый расчёт нагрузок и выбор аппаратов.xlsx", "Основной итоговый Excel проекта", "Основные результаты", 0)
    elif name.startswith("previous_run_") and path.suffix.lower() == ".zip":
        stamp = name.removeprefix("previous_run_").removesuffix(".zip")
        meta = (f"Архив предыдущего расчёта {stamp}.zip", "Предыдущая версия всех результатов", "Архивы", 80)
    else:
        meta = FRIENDLY_FILES.get(key)
        if meta is None:
            stem = path.stem.replace("_", " ").strip().capitalize()
            meta = (f"{stem}{path.suffix}", "Дополнительный артефакт пайплайна", "Дополнительные материалы", 70)
    display_name, description, group, order = meta
    return {
        "name": name,
        "display_name": display_name,
        "description": description,
        "group": group,
        "order": order,
        "size": path.stat().st_size,
        "recommended": order < 10,
    }


def _session_error(exc: Exception) -> HTTPException:
    if isinstance(exc, ProjectValidationError):
        return HTTPException(status_code=403 if "ключ сессии" in str(exc) else 400, detail=str(exc))
    if isinstance(exc, ProjectConflictError):
        return HTTPException(status_code=409, detail=str(exc))
    if isinstance(exc, SessionProjectNotFoundError):
        return HTTPException(status_code=404, detail="Проект не найден")
    if isinstance(exc, ShareError):
        return HTTPException(status_code=400, detail=str(exc))
    return HTTPException(status_code=500, detail=str(exc))


def _provided_key(request: Request, key: str | None = None) -> str | None:
    return key or request.headers.get("X-Project-Key") or request.query_params.get("key")


def _authorize(project_id: str, request: Request, key: str | None = None) -> None:
    project_service.require_access(project_id, _provided_key(request, key))
    state = project_service.get_state(project_id)
    if SHARE_ADMIN_TOKEN and not state.get("shared_project") and not _is_local_request(request):
        provided_admin = request.headers.get("X-Share-Admin-Token") or request.query_params.get("admin")
        if provided_admin != SHARE_ADMIN_TOKEN:
            raise HTTPException(status_code=403, detail="Доступ к локальному проекту разрешён только владельцу")


def _require_share_admin(request: Request) -> None:
    if SHARE_ADMIN_TOKEN and request.headers.get("X-Share-Admin-Token") != SHARE_ADMIN_TOKEN:
        raise HTTPException(status_code=403, detail="Неверный административный токен публикации")


def _local_ip() -> str:
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
            sock.connect(("8.8.8.8", 80))
            return sock.getsockname()[0]
    except OSError:
        return "127.0.0.1"


def _is_local_request(request: Request) -> bool:
    host = request.client.host if request.client else ""
    return host in {"127.0.0.1", "::1", "localhost", "testclient"}


def _base_url(request: Request) -> str:
    if PUBLIC_BASE_URL:
        return PUBLIC_BASE_URL
    base = request.url
    host = base.hostname or "127.0.0.1"
    if host in {"127.0.0.1", "localhost", "::1"}:
        port = base.port or 80
        return f"http://{_local_ip()}:{port}"
    return str(request.base_url).rstrip("/")


@app.get("/", include_in_schema=False)
def index() -> FileResponse:
    return FileResponse(STATIC_DIR / "index.html")


@app.get("/api/health")
def health() -> dict[str, Any]:
    try:
        mode = pipeline_bridge.resolved_mode()
    except Exception as exc:
        mode = f"error: {exc}"
    return {
        "status": "ok",
        "runs_root": str(repository.runs_root),
        "projects_root": str(project_service.projects_root),
        "pipeline_mode": mode,
        "version": app.version,
        "public_base_url": PUBLIC_BASE_URL or None,
    }


@app.get("/api/network")
def network(request: Request) -> dict[str, Any]:
    local_ip = _local_ip()
    port = request.url.port or 80
    return {
        "local_url": f"http://127.0.0.1:{port}",
        "lan_url": f"http://{local_ip}:{port}",
        "public_url": PUBLIC_BASE_URL or None,
        "note": "Для доступа через интернет задайте BOILER_ELEC_PUBLIC_BASE_URL и опубликуйте порт через VPN, туннель или reverse proxy.",
    }


@app.post("/api/invites", status_code=201)
def create_invite(request: Request, payload: InviteCreateRequest) -> dict[str, Any]:
    _require_share_admin(request)
    invite = share_service.create(label=payload.label, expires_hours=payload.expires_hours, max_uses=payload.max_uses)
    invite["url"] = f"{_base_url(request)}/?invite={invite['token']}"
    return invite


@app.get("/api/invites/{token}")
def resolve_invite(token: str) -> dict[str, Any]:
    try:
        return share_service.resolve(token)
    except Exception as exc:
        raise _session_error(exc) from exc


@app.get("/api/projects")
def list_projects(request: Request) -> dict[str, Any]:
    if SHARE_ADMIN_TOKEN and not _is_local_request(request):
        return {"projects": []}
    return {"projects": project_service.list_projects()}


@app.post("/api/projects", status_code=201)
def create_project(payload: ProjectCreateRequest, request: Request) -> dict[str, Any]:
    try:
        if payload.invite_token:
            share_service.consume(payload.invite_token)
            project_id = payload.project_id or f"shared-{uuid.uuid4().hex[:12]}"
            access_key = secrets.token_urlsafe(24)
            return project_service.create_project(
                project_id,
                overwrite=False,
                access_key=access_key,
                shared_invite=payload.invite_token,
            )
        if payload.overwrite:
            _require_share_admin(request)
        return project_service.create_project(payload.project_id, overwrite=payload.overwrite)
    except Exception as exc:
        raise _session_error(exc) from exc


@app.get("/api/projects/{project_id}/state")
def get_project_state(project_id: str, request: Request) -> dict[str, Any]:
    try:
        _authorize(project_id, request)
        return project_service.get_state(project_id)
    except Exception as exc:
        raise _session_error(exc) from exc


@app.get("/api/projects/{project_id}/dialog")
def get_project_dialog(project_id: str, request: Request) -> dict[str, Any]:
    try:
        _authorize(project_id, request)
        return project_service.dialog(project_id)
    except Exception as exc:
        raise _session_error(exc) from exc


@app.post("/api/projects/{project_id}/chat")
def chat(project_id: str, payload: ChatRequest, request: Request) -> dict[str, Any]:
    try:
        _authorize(project_id, request)
        return project_service.chat_reply(project_id, payload.message)
    except Exception as exc:
        raise _session_error(exc) from exc


@app.get("/api/projects/{project_id}/files")
def list_project_files(project_id: str, request: Request) -> dict[str, Any]:
    try:
        _authorize(project_id, request)
        return {"project_id": project_id, "files": project_service.list_uploads(project_id)}
    except Exception as exc:
        raise _session_error(exc) from exc


@app.delete("/api/projects/{project_id}/files/{file_id}")
def delete_project_file(project_id: str, file_id: str, request: Request) -> dict[str, Any]:
    try:
        _authorize(project_id, request)
        return project_service.delete_upload(project_id, file_id)
    except Exception as exc:
        raise _session_error(exc) from exc


@app.post("/api/projects/{project_id}/files")
async def upload_files(
    project_id: str,
    request: Request,
    category: str = Query(..., pattern="^(schemes|passports|template|additional)$"),
    document_type: str | None = Query(default=None, max_length=24),
    files: list[UploadFile] = File(...),
) -> dict[str, Any]:
    saved: list[dict[str, Any]] = []
    try:
        _authorize(project_id, request)
        for upload in files:
            content = await upload.read()
            saved.append(project_service.save_upload(
                project_id,
                category,
                upload.filename or "file",
                content,
                document_type=document_type,
            ))
        return {"project_id": project_id, "saved": saved, "state": project_service.get_state(project_id)}
    except Exception as exc:
        raise _session_error(exc) from exc
    finally:
        for upload in files:
            await upload.close()


@app.post("/api/projects/{project_id}/scan-schemes")
async def scan_schemes(project_id: str, request: Request) -> dict[str, Any]:
    try:
        _authorize(project_id, request)
        return await run_in_threadpool(pipeline_bridge.scan_schemes, project_id)
    except Exception as exc:
        raise _session_error(exc) from exc


@app.post("/api/projects/{project_id}/preprocess")
async def preprocess_project(project_id: str, request: Request) -> dict[str, Any]:
    try:
        _authorize(project_id, request)
        return await run_in_threadpool(pipeline_bridge.preprocess, project_id)
    except Exception as exc:
        raise _session_error(exc) from exc


@app.get("/api/projects/{project_id}/questions")
def list_questions(project_id: str, request: Request) -> dict[str, Any]:
    try:
        _authorize(project_id, request)
        return project_service.list_questions(project_id)
    except Exception as exc:
        raise _session_error(exc) from exc


@app.get("/api/projects/{project_id}/questions/next")
def next_question(project_id: str, request: Request) -> dict[str, Any]:
    try:
        _authorize(project_id, request)
        return {"project_id": project_id, "question": project_service.next_question(project_id)}
    except Exception as exc:
        raise _session_error(exc) from exc


@app.post("/api/projects/{project_id}/questions/{question_id}/answer")
def answer_question(project_id: str, question_id: str, payload: QuestionAnswerRequest, request: Request) -> dict[str, Any]:
    try:
        _authorize(project_id, request)
        return project_service.answer_question(
            project_id,
            question_id,
            value=payload.value,
            action=payload.action,
            comment=payload.comment,
            replace=payload.replace,
        )
    except Exception as exc:
        raise _session_error(exc) from exc


@app.post("/api/projects/{project_id}/questions/{question_id}/reset")
def reset_question(project_id: str, question_id: str, request: Request) -> dict[str, Any]:
    try:
        _authorize(project_id, request)
        return project_service.reset_question(project_id, question_id)
    except Exception as exc:
        raise _session_error(exc) from exc


@app.get("/api/projects/{project_id}/preflight")
def project_preflight(project_id: str, request: Request) -> dict[str, Any]:
    try:
        _authorize(project_id, request)
        return pipeline_bridge.preflight(project_id)
    except Exception as exc:
        raise _session_error(exc) from exc


@app.post("/api/projects/{project_id}/run", status_code=202)
def run_project(project_id: str, request: Request) -> dict[str, Any]:
    try:
        _authorize(project_id, request)
        return pipeline_bridge.start_run(project_id)
    except Exception as exc:
        raise _session_error(exc) from exc


@app.get("/api/projects/{project_id}/events")
async def project_events(project_id: str, request: Request, key: str | None = Query(default=None)) -> StreamingResponse:
    _authorize(project_id, request, key)
    project_service.get_state(project_id)

    async def stream():
        last_updated = None
        heartbeat = 0
        while True:
            try:
                dialog = project_service.dialog(project_id)
                updated = dialog.get("state", {}).get("updated_at")
                if updated != last_updated:
                    payload = json.dumps(dialog, ensure_ascii=False)
                    yield f"event: dialog\ndata: {payload}\n\n"
                    last_updated = updated
                    heartbeat = 0
                else:
                    heartbeat += 1
                    if heartbeat >= 15:
                        yield ": heartbeat\n\n"
                        heartbeat = 0
                await asyncio.sleep(1.0)
            except asyncio.CancelledError:
                break
            except Exception as exc:
                payload = json.dumps({"detail": str(exc)}, ensure_ascii=False)
                yield f"event: error\ndata: {payload}\n\n"
                break

    return StreamingResponse(stream(), media_type="text/event-stream", headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


@app.get("/api/projects/{project_id}/status")
def project_status(project_id: str, request: Request) -> dict[str, Any]:
    try:
        _authorize(project_id, request)
        return project_service.get_state(project_id)
    except Exception as exc:
        raise _session_error(exc) from exc


@app.get("/api/projects/{project_id}/downloads")
def list_downloads(project_id: str, request: Request) -> dict[str, Any]:
    try:
        _authorize(project_id, request)
        run_dir = project_service.run_dir(project_id)
        if not run_dir.exists():
            raise SessionProjectNotFoundError(project_id)
        allowed = {".xlsx", ".csv", ".json", ".log", ".txt", ".zip"}
        files = [_friendly_file(path) for path in run_dir.iterdir() if path.is_file() and path.suffix.lower() in allowed]
        files.sort(key=lambda row: (row["order"], row["display_name"].lower()))
        groups: dict[str, list[dict[str, Any]]] = {}
        for row in files:
            groups.setdefault(row["group"], []).append(row)
        return {"project_id": project_id, "files": files, "groups": groups}
    except Exception as exc:
        raise _session_error(exc) from exc


@app.get("/api/projects/{project_id}/download/{filename}")
def download_result(project_id: str, filename: str, request: Request, key: str | None = Query(default=None)) -> FileResponse:
    try:
        _authorize(project_id, request, key)
        safe_name = Path(filename).name
        if safe_name != filename:
            raise ProjectValidationError("Недопустимое имя файла")
        run_dir = project_service.run_dir(project_id)
        path = (run_dir / safe_name).resolve()
        if run_dir not in path.parents or not path.exists() or not path.is_file():
            raise SessionProjectNotFoundError(filename)
        if path.suffix.lower() not in {".xlsx", ".csv", ".json", ".log", ".txt", ".zip"}:
            raise ProjectValidationError("Скачивание этого типа файла запрещено")
        return FileResponse(path, filename=_friendly_file(path)["display_name"])
    except Exception as exc:
        raise _session_error(exc) from exc


@app.get("/api/projects/{project_id}/equipment")
def list_equipment(project_id: str, request: Request) -> dict[str, Any]:
    try:
        _authorize(project_id, request)
        return repository.list_equipment(project_id)
    except InvalidProjectIdError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except ProjectNotFoundError as exc:
        raise HTTPException(status_code=404, detail="Результаты проекта не найдены") from exc
    except (OSError, ValueError) as exc:
        raise HTTPException(status_code=500, detail=f"Ошибка чтения результатов: {exc}") from exc


@app.get("/api/projects/{project_id}/equipment/{tag}")
def get_equipment(project_id: str, tag: str, request: Request, limit: int = Query(default=8, ge=1, le=50)) -> dict[str, Any]:
    try:
        _authorize(project_id, request)
        result = repository.get_equipment(project_id, tag, limit=limit)
    except InvalidProjectIdError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except ProjectNotFoundError as exc:
        raise HTTPException(status_code=404, detail="Результаты проекта не найдены") from exc
    except (OSError, ValueError) as exc:
        raise HTTPException(status_code=500, detail=f"Ошибка чтения результатов: {exc}") from exc
    if result is None:
        raise HTTPException(status_code=404, detail="Тег не найден")
    return result
