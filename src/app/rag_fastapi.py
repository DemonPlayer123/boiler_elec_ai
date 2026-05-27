from __future__ import annotations

import html
import json
import re
import os
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

def _load_dotenv_file(path: str | Path = ".env") -> None:
    env_path = Path(path)

    if not env_path.exists():
        return

    for line in env_path.read_text(encoding="utf-8").splitlines():
        line = line.strip()

        if not line or line.startswith("#") or "=" not in line:
            continue

        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip('"').strip("'")

        if key and key not in os.environ:
            os.environ[key] = value


_load_dotenv_file()

from fastapi import FastAPI, Form
from fastapi.responses import HTMLResponse, JSONResponse

from src.engine.normative_review import build_normative_review

from src.engine.normative_qdrant_store import NormativeQdrantStore

from src.ai.grok_critic import (
    call_grok_critic,
    load_saved_grok_critic,
    save_grok_critic_result,
)

from concurrent.futures import ThreadPoolExecutor

from src.ai.enrich_normative_review_openai import enrich_normative_review
from src.ai.review_miner_openai import enrich_reviews
from src.ai.price_miner_openai import enrich_prices

from fastapi.middleware.cors import CORSMiddleware



APP_TITLE = "Boiler Elec AI — Normative RAG"


def _load_json(path: str | Path) -> Any:
    return json.loads(Path(path).read_text(encoding="utf-8"))

def _load_json_or(path: str | Path, default: Any) -> Any:
    path = Path(path)
    if not path.exists():
        return default
    return json.loads(path.read_text(encoding="utf-8"))


def _save_json(path: str | Path, data: Any) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def _upsert_row_by_tag(path: Path, new_row: dict) -> None:
    tag = str(new_row.get("tag") or "").strip().upper()
    if not tag:
        return

    rows = _load_json_or(path, [])
    if not isinstance(rows, list):
        rows = []

    out = []
    replaced = False

    for row in rows:
        row_tag = str((row or {}).get("tag") or "").strip().upper()
        if row_tag == tag:
            out.append(new_row)
            replaced = True
        else:
            out.append(row)

    if not replaced:
        out.append(new_row)

    _save_json(path, out)

SHORTLIST_PATH = Path("data/output/runs/25-05/shortlist.json")
REQUIREMENTS_PATH = Path("data/output/runs/25-05/requirements.json")
CORPUS_PATH = Path("data/norms/normative_corpus.json")
QDRANT_PATH = Path("data/norms/qdrant_store")
QDRANT_COLLECTION = "normative_chunks"
EMBEDDING_MODEL = "models/Frida"

OPENAI_NORMATIVE_REVIEW_PATH = Path("data/output/runs/25-05/normative_review_openai.json")
OPENAI_CANDIDATE_REVIEWS_PATH = Path("data/output/runs/25-05/candidate_reviews_openai.json")
OPENAI_CANDIDATE_PRICES_PATH = Path("data/output/runs/25-05/candidate_prices_openai.json")

MERGED_CATALOG_WITH_PRICES_PATH = Path("data/output/runs/25-05/catalog_with_prices_grouped.json")

MAX_CANDIDATES_TO_RENDER = 5

RUN_DIR = Path("data/output/runs/25-05")

GROK_CRITIC_ENABLED = os.getenv("GROK_CRITIC_ENABLED", "1") == "1"
GROK_CRITIC_EVERY_REQUEST = os.getenv("GROK_CRITIC_EVERY_REQUEST", "0") == "1"
GROK_CRITIC_MODEL = os.getenv("GROK_CRITIC_MODEL", "grok-4.3")
GROK_CRITIC_REASONING = os.getenv("GROK_CRITIC_REASONING", "medium")

OPENAI_NORMATIVE_MODEL = os.getenv("OPENAI_NORMATIVE_MODEL", "gpt-5.4-mini")
OPENAI_REVIEW_MODEL = os.getenv("OPENAI_REVIEW_MODEL", "gpt-5.4-mini")
OPENAI_PRICE_MODEL = os.getenv("OPENAI_PRICE_MODEL", "gpt-5.4-mini")
OPENAI_MAX_CANDIDATES_PER_TAG = int(os.getenv("OPENAI_MAX_CANDIDATES_PER_TAG", "5"))

PRICE_FIELDS = [
    "price_found",
    "price_match_type",
    "price_vendor",
    "price_article",
    "price_designation",
    "price_title",
    "price_rub",
    "price_currency",
    "price_source_domain",
    "price_source_type",
    "price_product_url",
    "price_match_key",
    "price_source_name",
    "price_url_note",
    "price_comment",
]

QDRANT_STORE: NormativeQdrantStore | None = None

def _pretty_source_name(
    source_url: str,
    source_domain: str,
    source_type: str,
    vendor: str = "",
) -> str:
    vendor_norm = str(vendor or "").strip().upper()
    domain = str(source_domain or "").strip().lower()
    url = str(source_url or "").strip()
    source_type_norm = str(source_type or "").strip().lower()

    if not domain and url:
        try:
            parsed = urlparse(url)
            domain = (parsed.netloc or "").lower()
        except Exception:
            domain = ""

    if domain.startswith("www."):
        domain = domain[4:]

    if domain:
        return domain

    # fallback по вендору, если это официальный источник, но домен не передали
    if source_type_norm in {"official", "official_site", "official_site_listing"}:
        if vendor_norm == "CHINT":
            return "chint.ru"
        if vendor_norm == "DEKRAFT":
            return "dek.ru"
        if vendor_norm == "KEAZ":
            return "keaz.ru"

    return "не указан"

def _looks_like_real_article(value: str) -> bool:
    s = str(value or "").strip()
    if not s:
        return False

    s_lower = s.lower()

    # Явный мусор / единицы измерения / служебные значения
    bad_exact = {
        "шт",
        "шт.",
        "1 шт",
        "1 шт.",
        "компл",
        "компл.",
        "комплект",
        "уп",
        "уп.",
        "—",
        "-",
    }
    if s_lower in bad_exact:
        return False

    bad_words = [
        "выключатель",
        "авт. выкл",
        "автомат",
        "защиты двигателя",
        "защиты эл. двигателя",
        "х-ка",
        "характеристика",
        "серия",
    ]
    if any(word in s_lower for word in bad_words):
        return False

    # Слишком длинная человекочитаемая строка — почти наверняка не артикул
    if len(s) > 40:
        return False

    # Если есть много пробелов — обычно это уже название, а не артикул
    if s.count(" ") >= 2:
        return False

    # В артикуле обычно есть цифры или смесь букв/цифр
    has_digit = any(ch.isdigit() for ch in s)
    has_alpha = any(ch.isalpha() for ch in s)

    # Только буквы без цифр и без спецсимволов — подозрительно
    if has_alpha and not has_digit and len(s) <= 6:
        return False

    return True


def _normalize_article(value: Any) -> str:
    s = str(value or "").strip()
    if not s:
        return ""

    if _looks_like_real_article(s):
        return s

    return ""

def _normalize_designation(value: Any) -> str:
    s = str(value or "").strip()
    if not s:
        return ""

    s_lower = s.lower()
    bad_exact = {"шт", "шт.", "компл", "компл.", "—", "-"}
    if s_lower in bad_exact:
        return ""

    return s

def _normalize_price_number(value: Any) -> float | None:
    if value in (None, ""):
        return None

    if isinstance(value, (int, float)):
        return float(value)

    s = str(value).strip()
    if not s:
        return None

    s = (
        s.replace("\u00a0", " ")
         .replace("₽", "")
         .replace("RUB", "")
         .replace("rub", "")
         .strip()
    )

    # убираем пробелы-разделители тысяч
    s = s.replace(" ", "")

    # десятичную запятую переводим в точку
    s = s.replace(",", ".")

    try:
        return float(s)
    except Exception:
        return None


def _is_official_source(source_type: str | None) -> bool:
    s = str(source_type or "").strip().lower()
    return s in {"official", "official_site", "official_site_listing", "official_site_json"}

