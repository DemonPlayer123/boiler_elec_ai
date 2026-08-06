from __future__ import annotations

import importlib
import json
import os
import shutil
import threading
import time
import traceback
import zipfile
from datetime import datetime
from pathlib import Path
from typing import Any, Callable

from .project_service import ProjectService, ProjectValidationError

ProgressCallback = Callable[[str, int], None]


class PipelineBridge:
    """Bridge between the web wizard and the existing VКР pipeline.

    Modes:
    - ``real``: import and call the existing project modules;
    - ``demo``: copy bundled result artifacts so the UI flow can be tested;
    - ``auto``: use real modules when available, otherwise demo.
    """

    def __init__(self, service: ProjectService, demo_runs_root: str | Path | None = None) -> None:
        self.service = service
        self.demo_runs_root = Path(demo_runs_root).resolve() if demo_runs_root else None
        self.mode = os.getenv("BOILER_ELEC_PIPELINE_MODE", "auto").strip().lower()
        repo_root = Path(__file__).resolve().parents[2]
        self.norms_dir = Path(
            os.getenv("BOILER_ELEC_NORMS_DIR", str(repo_root / "data" / "norms"))
        ).resolve()
        self._threads: dict[str, threading.Thread] = {}
        self._lock = threading.RLock()
        self.max_concurrent_runs = max(1, int(os.getenv("BOILER_ELEC_MAX_CONCURRENT_RUNS", "1")))
        self._run_slots = threading.Semaphore(self.max_concurrent_runs)

    @staticmethod
    def _import_attr(module_name: str, attr_name: str) -> Any:
        module = importlib.import_module(module_name)
        return getattr(module, attr_name)

    def real_available(self) -> bool:
        try:
            self._import_attr("src.pipeline.run_pipeline", "run_pipeline")
            self._import_attr("src.extract.passports.passport_parser", "parse_passports_dir")
            self._import_attr("src.extract.schemes.scheme_parser", "parse_schemes_to_registry")
            return True
        except (ImportError, AttributeError):
            return False

    def resolved_mode(self) -> str:
        if self.mode == "real":
            if not self.real_available():
                raise RuntimeError(
                    "Режим real выбран, но модули src.pipeline и src.extract не найдены. "
                    "Поместите src/web в основной репозиторий проекта."
                )
            return "real"
        if self.mode == "demo":
            return "demo"
        return "real" if self.real_available() else "demo"

    def scan_schemes(self, project_id: str) -> dict[str, Any]:
        self.service.set_scheme_scanning(project_id)
        try:
            mode = self.resolved_mode()
            if mode == "real":
                parse_schemes = self._import_attr(
                    "src.extract.schemes.scheme_parser", "parse_schemes_to_registry"
                )
                registry = parse_schemes(self.service.upload_paths(project_id)["schemes"])
            else:
                source = self._demo_source_dir()
                registry = self._read_first_json(
                    source, ("equipment_registry.json", "equipment_registry(2).json"), []
                ) if source else []
            if not isinstance(registry, list):
                raise RuntimeError("Парсер схем вернул неожиданный формат")
            return self.service.complete_scheme_scan(project_id, registry, mode)
        except Exception as exc:
            self.service.fail_scheme_scan(project_id, str(exc))
            raise

    def preprocess(self, project_id: str) -> dict[str, Any]:
        self.service.set_preprocessing(project_id)
        try:
            mode = self.resolved_mode()
            if mode == "real":
                payload = self._preprocess_real(project_id)
            else:
                payload = self._preprocess_demo(project_id)
            return self.service.complete_preprocess(project_id, mode=mode, **payload)
        except Exception as exc:
            self.service.fail(project_id, str(exc))
            raise

    def _preprocess_real(self, project_id: str) -> dict[str, Any]:
        paths = self.service.upload_paths(project_id)
        parse_schemes = self._import_attr(
            "src.extract.schemes.scheme_parser", "parse_schemes_to_registry"
        )
        parse_passports = self._import_attr(
            "src.extract.passports.passport_parser", "parse_passports_dir"
        )

        registry = parse_schemes(paths["schemes"])
        passport_result = parse_passports(paths["passports"])
        if not isinstance(registry, list):
            raise RuntimeError("Парсер схем вернул неожиданный формат")

        # Текущий passport_parser проекта возвращает (items, parsed).
        # Поддерживаем также будущий вариант, где функция возвращает только parsed.
        if isinstance(passport_result, tuple) and len(passport_result) == 2:
            items, passports = passport_result
        else:
            items, passports = [], passport_result
        if not isinstance(passports, list) or not isinstance(items, list):
            raise RuntimeError("Парсер паспортов вернул неожиданный формат")
        return {
            "registry": registry,
            "passports": passports,
            "items": items,
            "notes": [
                f"В схемах найдено сущностей: {len(registry)}. Паспортных записей: {len(passports)}."
            ],
        }

    def _preprocess_demo(self, project_id: str) -> dict[str, Any]:
        notes = [
            "Сервер работает в демонстрационном режиме: реальные модули парсинга не обнаружены. "
            "После переноса src/web в основной репозиторий режим auto переключится на реальный пайплайн."
        ]
        passports: list[dict[str, Any]] = []
        registry: list[dict[str, Any]] = []
        items: list[dict[str, Any]] = []
        source = self._demo_source_dir()
        if source:
            passports = self._read_first_json(
                source,
                ("passports_parsed.json", "passports_parsed(2).json", "passports_items_final.json", "passports_items_final(2).json"),
                [],
            )
            registry = self._read_first_json(source, ("equipment_registry.json", "equipment_registry(2).json"), [])
            items = self._read_first_json(source, ("items_final.json", "items_final(1).json"), [])
            notes.append(
                f"Для проверки интерфейса загружен демонстрационный набор: паспортов {len(passports)}, ЭП {len(items)}."
            )
        return {"registry": registry, "passports": passports, "items": items, "notes": notes}

    def _demo_source_dir(self) -> Path | None:
        project = os.getenv("BOILER_ELEC_DEMO_PROJECT", "25-05")
        candidates = []
        if self.demo_runs_root:
            candidates.append(self.demo_runs_root / project)
        candidates.append(self.service.runs_root / project)
        for candidate in candidates:
            if candidate.exists() and candidate.is_dir():
                return candidate
        return None

    @staticmethod
    def _read_first_json(directory: Path, names: tuple[str, ...], default: Any) -> Any:
        for name in names:
            path = directory / name
            if path.exists():
                try:
                    return json.loads(path.read_text(encoding="utf-8"))
                except (OSError, ValueError):
                    continue
        return default

    def preflight(self, project_id: str) -> dict[str, Any]:
        """Validate the selected execution mode and project files before a run.

        Critical problems block the run. Warnings are shown to the user but do
        not prevent execution because norms and the technical catalog may be
        intentionally omitted during interface development.
        """
        critical: list[str] = []
        warnings: list[str] = []
        details: dict[str, Any] = {}

        try:
            mode = self.resolved_mode()
        except Exception as exc:
            mode = self.mode
            critical.append(str(exc))

        paths = self.service.upload_paths(project_id)
        schemes = sorted(paths["schemes"].glob("*.pdf"))
        passports = sorted(paths["passports"].glob("*.pdf"))
        templates = sorted(paths["template"].glob("*.xlsx"))
        user_inputs = self.service.run_dir(project_id) / "user_inputs.json"

        if not schemes:
            critical.append("Не загружены PDF-схемы")
        if not passports:
            critical.append("Не загружены PDF-паспорта")
        if not templates:
            critical.append("Не загружен XLSX-шаблон")
        elif len(templates) > 1:
            warnings.append(
                f"Загружено шаблонов XLSX: {len(templates)}. Будет использован {templates[0].name}."
            )

        if not user_inputs.exists():
            warnings.append(
                "user_inputs.json ещё не сформирован. Запуск допустим, но ручные уточнения не будут применены."
            )

        if mode == "real":
            if not self.real_available():
                critical.append(
                    "Не найдены реальные модули src.pipeline.run_pipeline и src.extract."
                )
            if not self.norms_dir.exists():
                warnings.append(
                    f"Каталог нормативных документов не найден: {self.norms_dir}. RAG-результаты могут быть пустыми."
                )
            try:
                module = importlib.import_module("src.pipeline.run_pipeline")
                module_path = Path(module.__file__).resolve()
                repo_root = module_path.parents[2]
                catalog_path = repo_root / "data" / "catalogs" / "catalog_metadata.json"
                details["repo_root"] = str(repo_root)
                details["catalog_metadata"] = str(catalog_path)
                if not catalog_path.exists() and not (self.service.run_dir(project_id) / "catalog_normalized.json").exists():
                    warnings.append(
                        "Не найден технический каталог data/catalogs/catalog_metadata.json. "
                        "Пайплайн выполнится, но список кандидатов может оказаться пустым."
                    )
            except Exception as exc:
                critical.append(f"Ошибка импорта реального пайплайна: {type(exc).__name__}: {exc}")
        else:
            source = self._demo_source_dir()
            if source is None:
                critical.append("Не найден демонстрационный проект с результатами")
            else:
                details["demo_source"] = str(source)

        out_dir = self.service.run_dir(project_id)
        try:
            out_dir.mkdir(parents=True, exist_ok=True)
            probe = out_dir / ".write_test"
            probe.write_text("ok", encoding="utf-8")
            probe.unlink(missing_ok=True)
        except OSError as exc:
            critical.append(f"Каталог результатов недоступен для записи: {exc}")

        payload = {
            "project_id": project_id,
            "mode": mode,
            "ok": not critical,
            "critical": critical,
            "warnings": warnings,
            "files": {
                "schemes": len(schemes),
                "passports": len(passports),
                "templates": len(templates),
                "user_inputs": user_inputs.exists(),
            },
            "paths": {
                "schemes": str(paths["schemes"]),
                "passports": str(paths["passports"]),
                "template": str(paths["template"]),
                "out_dir": str(out_dir),
                "norms_dir": str(self.norms_dir),
            },
            "details": details,
        }
        working = self.service.project_dir(project_id) / "working"
        working.mkdir(parents=True, exist_ok=True)
        (working / "preflight.json").write_text(
            json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        return payload

    def _archive_and_clean_output(self, project_id: str) -> Path | None:
        """Archive the previous top-level run and remove stale artifacts before rerun."""
        out_dir = self.service.run_dir(project_id)
        out_dir.mkdir(parents=True, exist_ok=True)
        preserved = {"user_inputs.json"}
        candidates = [
            path for path in out_dir.iterdir()
            if path.is_file()
            and path.name not in preserved
            and not path.name.startswith("previous_run_")
        ]
        archive_path: Path | None = None
        if candidates:
            stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
            archive_path = out_dir / f"previous_run_{stamp}.zip"
            with zipfile.ZipFile(archive_path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
                for path in candidates:
                    archive.write(path, arcname=path.name)
            for path in candidates:
                path.unlink(missing_ok=True)
        return archive_path

    def start_run(self, project_id: str) -> dict[str, Any]:
        check = self.preflight(project_id)
        if not check["ok"]:
            raise ProjectValidationError("Предварительная проверка не пройдена: " + "; ".join(check["critical"]))
        with self._lock:
            existing = self._threads.get(project_id)
            if existing and existing.is_alive():
                raise ProjectValidationError("Пайплайн уже выполняется")
            archive = self._archive_and_clean_output(project_id)
            self.service.set_running(project_id)
            if archive is not None:
                self.service.append_event(
                    project_id,
                    "bot",
                    f"Предыдущий результат сохранён в {archive.name}.",
                    "run_archived",
                )
            thread = threading.Thread(
                target=self._run_worker,
                args=(project_id,),
                daemon=True,
                name=f"boiler-elec-{project_id}",
            )
            self._threads[project_id] = thread
            thread.start()
        return self.service.get_state(project_id)

    def _animate_progress(self, project_id: str, worker: threading.Thread) -> None:
        """Coarse progress heartbeat while the legacy monolithic pipeline runs.

        The existing run_pipeline function has no callback contract yet, so this
        only communicates the current coarse stage and deliberately stops at 90%.
        Completion remains controlled exclusively by the actual worker result.
        """
        phases = [
            (4, "Подготовка входных данных", 48),
            (7, "Чтение схем и паспортов", 55),
            (8, "Сопоставление оборудования", 63),
            (10, "Проверка расчётных параметров", 71),
            (12, "Подбор аппаратов защиты", 79),
            (14, "Нормативный поиск и проверка", 86),
            (16, "Формирование итоговых файлов", 90),
        ]
        for delay, stage, progress in phases:
            waited = 0.0
            while worker.is_alive() and waited < delay:
                time.sleep(0.5)
                waited += 0.5
            if not worker.is_alive():
                return
            try:
                self.service.update_progress(project_id, stage, progress)
            except Exception:
                return

    def _run_worker(self, project_id: str) -> None:
        acquired = self._run_slots.acquire(blocking=False)
        if not acquired:
            self.service.update_progress(project_id, "В очереди на расчёт", 43)
            self._run_slots.acquire()
        try:
            self.service.update_progress(project_id, "Подготовка входных данных", 46)
            worker = threading.current_thread()
            animator = threading.Thread(
                target=self._animate_progress,
                args=(project_id, worker),
                daemon=True,
                name=f"boiler-elec-progress-{project_id}",
            )
            animator.start()
            mode = self.resolved_mode()
            if mode == "real":
                result = self._run_real(project_id)
            else:
                result = self._run_demo(project_id)
            self.service.complete_run(project_id, result, mode)
        except Exception as exc:
            error_text = traceback.format_exc()
            try:
                out_dir = self.service.run_dir(project_id)
                out_dir.mkdir(parents=True, exist_ok=True)
                (out_dir / "pipeline_error.log").write_text(error_text, encoding="utf-8")
            except OSError:
                pass
            self.service.fail(project_id, f"{type(exc).__name__}: {exc}")
        finally:
            self._run_slots.release()

    def _run_real(self, project_id: str) -> dict[str, Any]:
        run_pipeline = self._import_attr("src.pipeline.run_pipeline", "run_pipeline")
        paths = self.service.upload_paths(project_id)
        templates = sorted(paths["template"].glob("*.xlsx"))
        if not templates:
            raise RuntimeError("Не найден XLSX-шаблон")
        out_dir = self.service.run_dir(project_id)
        out_dir.mkdir(parents=True, exist_ok=True)
        check = self.preflight(project_id)
        (out_dir / "preflight.json").write_text(
            json.dumps(check, ensure_ascii=False, indent=2), encoding="utf-8"
        )

        self.service.update_progress(project_id, "Извлечение и сопоставление оборудования", 50)
        result = run_pipeline(
            schemes_dir=paths["schemes"],
            passports_dir=paths["passports"],
            template_xlsx=templates[0],
            out_dir=out_dir,
            norms_dir=self.norms_dir if self.norms_dir.exists() else None,
            project_code=project_id,
        )
        self.service.update_progress(project_id, "Публикация результатов", 95)
        if not isinstance(result, dict):
            result = {"result": result}
        (out_dir / "run_result.json").write_text(
            json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        return result

    def _run_demo(self, project_id: str) -> dict[str, Any]:
        source = self._demo_source_dir()
        if source is None:
            raise RuntimeError("Не найден демонстрационный проект с результатами")
        destination = self.service.run_dir(project_id)
        destination.mkdir(parents=True, exist_ok=True)
        self.service.update_progress(project_id, "Демонстрация расчётного этапа", 60)
        for path in source.iterdir():
            if not path.is_file():
                continue
            if path.name == "user_inputs.json":
                continue
            if path.suffix.lower() not in {".json", ".csv", ".xlsx"}:
                continue
            target = destination / path.name
            if path.resolve() == target.resolve():
                continue
            shutil.copy2(path, target)
        self.service.update_progress(project_id, "Формирование сводного результата", 90)
        run_result = self._read_first_json(destination, ("run_result.json", "run_result(2).json"), {})
        if not isinstance(run_result, dict):
            run_result = {}
        run_result = {**run_result, "demo_mode": True, "project_id": project_id}
        (destination / "run_result.json").write_text(
            json.dumps(run_result, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        return run_result