def _render_page(body: str) -> str:
    return f"""
<!doctype html>
<html lang="ru">
<head>
  <meta charset="utf-8"/>
  <title>{APP_TITLE}</title>
  <style>
    body {{
      font-family: Arial, sans-serif;
      max-width: 1100px;
      margin: 32px auto;
      padding: 0 16px;
      line-height: 1.5;
      background: #f7f7f8;
      color: #222;
    }}
    h1, h2, h3 {{
      margin-bottom: 0.4em;
    }}
    .card {{
      background: #fff;
      border-radius: 12px;
      padding: 18px 20px;
      margin: 16px 0;
      box-shadow: 0 2px 10px rgba(0,0,0,0.06);
    }}
    .muted {{
      color: #666;
      font-size: 0.95rem;
    }}
    .ok {{ color: #0a7a2f; font-weight: bold; }}
    .warn {{ color: #9a6b00; font-weight: bold; }}
    .bad {{ color: #a40000; font-weight: bold; }}
    input[type="text"] {{
      width: 260px;
      padding: 10px;
      font-size: 16px;
    }}
    button {{
      padding: 10px 14px;
      font-size: 16px;
      cursor: pointer;
    }}
    ul {{
      padding-left: 20px;
    }}
    pre {{
      white-space: pre-wrap;
      word-break: break-word;
      background: #fafafa;
      padding: 12px;
      border-radius: 8px;
    }}
    .grid {{
      display: grid;
      grid-template-columns: 1fr 1fr;
      gap: 16px;
    }}
  </style>
</head>
<body>
  <h1>{APP_TITLE}</h1>
  {body}
</body>
</html>
"""


def _verdict_class(verdict: str) -> str:
    if verdict == "supported":
        return "ok"
    if verdict == "supported_with_conditions":
        return "warn"
    return "bad"


def _render_form(default_tag: str = "") -> str:
    return f"""
<div class="card">
  <form method="post" action="/check">
    <label for="tag"><b>Тег ЭП:</b></label><br/>
    <input type="text" id="tag" name="tag" value="{html.escape(default_tag)}" placeholder="Например: К6, ХВО, ГГ.1"/>
    <button type="submit">Проверить</button>
  </form>
  <p class="muted">Доступны теги вроде: К4, К6, К7, К8, К9, ХВО, ГГ.1, А1 и т.д.</p>
</div>
"""


def _find_row_by_tag(rows: list[dict], tag: str) -> dict | None:
    tag_norm = (tag or "").strip().upper()
    for row in rows:
        if str(row.get("tag") or "").strip().upper() == tag_norm:
            return row
    return None
  
def _build_index_by_tag(rows: list[dict]) -> dict[str, dict]:
    out: dict[str, dict] = {}
    for row in rows:
        tag = str(row.get("tag") or "").strip().upper()
        if tag:
            out[tag] = row
    return out
  
def _build_prices_index_by_tag(rows: list[dict]) -> dict[str, list[dict]]:
    out: dict[str, list[dict]] = {}
    for row in rows:
        tag = str(row.get("tag") or "").strip().upper()
        prices = row.get("prices") or []
        if tag and isinstance(prices, list):
            out[tag] = prices
    return out

def _build_catalog_index_by_tag(rows: list[dict]) -> dict[str, dict]:
    out: dict[str, dict] = {}
    for row in rows:
        tag = str(row.get("tag") or "").strip().upper()
        if tag:
            out[tag] = row
    return out

def _normalize_price_text(price_row: dict | None) -> str:
    if not price_row:
        return "—"

    if not price_row.get("price_found"):
        return "—"

    raw_value = price_row.get("price_rub")
    if raw_value in (None, ""):
        raw_value = price_row.get("price_value")

    value = _normalize_price_number(raw_value)
    if value is None:
        return "—"

    currency = str(price_row.get("price_currency") or price_row.get("currency") or "RUB").strip()

    text = f"{value:,.2f}".replace(",", "X").replace(".", ",").replace("X", " ")
    return f"{text} {currency}".strip()

def _normalize_candidate_price_text(candidate: dict | None) -> str:
    if not candidate:
        return "—"

    if not candidate.get("price_found"):
        return "—"

    value = _normalize_price_number(candidate.get("price_rub"))
    if value is None:
        return "—"

    currency = str(candidate.get("price_currency") or "RUB").strip()
    text = f"{value:,.2f}".replace(",", "X").replace(".", ",").replace("X", " ")
    return f"{text} {currency}".strip()


def _render_candidate_price_source(candidate: dict | None) -> str:
    if not candidate:
        return "—"

    if not candidate.get("price_found"):
        return "—"

    source_domain = str(candidate.get("price_source_domain") or "").strip()
    source_url = str(candidate.get("price_product_url") or "").strip()
    source_type = str(candidate.get("price_source_type") or "").strip()
    vendor = str(candidate.get("vendor") or "").strip()

    source_label = _pretty_source_name(
        source_url=source_url,
        source_domain=source_domain,
        source_type=source_type,
        vendor=vendor,
    )

    if source_url:
        return (
            f'<a href="{html.escape(source_url)}" target="_blank">'
            f'{html.escape(source_label)}'
            f'</a>'
        )

    return html.escape(source_label)


def _render_candidate_price_block(candidate: dict | None) -> str:
    if not candidate:
        return """
        <div class="card">
          <h3>Цена и источник</h3>
          <p class="muted">Кандидат не найден.</p>
        </div>
        """

    if not candidate.get("price_found"):
        return """
        <div class="card">
          <h3>Цена и источник</h3>
          <p><b>Статус:</b> цена не найдена</p>
        </div>
        """

    price_text = _normalize_candidate_price_text(candidate)
    source_html = _render_candidate_price_source(candidate)
    article = (
        _normalize_article(candidate.get("price_article"))
        or _normalize_designation(candidate.get("price_designation"))
        or "—"
    )
    price_title = str(candidate.get("price_title") or "—")
    match_type = str(candidate.get("price_match_type") or "—")

    return f"""
    <div class="card">
      <h3>Цена и источник</h3>
      <p><b>Статус:</b> <span class="ok">цена найдена</span></p>
      <p><b>Цена:</b> {html.escape(price_text)}</p>
      <p><b>Артикул:</b> {html.escape(article)}</p>
      <p><b>Наименование из прайс-каталога:</b> {html.escape(price_title)}</p>
      <p><b>Тип совпадения:</b> {html.escape(match_type)}</p>
      <p><b>Источник:</b> {source_html}</p>
    </div>
    """

def _norm_identity_text(value: Any) -> str:
    """
    Нормализация identity-текста для сравнения аппаратов.

    В каталогах и LLM-выходах часто смешиваются латиница и кириллица:
    BA-430 / ВА-430, BA51-35 / ВА51-35, C / С, A / А.
    Для матчинга это должен быть один и тот же ключ.
    """
    s = str(value or "").strip().upper()
    if not s:
        return ""

    s = (
        s.replace("\u00a0", " ")
         .replace("–", "-")
         .replace("—", "-")
         .replace("−", "-")
    )

    cyr_to_lat = str.maketrans({
        "А": "A",
        "В": "B",
        "Е": "E",
        "К": "K",
        "М": "M",
        "Н": "H",
        "О": "O",
        "Р": "P",
        "С": "C",
        "Т": "T",
        "У": "Y",
        "Х": "X",
    })

    s = s.translate(cyr_to_lat)

    # Нормализуем пробелы, но не удаляем их полностью:
    # NB8-125R 3P D40 и NB8-125R3PD40 пока лучше не схлопывать.
    s = " ".join(s.split())

    return s


def _norm_identity_float(value: Any) -> str:
    if value in (None, ""):
        return ""
    try:
        f = float(value)
        return str(int(f)) if f.is_integer() else str(f)
    except Exception:
        return str(value).strip().upper()


def _norm_identity_range(value: Any) -> str:
    if not isinstance(value, dict):
        return ""
    lo = _norm_identity_float(value.get("min"))
    hi = _norm_identity_float(value.get("max"))
    if not lo and not hi:
        return ""
    return f"{lo}-{hi}"

def _infer_current_range_from_model_text(candidate: dict) -> dict | None:
    """
    Для MPCB в candidate_options часто нет current_range_a,
    но диапазон уставки зашит в model:
    'ВА-430 9-14A', 'NS2 9-14A', 'NS8 6.3-10A'.
    Без этого official price из grouped-каталога не матчится.
    """
    model = str(candidate.get("model") or "").strip()
    if not model:
        return None

    model = (
        model.replace(",", ".")
             .replace("–", "-")
             .replace("—", "-")
             .replace("−", "-")
    )

    m = re.search(
        r"(\d+(?:\.\d+)?)\s*-\s*(\d+(?:\.\d+)?)\s*A\b",
        model,
        flags=re.I,
    )
    if not m:
        return None

    return {
        "min": float(m.group(1)),
        "max": float(m.group(2)),
    }

def _infer_poles_from_model_text(candidate: dict) -> Any:
    """
    В строках build_normative_review candidate_options часто нет поля poles.
    Для MCB полюсность обычно зашита в model: '3P D40', '3P+N D40'.
    Для MPCB модель часто выглядит как 'ВА-430 9-14A' или 'NS2 9-14A',
    без '3P', но такие автоматы защиты двигателя в нашем каталоге идут как 3P.
    """
    poles = candidate.get("poles")
    if poles not in (None, ""):
        return poles

    text = _norm_identity_text(candidate.get("model"))

    if "3P+N" in text:
        return "3P+N"
    if "1P+N" in text:
        return "1P+N"
    if "4P" in text:
        return 4
    if "3P" in text:
        return 3
    if "2P" in text:
        return 2
    if "1P" in text:
        return 1

    # ВАЖНО: MPCB / автоматы защиты двигателя в текущем проектном каталоге
    # считаем 3-полюсными, если poles не передали явно.
    if _norm_identity_text(candidate.get("device_class")) == "MPCB":
        return 3

    return ""

def _candidate_identity_key(candidate: dict) -> tuple[str, str, str, str, str, str, str, str, str]:
    current_range_key = (
        _norm_identity_range(candidate.get("current_range_a"))
        or _norm_identity_range(_infer_current_range_from_model_text(candidate))
    )

    return (
        _norm_identity_text(candidate.get("vendor")),
        _norm_identity_text(candidate.get("series")),
        _norm_identity_text(candidate.get("model")),
        _norm_identity_text(candidate.get("device_class")),
        _norm_identity_float(candidate.get("rated_current_a")),
        _norm_identity_text(_infer_poles_from_model_text(candidate)),
        _norm_identity_text(candidate.get("trip_curve")),
        _norm_identity_float(candidate.get("breaking_capacity_ka")),
        _norm_identity_text(candidate.get("rcd_ma") or current_range_key),
    )

def _infer_poles_from_price_row_model(price_row: dict) -> Any:
    """
    В candidate_prices_openai.json поле candidate_poles для 3P+N может быть равно 3.
    Для identity нужно сохранять различие между 3P и 3P+N,
    поэтому сначала извлекаем полюсность из candidate_model.
    """
    model = _norm_identity_text(price_row.get("candidate_model"))

    if "3P+N" in model:
        return "3P+N"
    if "1P+N" in model:
        return "1P+N"
    if "4P" in model:
        return 4
    if "3P" in model:
        return 3
    if "2P" in model:
        return 2
    if "1P" in model:
        return 1

    return price_row.get("candidate_poles")

def _price_candidate_identity_key(price_row: dict) -> tuple[str, str, str, str, str, str, str, str, str]:
    return (
        _norm_identity_text(price_row.get("candidate_vendor")),
        _norm_identity_text(price_row.get("candidate_series")),
        _norm_identity_text(price_row.get("candidate_model")),
        _norm_identity_text(price_row.get("candidate_device_class")),
        _norm_identity_float(price_row.get("candidate_rated_current_a")),
        _norm_identity_text(_infer_poles_from_price_row_model(price_row)),
        _norm_identity_text(price_row.get("candidate_trip_curve")),
        _norm_identity_float(price_row.get("candidate_breaking_capacity_ka")),
        _norm_identity_text(
            price_row.get("candidate_rcd_ma")
            or _norm_identity_range(price_row.get("candidate_current_range_a"))
        ),
    )
    
def _safe_rank_fallback_match(price_row: dict, opt: dict) -> bool:
    """
    Безопасный fallback после строгого identity-match.

    Используется только когда:
    - rank совпадает;
    - vendor совпадает;
    - device_class совпадает;
    - основные технические параметры совпадают;
    - модель/серия совпадают после нормализации кириллица/латиница
      или хотя бы одна модельная строка содержит другую.

    Это возвращает цены, которые раньше отображались,
    но не откатывает поведение к опасному "по одному rank".
    """
    if str(price_row.get("rank") or "").strip() != str(opt.get("rank") or "").strip():
        return False

    if _norm_identity_text(price_row.get("candidate_vendor")) != _norm_identity_text(opt.get("vendor")):
        return False

    if _norm_identity_text(price_row.get("candidate_device_class")) != _norm_identity_text(opt.get("device_class")):
        return False

    if _norm_identity_float(price_row.get("candidate_rated_current_a")) != _norm_identity_float(opt.get("rated_current_a")):
        return False

    if _norm_identity_text(price_row.get("candidate_poles")) != _norm_identity_text(opt.get("poles")):
        return False

    if _norm_identity_text(price_row.get("candidate_trip_curve")) != _norm_identity_text(opt.get("trip_curve")):
        return False

    if _norm_identity_float(price_row.get("candidate_breaking_capacity_ka")) != _norm_identity_float(opt.get("breaking_capacity_ka")):
        return False

    price_rcd_or_range = _norm_identity_text(
        price_row.get("candidate_rcd_ma")
        or _norm_identity_range(price_row.get("candidate_current_range_a"))
    )
    opt_rcd_or_range = _norm_identity_text(
        opt.get("rcd_ma")
        or _norm_identity_range(opt.get("current_range_a"))
    )

    if price_rcd_or_range != opt_rcd_or_range:
        return False

    price_series = _norm_identity_text(price_row.get("candidate_series"))
    opt_series = _norm_identity_text(opt.get("series"))

    price_model = _norm_identity_text(price_row.get("candidate_model"))
    opt_model = _norm_identity_text(opt.get("model"))

    if price_series and opt_series and price_series != opt_series:
        return False

    if price_model and opt_model:
        if price_model == opt_model:
            return True
        if price_model in opt_model or opt_model in price_model:
            return True
        return False

    return True

def _find_price_for_candidate(
    prices: list[dict],
    opt: dict,
) -> dict | None:
    # 1. Цена уже есть в candidate_options после official merge.
    if opt.get("price_found"):
        normalized_price = _normalize_price_number(opt.get("price_rub"))
        if normalized_price is None:
            return None

        return {
            "price_found": True,
            "price_value": normalized_price,
            "price_article": _normalize_article(opt.get("price_article")),
            "price_currency": opt.get("price_currency") or "RUB",
            "price_source_type": opt.get("price_source_type") or "",
            "price_source_name": opt.get("price_source_domain") or "",
            "price_url_note": opt.get("price_product_url") or "",
            "price_comment": opt.get("price_title") or "",
            "price_designation": _normalize_designation(opt.get("price_designation")),
            "price_source_domain": opt.get("price_source_domain") or "",
            "price_product_url": opt.get("price_product_url") or "",
        }

    # 2. Строгий fallback по расширенному identity.
    opt_key = _candidate_identity_key(opt)

    for row in prices:
        if _price_candidate_identity_key(row) == opt_key:
            return row

    # 3. Мягкий fallback по vendor/series/model.
    # Это возвращает старое рабочее поведение, но без опасного match только по rank.
    opt_vendor = _norm_identity_text(opt.get("vendor"))
    opt_series = _norm_identity_text(opt.get("series"))
    opt_model = _norm_identity_text(opt.get("model"))

    for row in prices:
        rv = _norm_identity_text(row.get("candidate_vendor"))
        rs = _norm_identity_text(row.get("candidate_series"))
        rm = _norm_identity_text(row.get("candidate_model"))

        if rv == opt_vendor and rs == opt_series and rm == opt_model:
            return row

    return None

# def _find_price_for_candidate(
#     prices: list[dict],
#     opt: dict,
# ) -> dict | None:
#     # 1. Сначала берем цену прямо из merged candidate_options
#     if opt.get("price_found"):
#         normalized_price = _normalize_price_number(opt.get("price_rub"))
#         return {
#             "price_found": normalized_price is not None,
#             "price_value": normalized_price,
#             "price_article": _normalize_article(opt.get("price_article")),
#             "price_currency": opt.get("price_currency") or "RUB",
#             "price_source_type": opt.get("price_source_type") or "",
#             "price_source_name": opt.get("price_source_domain") or "",
#             "price_url_note": opt.get("price_product_url") or "",
#             "price_comment": opt.get("price_title") or "",
#             "candidate_vendor": opt.get("vendor"),
#             "candidate_series": opt.get("series"),
#             "candidate_model": opt.get("model"),
#             "price_designation": _normalize_designation(opt.get("price_designation")),
#         }

#     # 2. Потом fallback из OpenAI candidate_prices_openai.json.
#     # Сначала строгий расширенный identity-match.
#     opt_key = _candidate_identity_key(opt)

#     for row in prices:
#         if _price_candidate_identity_key(row) == opt_key:
#             return row

#     # 2.1. Безопасный fallback для случаев BA/ВА, A/А, C/С
#     # и похожих нормализационных расхождений.
#     for row in prices:
#         if _safe_rank_fallback_match(row, opt):
#             return row

#     # 3. Backward compatibility для старых candidate_prices_openai.json,
#     # где ещё нет расширенных candidate_* полей.
#     opt_vendor = str(opt.get("vendor") or "").strip().upper()
#     opt_series = str(opt.get("series") or "").strip().upper()
#     opt_model = str(opt.get("model") or "").strip().upper()

#     for row in prices:
#         rv = str(row.get("candidate_vendor") or "").strip().upper()
#         rs = str(row.get("candidate_series") or "").strip().upper()
#         rm = str(row.get("candidate_model") or "").strip().upper()

#         has_new_identity_fields = any(
#             row.get(k) not in (None, "", [])
#             for k in [
#                 "candidate_device_class",
#                 "candidate_rated_current_a",
#                 "candidate_poles",
#                 "candidate_trip_curve",
#                 "candidate_breaking_capacity_ka",
#                 "candidate_rcd_ma",
#                 "candidate_current_range_a",
#             ]
#         )

#         # Если файл новый, но строгий ключ не совпал — НЕ делаем мягкий fallback,
#         # иначе опять можно приклеить цену к похожему аппарату.
#         if has_new_identity_fields:
#             continue

#         if rv == opt_vendor and rs == opt_series and rm == opt_model:
#             return row

#     return None

def _fallback_price_payload(price_row: dict | None) -> dict:
    if not price_row:
        return {}

    normalized_price = _normalize_price_number(price_row.get("price_value"))
    price_found = bool(price_row.get("price_found")) and normalized_price is not None
    price_currency = str(price_row.get("price_currency") or "RUB").strip() if price_found else ""

    return {
        "price_found": price_found,
        "price_match_type": "openai_fallback" if price_found else "openai_fallback_not_found",
        "price_vendor": price_row.get("candidate_vendor"),
        "price_article": _normalize_article(price_row.get("price_article")),
        "price_title": price_row.get("price_title") or price_row.get("price_comment") or price_row.get("price_url_note") or "",
        "price_rub": normalized_price,
        "price_currency": price_currency,
        "price_source_domain": "",
        "price_source_type": price_row.get("price_source_type"),
        "price_product_url": "",
        "price_match_key": None,
        "price_source_name": price_row.get("price_source_name"),
        "price_url_note": price_row.get("price_url_note"),
        "price_comment": price_row.get("price_comment"),
        "price_designation": _normalize_designation(price_row.get("price_designation")),
    }
    
def _merge_candidate_options_with_fallback_prices(
    row: dict,
    price_rows: list[dict] | None,
) -> dict:
    if not row:
        return row

    if not price_rows:
        return row

    candidate_options = row.get("candidate_options") or []
    if not isinstance(candidate_options, list):
        return row

    new_options: list[dict] = []

    for opt in candidate_options:
        patched = dict(opt)

        if not patched.get("price_found"):
            fallback = _find_price_for_candidate(price_rows, patched)
            fallback_payload = _fallback_price_payload(fallback)

            if fallback_payload.get("price_found"):
                for fld, val in fallback_payload.items():
                    if val not in (None, "", []):
                        patched[fld] = val

        new_options.append(patched)

    out = dict(row)
    out["candidate_options"] = new_options
    return out

def _merge_candidate_with_fallback_price(
    row: dict,
    price_rows: list[dict] | None,
) -> dict:
    if not row:
        return row

    if not price_rows:
        return row

    candidate = row.get("candidate") or {}
    if not isinstance(candidate, dict) or not candidate:
        return row

    patched_candidate = dict(candidate)

    if not patched_candidate.get("price_found"):
        fallback = _find_price_for_candidate(price_rows, patched_candidate)
        fallback_payload = _fallback_price_payload(fallback)

        if fallback_payload.get("price_found"):
            for fld, val in fallback_payload.items():
                if val not in (None, "", []):
                    patched_candidate[fld] = val

    out = dict(row)
    out["candidate"] = patched_candidate
    return out

def _merge_candidate_options_with_prices(
    base_row: dict,
    merged_row: dict | None,
) -> dict:
    if not base_row:
        return base_row

    if not merged_row:
        return base_row

    base_options = base_row.get("candidate_options") or []
    if not isinstance(base_options, list):
        return base_row

    price_index = _build_grouped_price_identity_index(merged_row)

    new_options: list[dict] = []

    for opt in base_options[:MAX_CANDIDATES_TO_RENDER]:
        if not isinstance(opt, dict):
            new_options.append(opt)
            continue

        merged_opt = price_index.get(_candidate_identity_key(opt))

        if merged_opt:
            patched = _copy_price_fields_from_source(opt, merged_opt)
            new_options.append(patched)
        else:
            new_options.append(opt)

    out = dict(base_row)
    out["candidate_options"] = new_options
    return out

def _copy_price_fields_from_source(target: dict, source: dict | None) -> dict:
    """
    Копирует только price_* поля. Не трогает vendor/series/model/rank/why/verdict.
    """
    out = dict(target or {})

    if not source:
        return out

    for fld in PRICE_FIELDS:
        if fld not in source:
            continue

        val = source.get(fld)

        if fld == "price_article":
            val = _normalize_article(val)

        if fld == "price_found":
            out[fld] = bool(val)
            continue

        if val not in (None, "", []):
            out[fld] = val

    return out


def _build_grouped_price_identity_index(merged_row: dict | None) -> dict[tuple, dict]:
    """
    Индексирует все ценовые кандидаты grouped-каталога по identity.
    Берём и верхний candidate, и candidate_options.
    """
    index: dict[tuple, dict] = {}

    if not merged_row:
        return index

    sources: list[dict] = []

    candidate = merged_row.get("candidate")
    if isinstance(candidate, dict):
        sources.append(candidate)

    options = merged_row.get("candidate_options") or []
    if isinstance(options, list):
        sources.extend([x for x in options if isinstance(x, dict)])

    for src in sources:
        if not src.get("price_found"):
            continue

        key = _candidate_identity_key(src)
        if key not in index:
            index[key] = src

    return index


def _merge_candidate_price_into_row(
    base_row: dict,
    merged_row: dict | None,
) -> dict:
    """
    Доклеивает цену к верхнему candidate только если найден тот же самый аппарат.

    ВАЖНО:
    нельзя копировать price_* из merged_row['candidate'] напрямую,
    потому что верхний кандидат в build_normative_review() и верхний кандидат
    в catalog_with_prices_grouped.json могут отличаться по порядку.
    """
    if not base_row or not merged_row:
        return base_row

    out = dict(base_row)

    base_candidate = base_row.get("candidate") or {}
    if not isinstance(base_candidate, dict) or not base_candidate:
        return out

    price_index = _build_grouped_price_identity_index(merged_row)
    matched_price_source = price_index.get(_candidate_identity_key(base_candidate))

    if matched_price_source:
        out["candidate"] = _copy_price_fields_from_source(
            base_candidate,
            matched_price_source,
        )

    return out

def _critic_badge_class(value: str) -> str:
    v = str(value or "").strip().lower()
    if v in {"accepted", "low"}:
        return "ok"
    if v in {"accepted_with_conditions", "manual_review_required", "medium"}:
        return "warn"
    return "bad"


def _render_grok_critic_block(critic: dict | None, critic_error: str | None = None) -> str:
    if critic_error and not critic:
        return f"""
        <div class="card">
          <h3>Внешняя LLM-критика результата</h3>
          <p class="bad">Критика не выполнена: {html.escape(str(critic_error))}</p>
        </div>
        """

    if not critic:
        return """
        <div class="card">
          <h3>Внешняя LLM-критика результата</h3>
          <p class="muted">Критика результата не сформирована.</p>
        </div>
        """

    issues = critic.get("issues") or []
    issues_html = ""

    if issues:
        items = []
        for issue in issues:
            sev = str(issue.get("severity") or "")
            items.append(
                f"""
                <li>
                  <b class="{_critic_badge_class(sev)}">{html.escape(sev)}</b>:
                  {html.escape(str(issue.get("message") or ""))}
                  <div class="muted">{html.escape(str(issue.get("evidence") or ""))}</div>
                </li>
                """
            )
        issues_html = "<ul>" + "".join(items) + "</ul>"
    else:
        issues_html = "<p class='muted'>Замечаний не найдено.</p>"

    stale_note = ""
    if critic.get("_stale"):
        stale_note = f"""
        <p class="warn">Показан ранее сохраненный результат критики. {html.escape(str(critic.get("_warning") or ""))}</p>
        """

    return f"""
    <div class="card">
      <h3>Внешняя LLM-критика результата</h3>
      <p class="muted">Критик не изменяет выбранный аппарат, а проверяет согласованность JSON/API, LLM-текста, цены, требований и нормативных оснований.</p>
      {stale_note}
      <p><b>Вердикт:</b> <span class="{_critic_badge_class(str(critic.get("critic_verdict") or ""))}">{html.escape(str(critic.get("critic_verdict") or "—"))}</span></p>
      <p><b>Оценка согласованности:</b> {html.escape(str(critic.get("critic_score") or "—"))}</p>
      <p><b>Уровень риска:</b> <span class="{_critic_badge_class(str(critic.get("risk_level") or ""))}">{html.escape(str(critic.get("risk_level") or "—"))}</span></p>
      <p><b>Итог:</b> {html.escape(str(critic.get("summary") or ""))}</p>
      <h4>Замечания</h4>
      {issues_html}
      <p><b>Рекомендация:</b> {html.escape(str(critic.get("recommendation") or ""))}</p>
    </div>
    """

def _render_result(row: dict, price_rows: list[dict] | None = None) -> str:
    verdict = row.get("verdict") or "unknown"
    candidate = row.get("candidate") or {}
    evidence = row.get("evidence_top") or []
    bullets = row.get("summary_bullets") or []
    explanation = row.get("llm_readable_explanation") or row.get("readable_explanation") or ""
    alternative_summary = row.get("llm_alternative_summary") or ""
    manual_review_note = row.get("llm_manual_review_note") or ""
    confidence = row.get("confidence")
    query = row.get("query") or ""
    checks = row.get("engineering_checks") or []
    why_this = row.get("why_this_candidate") or ""
    candidate_options = (row.get("candidate_options") or [])[:MAX_CANDIDATES_TO_RENDER]
    price_rows = price_rows or []
    normative_refs = row.get("normative_refs") or []
    llm_critic = row.get("llm_critic")
    llm_critic_error = row.get("llm_critic_error")

    candidate_title = " ".join(
        str(x) for x in [
            candidate.get("vendor") or "",
            candidate.get("series") or "",
            candidate.get("model") or "",
        ] if x
    ).strip()

    bullets_html = "".join(f"<li>{html.escape(str(x))}</li>" for x in bullets)

    checks_html = ""
    if checks:
        checks_items = []
        for ch in checks:
            ref = ch.get("reference") or {}
            ref_txt = ""
            if ref:
                ref_txt = f"<div class='muted'>Источник: {html.escape(str(ref.get('doc_title') or ''))}</div>"
            checks_items.append(
                f"<li><b>{html.escape(str(ch.get('title') or ''))}</b>: "
                f"{html.escape(str(ch.get('details') or ''))}{ref_txt}</li>"
            )
        checks_html = "<ul>" + "".join(checks_items) + "</ul>"
    else:
        checks_html = "<p>Дополнительные инженерные проверки не сформированы.</p>"

    options_html = ""
    if candidate_options:
        rows = []
        for opt in candidate_options:
            title = " ".join(
                str(x) for x in [
                    opt.get("vendor") or "",
                    opt.get("series") or "",
                    opt.get("model") or "",
                ] if x
            ).strip()

            # В первую очередь отображаем цену, уже записанную в candidate_options.
            # Это официальный merged/grouped слой.
            if opt.get("price_found"):
                price_row = opt
            else:
                price_row = _find_price_for_candidate(price_rows, opt)

            price_text = _normalize_price_text(price_row)

            price_comment = ""
            price_source = "—"

            if price_row:
                source_domain = str(
                    price_row.get("price_source_domain")
                    or price_row.get("source_domain")
                    or ""
                ).strip()

                source_url = str(
                    price_row.get("price_product_url")
                    or price_row.get("source_url")
                    or ""
                ).strip()

                source_type = str(
                    price_row.get("price_source_type")
                    or price_row.get("source_type")
                    or ""
                ).strip()

                source_label = _pretty_source_name(
                    source_url=source_url,
                    source_domain=source_domain,
                    source_type=source_type,
                    vendor=str(opt.get("vendor") or ""),
                )

                if source_url:
                    price_source = (
                        f'<a href="{html.escape(source_url)}" target="_blank">'
                        f'{html.escape(source_label)}'
                        f'</a>'
                    )
                else:
                    price_source = html.escape(source_label)

                # Явная логика пояснения по цене:
                # 1) если цена есть прямо в grouped merged/catalog parser слое
                # 2) если цена пришла через fallback OpenAI
                if price_row and price_row.get("price_found") and _is_official_source(price_row.get("price_source_type")):
                    price_comment = "Цена взята из официального каталога производителя."
                elif price_row and price_row.get("price_found"):
                    price_comment = str(
                        opt.get("price_comment")
                        or price_row.get("price_comment")
                        or price_row.get("price_title")
                        or "Цена найдена через fallback-поиск и требует проверки."
                    ).strip()
                else:
                    price_comment = str(
                        price_row.get("price_comment")
                        or price_row.get("price_title")
                        or ""
                    ).strip()
            else:
                price_source = "—"
                price_comment = "—"

            alt_article = ""
            if price_row:
                alt_article = _normalize_article(
                    price_row.get("price_article")
                    or price_row.get("article")
                    or opt.get("price_article")
                    or ""
                )
                alt_designation = _normalize_designation(
                    price_row.get("price_designation")
                    or opt.get("price_designation")
                    or ""
                )
            else:
                alt_article = _normalize_article(opt.get("price_article") or "")
                alt_designation = _normalize_designation(opt.get("price_designation") or "")

            article_cell = alt_article or alt_designation or "—"

            rows.append(
                f"<tr>"
                f"<td>{html.escape(str(opt.get('rank') or ''))}</td>"
                f"<td>{html.escape(title)}</td>"
                f"<td>{html.escape(price_text)}</td>"
                f"<td>{price_source}</td>"
                f"<td>{html.escape(article_cell)}</td>"
                f"<td>{html.escape(str(opt.get('verdict') or ''))}</td>"
                f"<td>{html.escape(str(opt.get('confidence') or ''))}</td>"
                f"<td>{html.escape(str(opt.get('why_not_best') or ''))}</td>"
                f"<td>{html.escape(price_comment)}</td>"
                f"</tr>"
            )
            

        options_html = f"""
        <div class="card">
          <h3>Альтернативные кандидаты</h3>
          <table style="width:100%; border-collapse:collapse;">
            <thead>
              <tr>
                <th style="text-align:left; padding:8px;">Ранг</th>
                <th style="text-align:left; padding:8px;">Кандидат</th>
                <th style="text-align:left; padding:8px;">Цена</th>
                <th style="text-align:left; padding:8px;">Источник цены</th>
                <th style="text-align:left; padding:8px;">Артикул</th>
                <th style="text-align:left; padding:8px;">Verdict</th>
                <th style="text-align:left; padding:8px;">Confidence</th>
                <th style="text-align:left; padding:8px;">Почему не лучший</th>
                <th style="text-align:left; padding:8px;">Примечание по цене</th>
              </tr>
            </thead>
            <tbody>
              {''.join(rows)}
            </tbody>
          </table>
        </div>
        """

    refs_html = ""
    if normative_refs:
        ref_items = []
        for ref in normative_refs:
            ref_items.append(
                f"<li><b>{html.escape(str(ref.get('doc_title') or ''))}</b>: "
                f"{html.escape(str(ref.get('section_hint') or ''))}</li>"
            )
        refs_html = f"""
        <div class="card">
          <h3>Нормативные ссылки</h3>
          <ul>{''.join(ref_items)}</ul>
        </div>
        """

    evidence_html_parts = []
    for item in evidence:
        evidence_html_parts.append(f"""
        <div class="card">
          <h3>{html.escape(str(item.get("doc_title") or ""))}</h3>
          <div class="muted">{html.escape(str(item.get("section_hint") or ""))}</div>
          <p><b>Score:</b> {html.escape(str(item.get("score") or ""))}</p>
          <pre>{html.escape(str(item.get("excerpt") or ""))}</pre>
        </div>
        """)

    evidence_html = "\n".join(evidence_html_parts) if evidence_html_parts else "<p>Нормативные основания не найдены.</p>"
    candidate_price_block = ""

    return f"""
<div class="card">
  <h2>Результат по тегу: {html.escape(str(row.get("tag") or ""))}</h2>
  <p><b>Кандидат:</b> {html.escape(candidate_title)}</p>
  <p><b>Verdict:</b> <span class="{_verdict_class(verdict)}">{html.escape(verdict)}</span></p>
  <p><b>Confidence:</b> {html.escape(str(confidence))}</p>
  <p><b>Почему выбран этот кандидат:</b> {html.escape(why_this)}</p>
  <p><b>LLM-объяснение:</b> {html.escape(explanation)}</p>
  <p><b>Сравнение с альтернативами:</b> {html.escape(alternative_summary)}</p>
  <p><b>Что проверить вручную:</b> {html.escape(manual_review_note)}</p>
</div>

{_render_grok_critic_block(llm_critic, llm_critic_error)}

<div class="grid">
  <div class="card">
    <h3>Краткие выводы</h3>
    <ul>{bullets_html}</ul>
  </div>
  <div class="card">
    <h3>Поисковый запрос RAG</h3>
    <pre>{html.escape(query)}</pre>
  </div>
</div>

{options_html}
{refs_html}

<div class="card">
  <h3>Что инженер должен проверить</h3>
  {checks_html}
</div>

<h2>Нормативные основания</h2>
{evidence_html}
"""

def _render_review_block(review_row: dict | None) -> str:
    if not review_row:
        return """
        <div class="card">
          <h3>Обзор открытых источников</h3>
          <p class="muted">Для данного тега обзор отзывов не найден.</p>
        </div>
        """

    review_summary = review_row.get("review_summary") or ""
    positive_points = review_row.get("positive_points") or []
    negative_points = review_row.get("negative_points") or []
    risk_flags = review_row.get("risk_flags") or []
    manual_caution = review_row.get("manual_caution") or ""
    source_notes = review_row.get("source_notes") or []

    def _ul(items: list[Any]) -> str:
        if not items:
            return "<p class='muted'>Нет данных.</p>"
        return "<ul>" + "".join(f"<li>{html.escape(str(x))}</li>" for x in items) + "</ul>"

    return f"""
    <div class="card">
      <h3>Обзор открытых источников</h3>
      <p class="muted">Этот блок носит справочный характер и не заменяет расчет и нормативную проверку.</p>
      <p>{html.escape(str(review_summary))}</p>
    </div>

    <div class="grid">
      <div class="card">
        <h3>Положительные сигналы</h3>
        {_ul(positive_points)}
      </div>
      <div class="card">
        <h3>Негативные сигналы</h3>
        {_ul(negative_points)}
      </div>
    </div>

    <div class="grid">
      <div class="card">
        <h3>Риски</h3>
        {_ul(risk_flags)}
      </div>
      <div class="card">
        <h3>Ручная проверка по отзывам</h3>
        <p>{html.escape(str(manual_caution))}</p>
      </div>
    </div>

    <div class="card">
      <h3>Примечания по источникам</h3>
      {_ul(source_notes)}
    </div>
    """
def _rank_key(value: Any) -> str:
    if value in (None, ""):
        return ""
    try:
        return str(int(float(value)))
    except Exception:
        return str(value).strip()


def _copy_price_fields(target: dict, source: dict | None) -> dict:
    """
    Копирует только ценовые поля из grouped/fallback-слоя.
    Не трогает verdict, confidence, why_not_best и прочую нормативную логику.
    """
    if not source:
        return dict(target or {})

    out = dict(target or {})

    for fld in PRICE_FIELDS:
        if fld not in source:
            continue

        val = source.get(fld)

        if fld == "price_article":
            val = _normalize_article(val)

        # price_found=false тоже нужно копировать, чтобы состояние было явным
        if fld == "price_found":
            out[fld] = bool(val)
            continue

        if val not in (None, "", []):
            out[fld] = val

    return out


def _merge_grouped_prices_preserve_normative(
    base_row: dict,
    grouped_row: dict | None,
) -> dict:
    """
    Берём base_row из build_normative_review как источник истины по:
    - verdict;
    - confidence;
    - why_this_candidate;
    - why_not_best;
    - количеству отображаемых кандидатов.

    Из catalog_with_prices_grouped.json берём только:
    - price_*;
    - артикул;
    - источник;
    - ссылку.

    Мержим в первую очередь по rank внутри одного тега.
    """
    if not grouped_row:
        return base_row

    out = dict(base_row or {})

    grouped_candidate = grouped_row.get("candidate") or {}
    if isinstance(grouped_candidate, dict):
        base_candidate = out.get("candidate") or {}
        if isinstance(base_candidate, dict):
            out["candidate"] = _copy_price_fields(base_candidate, grouped_candidate)

    base_options = out.get("candidate_options") or []
    grouped_options = grouped_row.get("candidate_options") or []

    if not isinstance(base_options, list):
        base_options = []

    if not isinstance(grouped_options, list):
        grouped_options = []

    grouped_by_rank: dict[str, dict] = {}
    for gopt in grouped_options:
        if not isinstance(gopt, dict):
            continue
        rk = _rank_key(gopt.get("rank"))
        if rk and rk not in grouped_by_rank:
            grouped_by_rank[rk] = gopt

    merged_options: list[dict] = []

    # ВАЖНО: сохраняем только первые MAX_CANDIDATES_TO_RENDER из нормативного shortlist.
    for opt in base_options[:MAX_CANDIDATES_TO_RENDER]:
        if not isinstance(opt, dict):
            continue

        rk = _rank_key(opt.get("rank"))
        grouped_opt = grouped_by_rank.get(rk)

        patched = _copy_price_fields(opt, grouped_opt)
        merged_options.append(patched)

    # Если вдруг base_options пустой, fallback — первые 5 из grouped.
    if not merged_options and grouped_options:
        for opt in grouped_options[:MAX_CANDIDATES_TO_RENDER]:
            if isinstance(opt, dict):
                merged_options.append(dict(opt))

    out["candidate_options"] = merged_options

    if out.get("requirement_ref") in (None, "", []) and grouped_row.get("requirement_ref"):
        out["requirement_ref"] = grouped_row.get("requirement_ref")

    return out


def _copy_fallback_price_fields(target: dict, price_row: dict | None) -> dict:
    """
    Доклеивает fallback-цену OpenAI в candidate/candidate_option.
    Работает только с price_row из candidate_prices_openai.json.
    """
    out = dict(target or {})

    if not price_row:
        return out

    payload = _fallback_price_payload(price_row)

    if not payload.get("price_found"):
        return out

    for fld, val in payload.items():
        if fld == "price_found":
            out[fld] = bool(val)
            continue

        if val not in (None, "", []):
            out[fld] = val

    return out


def _apply_fallback_prices_by_rank(row: dict, price_rows: list[dict] | None) -> dict:
    """
    candidate_prices_openai.json был сгенерирован по тем же candidate_options,
    поэтому для fallback-цен используем простую и стабильную привязку:
    один tag + один rank.

    Это не трогает нормативное ранжирование и не меняет порядок кандидатов.
    """
    if not row or not price_rows:
        return row

    out = dict(row)

    price_by_rank: dict[str, dict] = {}
    for price_row in price_rows:
        if not isinstance(price_row, dict):
            continue
        rk = _rank_key(price_row.get("rank"))
        if rk and rk not in price_by_rank:
            price_by_rank[rk] = price_row

    # Основной candidate = rank 1, если в нём нет официальной цены.
    candidate = out.get("candidate") or {}
    if isinstance(candidate, dict):
        patched_candidate = dict(candidate)
        if not patched_candidate.get("price_found"):
            patched_candidate = _copy_fallback_price_fields(
                patched_candidate,
                price_by_rank.get("1"),
            )
        out["candidate"] = patched_candidate

    options = out.get("candidate_options") or []
    if not isinstance(options, list):
        options = []

    patched_options: list[dict] = []

    for opt in options[:MAX_CANDIDATES_TO_RENDER]:
        if not isinstance(opt, dict):
            continue

        patched = dict(opt)

        if not patched.get("price_found"):
            rk = _rank_key(patched.get("rank"))
            patched = _copy_fallback_price_fields(
                patched,
                price_by_rank.get(rk),
            )

        patched_options.append(patched)

    out["candidate_options"] = patched_options

    return out

# def _build_tag_payload(tag: str) -> dict:
#     shortlist_rows = _load_json(SHORTLIST_PATH)
#     requirements_rows = _load_json(REQUIREMENTS_PATH)

#     global QDRANT_STORE
#     if QDRANT_STORE is None:
#         raise RuntimeError("Qdrant store is not initialized.")

#     qdrant_store = QDRANT_STORE

#     rows = build_normative_review(
#         shortlist_rows=shortlist_rows,
#         requirements_rows=requirements_rows,
#         corpus_path=CORPUS_PATH,
#         top_k=5,
#         qdrant_store=qdrant_store,
#     )

#     row = _find_row_by_tag(rows, tag)

#     openai_norm_rows = _load_json(OPENAI_NORMATIVE_REVIEW_PATH)
#     openai_review_rows = _load_json(OPENAI_CANDIDATE_REVIEWS_PATH)
#     openai_price_rows = _load_json(OPENAI_CANDIDATE_PRICES_PATH)

#     merged_catalog_rows = _load_json(MERGED_CATALOG_WITH_PRICES_PATH)
#     merged_catalog_row = _find_row_by_tag(merged_catalog_rows, tag)

#     if row is None and merged_catalog_row is not None:
#         row = dict(merged_catalog_row)
#         row["candidate_options"] = (row.get("candidate_options") or [])[:MAX_CANDIDATES_TO_RENDER]
#     elif row is not None and merged_catalog_row is not None:
#         row = _merge_grouped_prices_preserve_normative(row, merged_catalog_row)

#     openai_norm_index = _build_index_by_tag(openai_norm_rows)
#     openai_review_index = _build_index_by_tag(openai_review_rows)
#     openai_price_index = _build_prices_index_by_tag(openai_price_rows)

#     if row is None:
#         return {
#             "ok": False,
#             "tag": tag,
#             "error": "tag_not_found",
#             "message": f"Тег {tag} не найден в shortlist/requirements.",
#         }

#     tag_norm = str(tag or "").strip().upper()

#     llm_row = openai_norm_index.get(tag_norm)
#     if llm_row:
#         row = dict(row)
#         row["llm_readable_explanation"] = llm_row.get("llm_readable_explanation") or ""
#         row["llm_alternative_summary"] = llm_row.get("llm_alternative_summary") or ""
#         row["llm_manual_review_note"] = llm_row.get("llm_manual_review_note") or ""
#         row["llm_model"] = llm_row.get("llm_model") or ""

#     review_row = openai_review_index.get(tag_norm)
#     price_rows = openai_price_index.get(tag_norm, [])

#     row = _merge_candidate_with_fallback_price(row, price_rows)
#     row = _merge_candidate_options_with_fallback_prices(row, price_rows)

#     payload = {
#         "ok": True,
#         "tag": tag_norm,
#         "result": row,
#         "review_block": review_row,
#         "price_rows_fallback": price_rows,
#     }

#     return payload

def _candidate_identity_short(candidate: dict | None) -> tuple[str, str, str]:
    candidate = candidate or {}
    return (
        _norm_identity_text(candidate.get("vendor")),
        _norm_identity_text(candidate.get("series")),
        _norm_identity_text(candidate.get("model")),
    )


def _llm_row_matches_base_candidate(llm_row: dict | None, base_row: dict | None) -> bool:
    """
    LLM-текст можно подмешивать только если он относится к тому же выбранному
    кандидату, что и текущий результат build_normative_review().
    Иначе получаем ошибку типа: LLM пишет KEAZ, а таблица показывает CHINT.
    """
    if not llm_row or not base_row:
        return False

    llm_candidate = llm_row.get("candidate") or {}
    base_candidate = base_row.get("candidate") or {}

    return _candidate_identity_short(llm_candidate) == _candidate_identity_short(base_candidate)


def _limit_candidate_options(row: dict) -> dict:
    out = dict(row or {})
    options = out.get("candidate_options") or []
    if isinstance(options, list):
        out["candidate_options"] = options[:MAX_CANDIDATES_TO_RENDER]
    return out

def _attach_grok_critic(payload: dict, *, refresh: bool = False) -> dict:
    """
    Подключает внешний Grok-критик к API-ответу.

    Важно:
    - critic не меняет candidate;
    - critic не меняет verdict;
    - critic только добавляет result["llm_critic"];
    - результат сохраняется в data/output/runs/25-05/critic/{TAG}.grok_critic.json.
    """
    if not GROK_CRITIC_ENABLED:
        return payload

    if not payload.get("ok"):
        return payload

    tag = str(payload.get("tag") or "").strip().upper()
    if not tag:
        return payload

    result = payload.get("result")
    if not isinstance(result, dict):
        return payload

    try:
        if not refresh and not GROK_CRITIC_EVERY_REQUEST:
            critic_result = load_saved_grok_critic(tag=tag, run_dir=RUN_DIR)

            if critic_result is not None:
                result["llm_critic"] = critic_result
                payload["grok_critic_path"] = str(
                    RUN_DIR / "critic" / f"{tag}.grok_critic.json"
                )
            else:
                result["llm_critic_error"] = (
                    "Критика ещё не сформирована. "
                    "Для обновления вызови API с refresh_critic=true."
                )

            return payload

        critic_result = call_grok_critic(
            api_payload=payload,
            model=GROK_CRITIC_MODEL,
            reasoning_effort=GROK_CRITIC_REASONING,
        )
        critic_path = save_grok_critic_result(
            tag=tag,
            result=critic_result,
            run_dir=RUN_DIR,
        )

        result["llm_critic"] = critic_result
        payload["grok_critic_path"] = str(critic_path)

    except Exception as exc:
        # Если API Grok недоступен, не ломаем основной API.
        # Пробуем показать последнюю сохраненную критику.
        saved = load_saved_grok_critic(tag=tag, run_dir=RUN_DIR)

        if saved is not None:
            saved = dict(saved)
            saved["_stale"] = True
            saved["_warning"] = f"Grok API недоступен, показан ранее сохраненный результат: {exc}"
            result["llm_critic"] = saved
        else:
            result["llm_critic_error"] = str(exc)

    return payload

def _refresh_openai_layers_parallel(tag_norm: str, row: dict) -> None:
    """
    Пересчитывает OpenAI-слои для одного тега параллельно.

    Важно:
    - это НЕ должно выполняться при обычном запросе фронта;
    - запускать только через ?refresh_ai=true;
    - результаты сохраняются в те же JSON, которые потом читает FastAPI.
    """

    def run_normative() -> dict:
        rows = enrich_normative_review(
            [row],
            model=OPENAI_NORMATIVE_MODEL,
            only_tags=None,
        )
        return rows[0] if rows else {"tag": tag_norm, "llm_error": "empty_result"}

    def run_review() -> dict:
        rows = enrich_reviews(
            [row],
            model=OPENAI_REVIEW_MODEL,
            only_tags=None,
        )
        return rows[0] if rows else {"tag": tag_norm, "review_error": "empty_result"}

    def run_prices() -> dict:
        rows = enrich_prices(
            [row],
            model=OPENAI_PRICE_MODEL,
            only_tags=None,
            max_candidates_per_tag=OPENAI_MAX_CANDIDATES_PER_TAG,
        )
        return rows[0] if rows else {"tag": tag_norm, "prices": [], "price_error": "empty_result"}

    with ThreadPoolExecutor(max_workers=3) as pool:
        futures = {
            "normative": pool.submit(run_normative),
            "review": pool.submit(run_review),
            "prices": pool.submit(run_prices),
        }

        for name, future in futures.items():
            try:
                result = future.result()
            except Exception as exc:
                if name == "normative":
                    result = {"tag": tag_norm, "llm_error": f"{type(exc).__name__}: {exc}"}
                elif name == "review":
                    result = {"tag": tag_norm, "review_error": f"{type(exc).__name__}: {exc}"}
                else:
                    result = {"tag": tag_norm, "prices": [], "price_error": f"{type(exc).__name__}: {exc}"}

            if name == "normative":
                _upsert_row_by_tag(OPENAI_NORMATIVE_REVIEW_PATH, result)
            elif name == "review":
                _upsert_row_by_tag(OPENAI_CANDIDATE_REVIEWS_PATH, result)
            elif name == "prices":
                _upsert_row_by_tag(OPENAI_CANDIDATE_PRICES_PATH, result)

def _build_tag_payload(
    tag: str,
    *,
    refresh_ai: bool = False,
    refresh_critic: bool = False,
    ) -> dict:
    tag_norm = str(tag or "").strip().upper()

    shortlist_rows = _load_json(SHORTLIST_PATH)
    requirements_rows = _load_json(REQUIREMENTS_PATH)

    global QDRANT_STORE
    if QDRANT_STORE is None:
        raise RuntimeError("Qdrant store is not initialized.")

    # 1. Главный источник результата — нормативный review.
    rows = build_normative_review(
        shortlist_rows=shortlist_rows,
        requirements_rows=requirements_rows,
        corpus_path=CORPUS_PATH,
        top_k=5,
        qdrant_store=QDRANT_STORE,
    )

    row = _find_row_by_tag(rows, tag_norm)

    # 2. Загружаем дополнительные слои.
    openai_norm_rows = _load_json(OPENAI_NORMATIVE_REVIEW_PATH)
    openai_review_rows = _load_json(OPENAI_CANDIDATE_REVIEWS_PATH)
    openai_price_rows = _load_json(OPENAI_CANDIDATE_PRICES_PATH)
    merged_catalog_rows = _load_json(MERGED_CATALOG_WITH_PRICES_PATH)

    openai_norm_index = _build_index_by_tag(openai_norm_rows)
    openai_review_index = _build_index_by_tag(openai_review_rows)
    openai_price_index = _build_prices_index_by_tag(openai_price_rows)

    merged_catalog_row = _find_row_by_tag(merged_catalog_rows, tag_norm)

    # 3. Если live normative row не нашёлся, fallback на grouped.
    if row is None and merged_catalog_row is not None:
        row = dict(merged_catalog_row)

    if row is None:
        return {
            "ok": False,
            "tag": tag_norm,
            "error": "tag_not_found",
            "message": f"Тег {tag_norm} не найден.",
        }

    row = dict(row)

    # 4. Официальные цены из catalog_with_prices_grouped.json.
    # ВАЖНО: не заменяем весь row, а только доклеиваем price_* поля.
    if merged_catalog_row is not None:
        row = _merge_candidate_price_into_row(row, merged_catalog_row)
        row = _merge_candidate_options_with_prices(row, merged_catalog_row)

    # 5. Ограничиваем таблицу 5 кандидатами.
    row = _limit_candidate_options(row)

    if refresh_ai:
        _refresh_openai_layers_parallel(tag_norm, row)

        openai_norm_rows = _load_json_or(OPENAI_NORMATIVE_REVIEW_PATH, [])
        openai_review_rows = _load_json_or(OPENAI_CANDIDATE_REVIEWS_PATH, [])
        openai_price_rows = _load_json_or(OPENAI_CANDIDATE_PRICES_PATH, [])

        openai_norm_index = _build_index_by_tag(openai_norm_rows)
        openai_review_index = _build_index_by_tag(openai_review_rows)
        openai_price_index = _build_prices_index_by_tag(openai_price_rows)
    
    # 6. LLM-текст подмешиваем только если он относится к тому же кандидату.
    llm_row = openai_norm_index.get(tag_norm)
    if _llm_row_matches_base_candidate(llm_row, row):
        row["llm_readable_explanation"] = llm_row.get("llm_readable_explanation") or ""
        row["llm_alternative_summary"] = llm_row.get("llm_alternative_summary") or ""
        row["llm_manual_review_note"] = llm_row.get("llm_manual_review_note") or ""
        row["llm_model"] = llm_row.get("llm_model") or ""

    review_row = openai_review_index.get(tag_norm)
    price_rows = openai_price_index.get(tag_norm, [])

    # 7. Fallback-цены OpenAI применяем только после official merge.
    # Если official price_found уже есть — fallback не перезатирает цену.
    row = _merge_candidate_with_fallback_price(row, price_rows)
    row = _merge_candidate_options_with_fallback_prices(row, price_rows)

    payload = {
        "ok": True,
        "tag": tag_norm,
        "result": row,
        "review_block": review_row,
        "price_rows_fallback": price_rows,
    }
    
    payload = _attach_grok_critic(payload, refresh=refresh_critic)
    
    return payload



app = FastAPI(title=APP_TITLE)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:3000"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

@app.on_event("startup")
def startup_event() -> None:
    global QDRANT_STORE
    if QDRANT_STORE is None:
        QDRANT_STORE = NormativeQdrantStore.load(
            qdrant_path=QDRANT_PATH,
            collection_name=QDRANT_COLLECTION,
            model_name=EMBEDDING_MODEL,
        )

@app.get("/", response_class=HTMLResponse)
def index() -> str:
    body = _render_form()
    return _render_page(body)

@app.get("/api/tag/{tag}", response_class=JSONResponse)
def api_tag(
    tag: str,
    refresh_ai: bool = False,
    refresh_critic: bool = False,
):
    payload = _build_tag_payload(
        tag,
        refresh_ai=refresh_ai,
        refresh_critic=refresh_critic,
    )
    status_code = 200 if payload.get("ok") else 404
    return JSONResponse(content=payload, status_code=status_code)

# @app.post("/check", response_class=HTMLResponse)
# def check_tag(tag: str = Form(...)) -> str:
#     shortlist_rows = _load_json(SHORTLIST_PATH)
#     requirements_rows = _load_json(REQUIREMENTS_PATH)

#     global QDRANT_STORE
#     if QDRANT_STORE is None:
#         raise RuntimeError("Qdrant store is not initialized.")

#     qdrant_store = QDRANT_STORE

#     rows = build_normative_review(
#         shortlist_rows=shortlist_rows,
#         requirements_rows=requirements_rows,
#         corpus_path=CORPUS_PATH,
#         top_k=5,
#         qdrant_store=qdrant_store,
#     )

#     row = _find_row_by_tag(rows, tag)
#     openai_norm_rows = _load_json(OPENAI_NORMATIVE_REVIEW_PATH)
#     openai_review_rows = _load_json(OPENAI_CANDIDATE_REVIEWS_PATH)
#     openai_price_rows = _load_json(OPENAI_CANDIDATE_PRICES_PATH)
    
#     merged_catalog_rows = _load_json(MERGED_CATALOG_WITH_PRICES_PATH)
#     merged_catalog_row = _find_row_by_tag(merged_catalog_rows, tag)

#     if row is None and merged_catalog_row is not None:
#         row = dict(merged_catalog_row)
#     elif row is not None and merged_catalog_row is not None:
#         row = _merge_candidate_price_into_row(row, merged_catalog_row)
#         row = _merge_candidate_options_with_prices(row, merged_catalog_row)

#     openai_norm_index = _build_index_by_tag(openai_norm_rows)
#     openai_review_index = _build_index_by_tag(openai_review_rows)
#     openai_price_index = _build_prices_index_by_tag(openai_price_rows)
#     if row is None:
#         body = _render_form(tag) + f"""
#         <div class="card">
#           <p>Тег <b>{html.escape(tag)}</b> не найден в shortlist/requirements.</p>
#         </div>
#         """
#         return _render_page(body)
    
    
#     tag_norm = str(tag or "").strip().upper()

#     llm_row = openai_norm_index.get(tag_norm)
#     if llm_row:
#         row = dict(row)
#         row["llm_readable_explanation"] = llm_row.get("llm_readable_explanation") or ""
#         row["llm_alternative_summary"] = llm_row.get("llm_alternative_summary") or ""
#         row["llm_manual_review_note"] = llm_row.get("llm_manual_review_note") or ""
#         row["llm_model"] = llm_row.get("llm_model") or ""

#     review_row = openai_review_index.get(tag_norm)
#     price_rows = openai_price_index.get(tag_norm, [])

#     body = (
#         _render_form(tag)
#         + _render_result(row, price_rows=price_rows)
#         + _render_review_block(review_row)
#     )
#     return _render_page(body)

@app.post("/check", response_class=HTMLResponse)
def check_tag(tag: str = Form(...)) -> str:
    payload = _build_tag_payload(tag)

    if not payload.get("ok"):
        body = _render_form(tag) + f"""
        <div class="card">
          <p>Тег <b>{html.escape(tag)}</b> не найден в shortlist/requirements.</p>
        </div>
        """
        return _render_page(body)

    row = payload.get("result") or {}
    review_row = payload.get("review_block")
    price_rows = payload.get("price_rows_fallback") or []

    body = (
        _render_form(tag)
        + _render_result(row, price_rows=price_rows)
        + _render_review_block(review_row)
    )
    return _render_page(body)