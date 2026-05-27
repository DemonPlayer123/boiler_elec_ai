from __future__ import annotations

import argparse
import json
import re
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Iterable
from urllib.parse import urljoin, urlparse
import html as html_lib

import requests
from bs4 import BeautifulSoup


OUTPUT_DEFAULT = Path("data/output/runs/25-05/price_catalog.json")

CATEGORY_URLS = [
    # CHINT
    "https://chint.ru/catalog/oborudovanie_nizkogo_napryazheniya/modulnye_apparaty_raspredeleniya_elektroenergii/modulnye_avtomaticheskie_vyklyuchateli/filter/clear/apply/",
    "https://chint.ru/catalog/oborudovanie_nizkogo_napryazheniya/oborudovanie_dlya_zashchity_i_upravleniya_dvigatelem/avtomaticheskie_vyklyuchateli_dlya_zashchity_elektrodvigatelya/",
    "https://chint.ru/catalog/oborudovanie_nizkogo_napryazheniya/silovye_apparaty_raspredeleniya_elektroenergii/avtomaticheskie_vyklyuchateli_v_litom_korpuse/",
    "https://chint.ru/catalog/oborudovanie_nizkogo_napryazheniya/modulnye_apparaty_differentsialnoy_zashchity/differentsialnye_avtomaticheskie_vyklyuchateli/",


    # DEKRAFT
    "https://www.dek.ru/shop/modulnoe-oborudovanie/avtomaticheskie-vyklyuchateli",
    "https://www.dek.ru/shop/modulnoe-oborudovanie/differencialnye-avtomaty",
    "https://www.dek.ru/shop/puskoreguliruyushchaya-apparatura/avtomaticheskie-vyklyuchateli-zashchity-elektrodvigatelya",


    # KEAZ MCB
    "https://keaz.ru/catalog/ustroystva-na-din-reyku/modulnie-avtomaticheskie-vikluchateli/va47-29-modulnie-avtomaticheskie-vikluchateli-na-toki-do-63a-noviy#?sort=statsPercent&reverse=false&countProductsPerPage=30&page=1",
    "https://keaz.ru/catalog/ustroystva-na-din-reyku/modulnie-avtomaticheskie-vikluchateli/va47-100-modulnie-avtomaticheskie-vikluchateli-na-toki-do-100a-noviy#?sort=statsPercent&reverse=false&countProductsPerPage=30&page=1",
    "https://keaz.ru/catalog/ustroystva-na-din-reyku/modulnie-avtomaticheskie-vikluchateli/optidin-bm63-45ka-modulnie-vikluchateli-na-peremenniy-tok-do-63a#?sort=statsPercent&reverse=false&countProductsPerPage=30&page=1",
    "https://keaz.ru/catalog/ustroystva-na-din-reyku/modulnie-avtomaticheskie-vikluchateli/optidin-bm125-modulnie-avtomaticheskie-vikluchateli-na-toki-do-125a/optidin-bm125#?sort=statsPercent&reverse=false&countProductsPerPage=30&page=1",
    "https://keaz.ru/catalog/automat/avtomaticheskie-viklyuchateli-v-litom-korpuse/va51-blochnie-avtomaticheskie-vikluchateli-na-toki-ot-16a-do-630a/va51-35#?sort=statsPercent&reverse=false&countProductsPerPage=30&page=1",
    "https://keaz.ru/catalog/automat/avtomaticheskie-viklyuchateli-v-litom-korpuse/va57-blochnie-avtomaticheskie-vikluchateli-na-toki-ot-16a-do-630a/va57-35#?sort=statsPercent&reverse=false&countProductsPerPage=30&page=1",
    "https://keaz.ru/catalog/automat/avtomaticheskie-viklyuchateli-v-litom-korpuse/va04-blochnie-avtomaticheskie-vikluchateli-na-toki-ot-16a-do-400a/va04-36#?sort=statsPercent&reverse=false&countProductsPerPage=30&page=1",

    # KEAZ RCBO / RCD branch as requested
    "https://keaz.ru/catalog/ustroystva-na-din-reyku/uzo-diff/avdt32#?sort=statsPercent&reverse=false&countProductsPerPage=30&page=1",
    "https://keaz.ru/catalog/ustroystva-na-din-reyku/uzo-diff/ad-avtomaticheskie-vikluchateli-differencialnogo-toka-na-toki-do-63a-noviy#?sort=statsPercent&reverse=false&countProductsPerPage=30&page=1",
    "https://keaz.ru/catalog/ustroystva-na-din-reyku/uzo-diff/optidin-d63-6ka-avtomaticheskie-vikluchateli-differencialnogo-toka-na-toki-do-40a#?sort=statsPercent&reverse=false&countProductsPerPage=30&page=1",
    "https://keaz.ru/catalog/ustroystva-na-din-reyku/uzo-diff/ad32-avtomaticheskie-vikluchateli-differencialnogo-toka-na-toki-do-40a#?sort=statsPercent&reverse=false&countProductsPerPage=30&page=1",
    "https://keaz.ru/catalog/ustroystva-na-din-reyku/uzo-diff/optidin-d63-45ka-avtomaticheskie-vikluchateli-differencialnogo-toka-na-toki-do-40a#?sort=statsPercent&reverse=false&countProductsPerPage=30&page=1",
    "https://keaz.ru/catalog/ustroystva-na-din-reyku/uzo-diff/optidin-d63#?sort=statsPercent&reverse=false&countProductsPerPage=30&page=1",
    "https://keaz.ru/catalog/ustroystva-na-din-reyku/modulnie-avtomaticheskie-vikluchateli/va47-29-modulnie-avtomaticheskie-vikluchateli-na-toki-do-63a-noviy#?sort=statsPercent&reverse=false&countProductsPerPage=30&page=1",
    "https://keaz.ru/catalog/ustroystva-na-din-reyku/modulnie-avtomaticheskie-vikluchateli/va47-100-modulnie-avtomaticheskie-vikluchateli-na-toki-do-100a-noviy#?sort=statsPercent&reverse=false&countProductsPerPage=30&page=1",
    "https://keaz.ru/catalog/ustroystva-na-din-reyku/modulnie-avtomaticheskie-vikluchateli/optidin-bm63-45ka-modulnie-vikluchateli-na-peremenniy-tok-do-63a#?sort=statsPercent&reverse=false&countProductsPerPage=30&page=1",
    "https://keaz.ru/catalog/ustroystva-na-din-reyku/modulnie-avtomaticheskie-vikluchateli/optidin-bm125-modulnie-avtomaticheskie-vikluchateli-na-toki-do-125a/optidin-bm125#?sort=statsPercent&reverse=false&countProductsPerPage=30&page=1"
]


HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0 Safari/537.36"
    ),
    "Accept-Language": "ru-RU,ru;q=0.9,en;q=0.8",
}


@dataclass
class PriceItem:
    vendor: str
    source_domain: str
    category_url: str
    product_url: str
    title: str
    article: str | None
    price_rub: float | None
    currency: str | None
    availability: str | None
    source_type: str
    raw_price_text: str | None


def _sleep() -> None:
    time.sleep(1.0)


def _get(session: requests.Session, url: str, attempts: int = 3) -> requests.Response:
    last_exc = None

    for attempt in range(1, attempts + 1):
        try:
            resp = session.get(
                url,
                headers=HEADERS,
                timeout=(20, 90),  # connect timeout, read timeout
            )
            resp.raise_for_status()
            return resp

        except requests.RequestException as exc:
            last_exc = exc
            print(f"  [RETRY {attempt}/{attempts}] {url} -> {exc}")
            time.sleep(1.5 * attempt)

    raise last_exc

def _post_json(
    session: requests.Session,
    url: str,
    attempts: int = 3,
    data: dict | None = None,
) -> dict:
    last_exc = None

    for attempt in range(1, attempts + 1):
        try:
            resp = session.post(
                url,
                headers={
                    **HEADERS,
                    "X-Requested-With": "XMLHttpRequest",
                    "Accept": "application/json, text/plain, */*",
                    "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8",
                },
                data=data or {},
                timeout=(20, 90),
            )
            resp.raise_for_status()
            return resp.json()

        except requests.RequestException as exc:
            last_exc = exc
            print(f"  [RETRY POST {attempt}/{attempts}] {url} -> {exc}")
            time.sleep(1.5 * attempt)
        except ValueError as exc:
            last_exc = exc
            print(f"  [RETRY POST {attempt}/{attempts}] invalid JSON: {url} -> {exc}")
            time.sleep(1.5 * attempt)

    raise last_exc

def _clean_text(text: str | None) -> str:
    if not text:
        return ""
    return re.sub(r"\s+", " ", text).strip()


def _parse_price_to_float(text: str | None) -> float | None:
    if not text:
        return None

    s = text.replace("\xa0", " ").replace(",", ".")
    s = re.sub(r"\s+", " ", s).strip()

    # Сначала пытаемся вытащить именно число перед ₽ / руб
    m = re.search(r"(\d[\d ]*(?:\.\d+)?)\s*(?:₽|руб\.?|RUB)", s, re.I)
    if m:
        candidate = m.group(1).replace(" ", "")
        try:
            return float(candidate)
        except ValueError:
            pass

    # Иначе берем самое длинное разумное число, но только если оно похоже на цену
    matches = re.findall(r"\d[\d ]*(?:\.\d+)?", s)
    if not matches:
        return None

    candidate = max(matches, key=len).replace(" ", "")
    if len(candidate) < 3:
        return None

    try:
        return float(candidate)
    except ValueError:
        return None


def _dedupe_keep_order(urls: Iterable[str]) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for url in urls:
        if url and url not in seen:
            seen.add(url)
            out.append(url)
    return out


def _vendor_from_url(url: str) -> str:
    host = urlparse(url).netloc.lower()
    if "chint" in host:
        return "CHINT"
    if "dek" in host:
        return "DEKRAFT"
    if "keaz" in host:
        return "KEAZ"
    return host

def _detect_dekraft_category_kind(category_url: str) -> str:
    low = (category_url or "").lower()

    if "differencialnye-avtomaty" in low:
        return "rcbo"

    if "avtomaticheskie-vyklyuchateli-zashchity-elektrodvigatelya" in low:
        return "mpcb"

    return "mcb"


def _extract_json_ld_article_and_price(soup: BeautifulSoup) -> dict:
    for tag in soup.find_all("script", attrs={"type": "application/ld+json"}):
        raw = tag.string or tag.get_text(" ", strip=True)
        if not raw:
            continue
        try:
            data = json.loads(raw)
        except Exception:
            continue

        items = data if isinstance(data, list) else [data]
        for item in items:
            if not isinstance(item, dict):
                continue
            if item.get("@type") in {"Product", "Offer"} or "offers" in item:
                article = item.get("sku") or item.get("mpn")
                title = item.get("name")
                offers = item.get("offers")
                price = None
                currency = None
                availability = None

                if isinstance(offers, dict):
                    price = offers.get("price")
                    currency = offers.get("priceCurrency")
                    availability = offers.get("availability")
                elif isinstance(item, dict) and item.get("@type") == "Offer":
                    price = item.get("price")
                    currency = item.get("priceCurrency")
                    availability = item.get("availability")

                return {
                    "title": _clean_text(str(title) if title else ""),
                    "article": _clean_text(str(article) if article else ""),
                    "price": _parse_price_to_float(str(price) if price is not None else None),
                    "currency": _clean_text(str(currency) if currency else ""),
                    "availability": _clean_text(str(availability) if availability else ""),
                }

    return {}

def _extract_chint_listing_items(category_url: str, html: str) -> list[PriceItem]:
    soup = BeautifulSoup(html, "html.parser")
    items: list[PriceItem] = []

    for a in soup.select("a[href]"):
        href = a.get("href", "")
        full_url = urljoin(category_url, href)
        title = _clean_text(a.get_text(" ", strip=True))

        # 1. базовая фильтрация
        if not _is_chint_product_url(full_url):
            continue
        if not _is_valid_product_title(title):
            continue

        # 2. ищем ближайший контейнер, где реально есть цена
        container = a
        found_container = None
        for _ in range(8):
            parent = getattr(container, "parent", None)
            if parent is None:
                break
            container = parent
            text = _clean_text(container.get_text(" ", strip=True))
            if "₽" in text or "руб" in text.lower():
                found_container = container
                break

        if found_container is None:
            continue

        container_text = _clean_text(found_container.get_text(" ", strip=True))

        # 3. вытаскиваем именно кусок с ценой
        raw_price_text = None
        m = re.search(r"(\d[\d ]*(?:[.,]\d+)?)\s*(₽|руб\.?)", container_text, re.I)
        if m:
            raw_price_text = f"{m.group(1)} {m.group(2)}"

        price = _parse_price_to_float(raw_price_text or container_text)
        if price is None:
            continue

        # 4. дополнительный защитный барьер от мусора
        if price <= 0 or price > 10_000_000:
            continue

        items.append(
            PriceItem(
                vendor="CHINT",
                source_domain="chint.ru",
                category_url=category_url,
                product_url=full_url,
                title=title,
                article=None,
                price_rub=price,
                currency="RUB",
                availability=None,
                source_type="official_site_listing",
                raw_price_text=raw_price_text,
            )
        )

    # убираем дубли
    uniq: list[PriceItem] = []
    seen: set[tuple[str, str]] = set()
    for item in items:
        key = (item.product_url, item.title)
        if key not in seen:
            seen.add(key)
            uniq.append(item)

    return uniq

def _extract_dekraft_listing_items(category_url: str, html: str) -> list[PriceItem]:
    soup = BeautifulSoup(html, "html.parser")
    items: list[PriceItem] = []
    seen_urls: set[str] = set()

    # У DEKRAFT карточки товаров размечены как item-page
    main_grid = soup.select_one(".search-page-result__grid")
    if main_grid:
        cards = main_grid.select(".item-page-wrapper .item-page, .item-page")
    else:
        cards = soup.select(
            ".item-page, "
            "[itemtype='http://schema.org/Product'], "
            "[itemtype='https://schema.org/Product']"
        )
        
    print(f"    [DEBUG] DEKRAFT cards found: {len(cards)}")

    for card in cards:
        # product url
        a = card.select_one("a.item-page__img[href], a[itemprop='url'][href]")
        if not a:
            continue
        product_url = urljoin(category_url, a.get("href", "").strip())
        if not product_url or product_url in seen_urls:
            continue
        if "/tovar/" not in product_url:
            continue

        # article / sku
        article = None
        sku_node = card.select_one("[itemprop='sku']")
        if sku_node:
            article = _clean_text(sku_node.get_text(" ", strip=True))
        if not article:
            m_article = re.search(r"\b(\d{4,}DEK)\b", _clean_text(card.get_text(' ', strip=True)), re.I)
            if m_article:
                article = m_article.group(1).strip()

        # title
        title = None
        title_node = card.select_one("[itemprop='name']")
        if title_node:
            title = _clean_text(title_node.get_text(" ", strip=True))
        if not title:
            for sel in [".item-page__title", "h3", "h4", "a[href*='/tovar/']"]:
                node = card.select_one(sel)
                if node:
                    txt = _clean_text(node.get_text(" ", strip=True))
                    if txt and ("Автоматический выключатель" in txt or "ВА-" in txt):
                        title = txt
                        break

        # series
        series = None
        cat_node = card.select_one("[itemprop='category']")
        if cat_node:
            series = _clean_text(cat_node.get_text(" ", strip=True))
        if not series:
            full_text = _clean_text(card.get_text(" ", strip=True))
            m_series = re.search(r"Серия:\s*([A-Za-zА-Яа-я0-9.\- ]+)", full_text, re.I)
            if m_series:
                series = _clean_text(m_series.group(1))

        # price
        raw_price_text = None
        price = None

        price_attr = card.get("data-product-price")
        if price_attr:
            raw_price_text = f"{price_attr} ₽"
            price = _parse_price_to_float(price_attr)

        if price is None:
            price_node = card.select_one("[itemprop='price'], [data-product-price]")
            if price_node:
                raw_price_text = price_node.get("content") or price_node.get("data-product-price") or price_node.get_text(" ", strip=True)
                price = _parse_price_to_float(raw_price_text)

        if price is None:
            full_text = _clean_text(card.get_text(" ", strip=True))
            m_price = re.search(r"(\d[\d ]*(?:[.,]\d+)?)\s*₽", full_text, re.I)
            if m_price:
                raw_price_text = f"{m_price.group(1)} ₽"
                price = _parse_price_to_float(raw_price_text)

        if not title or price is None:
            continue

        if series and series not in title:
            title = f"{title} [{series}]"

        seen_urls.add(product_url)
        items.append(
            PriceItem(
                vendor="DEKRAFT",
                source_domain="dek.ru",
                category_url=category_url,
                product_url=product_url,
                title=title,
                article=article,
                price_rub=price,
                currency="RUB",
                availability=None,
                source_type="official_site_listing",
                raw_price_text=raw_price_text,
            )
        )

    return items

def _normalize_dekraft_href(base_url: str, href: str) -> str:
    href = (href or "").strip()
    if not href:
        return ""
    href = html_lib.unescape(href)
    return urljoin(base_url, href)

def _extract_dekraft_article_from_url(product_url: str) -> str | None:
    if not product_url:
        return None

    m = re.search(r"/tovar/([0-9A-Z\-]+)", product_url, re.I)
    if not m:
        return None

    article = (m.group(1) or "").strip().upper()
    if not article:
        return None

    return article

def _normalize_dekraft_article(article: str | None) -> str | None:
    if not article:
        return None

    article = _clean_text(str(article)).upper()
    article = article.replace("АРТИКУЛ", "").replace("КОД ТОВАРА", "").strip(" :#")

    # вытащим похожий на реальный артикул фрагмент
    m = re.search(r"\b([0-9]{4,}[A-Z]+)\b", article)
    if m:
        return m.group(1)

    # если строка совсем короткая/мусорная — отбрасываем
    if len(article) < 5:
        return None

    # если нет цифр, это почти наверняка не артикул
    if not re.search(r"\d", article):
        return None

    return article

def _dekraft_listing_signature(items: list[PriceItem]) -> set[str]:
    sig: set[str] = set()
    for x in items:
        key = x.article or x.product_url or x.title
        if key:
            sig.add(key)
    return sig

def _collect_dekraft_series_urls(category_url: str, html: str) -> list[str]:
    soup = BeautifulSoup(html, "html.parser")
    urls: list[str] = []

    category_kind = _detect_dekraft_category_kind(category_url)

    for a in soup.select("a[href]"):
        href = a.get("href", "")
        full_url = _normalize_dekraft_href(category_url, href)
        if not full_url:
            continue

        low = full_url.lower()

        if "dek.ru" not in low:
            continue
        if "/tovar/" in low:
            continue
        if "/all-products/" not in low:
            continue

        if category_kind == "mcb":
            if (
                "avtomaticheskie-viklyuchateli" not in low
                and "avtomaticheskie-vyklyuchateli" not in low
            ):
                continue

        elif category_kind == "rcbo":
            if "differencialnye-avtomaty" not in low and "dif-103" not in low:
                continue

        elif category_kind == "mpcb":
            if "zashchity-dvigatelya" not in low and "va-430" not in low:
                continue

        urls.append(full_url)

    urls = _dedupe_keep_order(urls)

    print(f"  [DEBUG] DEKRAFT series candidates: {len(urls)}")
    for u in urls[:20]:
        print("    [SERIES_URL]", u)

    return urls

def _collect_dekraft_product_urls_from_series_page(series_url: str, html: str) -> list[str]:
    soup = BeautifulSoup(html, "html.parser")
    urls: list[str] = []

    for a in soup.select("a[href]"):
        href = (a.get("href") or "").strip()
        full_url = _normalize_dekraft_href(series_url, href)
        if not full_url:
            continue

        low = full_url.lower()
        if "dek.ru" not in low:
            continue
        if "/tovar/" not in low:
            continue

        urls.append(full_url)

    urls = _dedupe_keep_order(urls)

    print(f"      [DEBUG] product urls on series page: {len(urls)}")
    for u in urls[:10]:
        print("        [PRODUCT_URL]", u)

    return urls

def _debug_dump_dekraft_links(category_url: str, html: str) -> None:
    soup = BeautifulSoup(html, "html.parser")
    rows: list[tuple[str, str]] = []

    for a in soup.select("a[href]"):
        href = (a.get("href") or "").strip()
        text = _clean_text(a.get_text(" ", strip=True))
        full_url = _normalize_dekraft_href(category_url, href)
        rows.append((full_url, text))

    print(f"  [DEBUG] total hrefs on DEKRAFT page: {len(rows)}")

    for i, (u, t) in enumerate(rows[:120], start=1):
        print(f"    [HREF {i}] {u} | text={t[:80]}")

def _collect_dekraft_items_via_series(
    session: requests.Session,
    root_category_url: str,
    root_html: str,
) -> list[PriceItem]:
    series_urls = _collect_dekraft_series_urls(root_category_url, root_html)
    print(f"  DEKRAFT series urls found: {len(series_urls)}")

    all_items: list[PriceItem] = []
    seen_product_urls: set[str] = set()

    for idx, series_url in enumerate(series_urls, start=1):
        try:
            print(f"    [SERIES {idx}/{len(series_urls)}] {series_url}")
            resp = _get(session, series_url)
            _sleep()
            series_html = resp.text
        except Exception as e:
            print(f"    [SERIES {idx}/{len(series_urls)}] error: {series_url} -> {e}")
            continue

        product_urls = _collect_dekraft_product_urls_from_series_page(series_url, series_html)

        for p_idx, product_url in enumerate(product_urls, start=1):
            if product_url in seen_product_urls:
                continue
            seen_product_urls.add(product_url)

            try:
                prod_resp = _get(session, product_url)
                _sleep()

                item = _extract_dekraft_product(
                    category_url=series_url,
                    product_url=product_url,
                    html=prod_resp.text,
                )
                if item is None:
                    print(f"      [{p_idx}/{len(product_urls)}] skip: parse failed -> {product_url}")
                    continue

                if not _is_valid_dekraft_product_title(item.title):
                    print(f"      [{p_idx}/{len(product_urls)}] skip: non-breaker -> {item.title}")
                    continue

                all_items.append(item)
                print(
                    f"      [{p_idx}/{len(product_urls)}] ok: "
                    f"{item.title[:70]} | article={item.article or '-'} | price={item.price_rub}"
                )

            except Exception as e:
                print(f"      [{p_idx}/{len(product_urls)}] error: {product_url} -> {e}")

    return all_items

def _is_chint_product_url(url: str) -> bool:
    if not url:
        return False

    bad_parts = [
        "#feedback-popup",
        "/filter/",
        "?count_pages=",
        "/nominalnyy_tok_a_",
        "/kharakteristika_",
        "/kolichestvo_polyusov_",
        "/nominalnaya_otklyuchayushchaya_sposobnost_",
        "/catalog/modulnye_avtomaticheskie_vyklyuchateli/",
        "/catalog/avtomaticheskie_vyklyuchateli_v_litom_korpuse/",
    ]

    bad_exact = {
        "https://chint.ru/catalog/",
        "https://chint.ru/catalog",
    }

    if url.rstrip("/") in {x.rstrip("/") for x in bad_exact}:
        return False

    if any(part in url for part in bad_parts):
        return False

    if "/catalog/" not in url:
        return False

    # Товарные карточки обычно глубже по вложенности
    if url.count("/") < 9:
        return False

    return True

def _is_valid_product_title(title: str) -> bool:
    if not title:
        return False

    title = title.strip()

    bad_exact = {
        "Оставить заявку",
        "Каталог",
        "Показать",
        "B",
        "C",
        "D",
        "K",
        "L",
        "1P+N",
        "2P+N",
        "3P+N",
        "4P+N",
        "Однополюсные",
        "Двухполюсные",
        "Трехполюсные",
    }
    if title in bad_exact:
        return False

    if re.fullmatch(r"\d+", title):
        return False

    if re.fullmatch(r"[A-ZА-Яa-zа-я0-9.+\-кАA ]+\(\s*\d+\s*\)", title):
        return False

    bad_starts = (
        "Доп.контакты",
        "Расцепитель",
        "Выносная рукоятка",
        "Шасси",
        "Внешний вывод",
        "Аварийно",
        "Аварийновсп",
        "Ручной поворотный привод",
    )
    if title.startswith(bad_starts):
        return False

    good_markers = (
        "Авт. выкл.",
        "Автоматический выключатель",
        "Авт. выкл. защиты двигателя",
        "Авт. выкл. для защиты эл. двигателя",
        "АВДТ",
        "дифференциального тока",
    )
    return any(marker in title for marker in good_markers)

def _is_valid_dekraft_product_title(title: str) -> bool:
    title = _clean_text(title)
    if not title:
        return False

    low = title.lower()

    bad_markers = [
        "контакт дополнительный",
        "контакт сигнальный",
        "расц.",
        "расцепитель",
        "незав.",
        "независимого расцепителя",
        "аксессуар",
        "заглушка",
        "дк-201",
        "ск-201",
        "рмн-201",
        "рмк-201",
        "нд201",
    ]
    if any(x in low for x in bad_markers):
        return False

    good_markers = [
        "автоматический выключатель",
        "авт. выкл.",
        "авдт",
        "диф-103",
        "защиты двигателя",
        "ва-",
        "ва431",
        "ва432",
    ]
    return any(x in low for x in good_markers)

def _is_valid_keaz_product_title(title: str) -> bool:
    title = _clean_text(title)
    if not title:
        return False

    low = title.lower()

    bad_markers = [
        "аксессуар",
        "аксессуары",
        "контактор",
        "реле",
        "корпуса",
        "корпус",
        "автоматизация",
        "датчики",
        "источники питания",
        "устройства плавного пуска",
        "преобразователи частоты",
        "выключатели нагрузки",
        "выключатели-разъединители",
        "автоматический ввод резерва",
        "розетки для реле",
        "индикаторы",
        "кнопки",
        "зуммеры",
        "потенциометры",
    ]
    if any(x in low for x in bad_markers):
        return False

    good_markers = [
        "автоматические выключатели",
        "модульные автоматические выключатели",
        "автоматические выключатели дифференциального тока",
        "дифференциального тока",
        "авдт32",
        "ад12",
        "ад14",
        "ад32",
        "optidin d63",
        "optidin vd63",
        "ва47-29",
        "ва47-100",
        "optidin bm63",
        "optidin bm125",
        "ва51-35",
        "ва57-35",
        "ва04-36",
    ]
    return any(x in low for x in good_markers)

def _is_valid_keaz_product_url(url: str, category_url: str) -> bool:
    if not url:
        return False

    low = url.lower()
    parsed_url = urlparse(url)
    path = parsed_url.path.rstrip("/")

    if "keaz.ru" not in low:
        return False

    # берем не полный slug серии, а родительскую ветку раздела
    # .../modulnie-avtomaticheskie-vikluchateli
    category_path = urlparse(category_url).path.rstrip("/")
    parent_path = category_path.rsplit("/", 1)[0]

    # только внутри нужной ветки раздела
    if not path.startswith(parent_path):
        return False

    # сам родительский раздел не берем
    if path == parent_path:
        return False

    # отсекаем совсем верхнеуровневые вещи
    if path.count("/") < parent_path.count("/") + 1:
        return False

    bad_markers = [
        "aksessu",
        "uzo",
        "diff",
        "kontaktor",
        "rele",
        "korpus",
        "avtomatizaciya",
        "datchik",
        "istochniki-pitaniya",
        "plavnogo-puska",
        "preobrazovateli-chastoti",
        "vikluchateli-nagruzki",
        "razediniteli",
        "vvod-rezerva",
        "indikator",
        "knopk",
        "zummer",
        "potenciometr",
    ]
    if any(x in low for x in bad_markers):
        return False

    good_markers = [
        "va47-29",
        "va47-100",
        "optidin-bm63",
        "optidin-bm125",
        "modulnie-avtomaticheskie-vikluchateli",
    ]
    return any(x in low for x in good_markers)

# def _collect_chint_pagination_urls(category_url: str, html: str) -> list[str]:
#     soup = BeautifulSoup(html, "html.parser")

#     # базовый URL категории без номера страницы
#     base_url = category_url.split("?")[0].rstrip("/")

#     # собираем все номера страниц, которые видим в HTML
#     page_nums: set[int] = set()

#     for a in soup.select("a[href]"):
#         href = a.get("href", "")
#         full_url = urljoin(category_url, href)

#         if "chint.ru" not in full_url:
#             continue
#         if "PAGEN_" not in full_url:
#             continue

#         m = re.search(r"PAGEN_\d+=(\d+)", full_url)
#         if m:
#             page_nums.add(int(m.group(1)))

#     # дополнительно смотрим просто текст кнопок пагинации: 58, 59, 60, 61, 62
#     for a in soup.select("a, span"):
#         text = _clean_text(a.get_text(" ", strip=True))
#         if text.isdigit():
#             n = int(text)
#             if 1 <= n <= 10000:
#                 page_nums.add(n)

#     if not page_nums:
#         return [category_url]

#     max_page = max(page_nums)

#     # восстановим имя параметра PAGEN_X из уже найденных href
#     page_param = None
#     for a in soup.select("a[href]"):
#         href = a.get("href", "")
#         full_url = urljoin(category_url, href)
#         m = re.search(r"(PAGEN_\d+)=\d+", full_url)
#         if m:
#             page_param = m.group(1)
#             break

#     if not page_param:
#         page_param = "PAGEN_1"

#     urls = [base_url + "/"]
#     for n in range(2, max_page + 1):
#         urls.append(f"{base_url}/?{page_param}={n}")

#     return urls

def _collect_chint_pagination_urls(category_url: str, html: str) -> list[str]:
    soup = BeautifulSoup(html, "html.parser")

    base_url = category_url.split("?")[0].rstrip("/")

    page_nums: set[int] = {1}
    page_param = None

    for a in soup.select("a[href]"):
        href = a.get("href", "")
        full_url = urljoin(category_url, href)

        if "chint.ru" not in full_url:
            continue

        m = re.search(r"(PAGEN_\d+)=(\d+)", full_url)
        if not m:
            continue

        page_param = m.group(1)
        page_nums.add(int(m.group(2)))

    if not page_nums:
        return [category_url]

    max_page = max(page_nums)

    if not page_param:
        page_param = "PAGEN_1"

    urls = [base_url + "/"]
    for n in range(2, max_page + 1):
        urls.append(f"{base_url}/?{page_param}={n}")

    return urls

def _collect_dekraft_pagination_urls(category_url: str, html: str) -> list[str]:
    soup = BeautifulSoup(html, "html.parser")
    page_nums: set[int] = {1}

    category_base = category_url.split("?")[0].rstrip("/").lower()

    for a in soup.select("a[href]"):
        href = a.get("href", "")
        full_url = urljoin(category_url, href)

        if "dek.ru" not in full_url.lower():
            continue

        full_base = full_url.split("?")[0].rstrip("/").lower()

        if full_base != category_base:
            continue

        m = re.search(r"(?:[?&]page=|/page/)(\d+)", full_url, re.I)
        if m:
            page_nums.add(int(m.group(1)))

    for node in soup.select("a, span"):
        text = _clean_text(node.get_text(" ", strip=True))
        if text.isdigit():
            n = int(text)
            if 1 <= n <= 1000:
                page_nums.add(n)

    max_page = max(page_nums) if page_nums else 1

    base_url = category_url.split("?")[0].rstrip("/")
    urls = [base_url]
    for n in range(2, max_page + 1):
        urls.append(f"{base_url}?page={n}")

    return urls

def _enrich_chint_article(session: requests.Session, item: PriceItem) -> PriceItem:
    try:
        resp = _get(session, item.product_url)
        _sleep()
    except Exception:
        return item

    soup = BeautifulSoup(resp.text, "html.parser")
    meta = _extract_json_ld_article_and_price(soup)

    article = meta.get("article") or ""
    if not article:
        page_text = soup.get_text(" ", strip=True)
        m = re.search(r"(?:Артикул|Код товара)\s*:?\s*([A-Za-zА-Яа-я0-9\\-_/]+)", page_text, re.I)
        if m:
            article = m.group(1).strip()

    if article:
        item.article = article

    # если в карточке цена есть и она точнее — можно обновить
    if item.price_rub is None:
        raw_price_text = None
        for sel in [
            ".catalog-detail__price-current",
            ".product-detail-price",
            ".price",
            "[itemprop='price']",
        ]:
            node = soup.select_one(sel)
            if node:
                raw_price_text = _clean_text(node.get_text(" ", strip=True))
                break

        detail_price = meta.get("price")
        if detail_price is None:
            detail_price = _parse_price_to_float(raw_price_text)

        if detail_price is not None:
            item.price_rub = detail_price
            item.currency = "RUB"
            item.raw_price_text = raw_price_text or item.raw_price_text

    return item

def _collect_product_links_chint(category_url: str, html: str) -> list[str]:
    soup = BeautifulSoup(html, "html.parser")
    links: list[str] = []

    for a in soup.select("a[href]"):
        href = a.get("href", "")
        text = _clean_text(a.get_text(" ", strip=True))
        full = urljoin(category_url, href)

        if "/catalog/" not in full:
            continue
        if full == category_url:
            continue
        if any(
            bad in full
            for bad in [
                "/filter/",
                "/oborudovanie_nizkogo_napryazheniya/",
                "/shop/",
                "#",
            ]
        ) and full.count("/") < 8:
            continue

        if text or "/catalog/" in full:
            links.append(full)

    return _dedupe_keep_order(links)


def _collect_product_links_dekraft(category_url: str, html: str) -> list[str]:
    soup = BeautifulSoup(html, "html.parser")
    links: list[str] = []

    for a in soup.select("a[href]"):
        href = a.get("href", "")
        full = urljoin(category_url, href)
        if "/product/" in full or "/shop/" in full:
            if full != category_url:
                links.append(full)

    return _dedupe_keep_order(links)


def _collect_product_links_keaz(category_url: str, html: str) -> list[str]:
    soup = BeautifulSoup(html, "html.parser")
    links: list[str] = []

    for a in soup.select("a[href]"):
        href = a.get("href", "")
        full = urljoin(category_url, href)

        if "#" in full:
            continue
        if not _is_valid_keaz_product_url(full, category_url):
            continue

        links.append(full)

    links = _dedupe_keep_order(links)

    print(f"  [DEBUG] KEAZ filtered links: {len(links)}")
    for u in links[:30]:
        print("    [KEAZ_LINK]", u)

    return links


def _collect_product_links(category_url: str, html: str) -> list[str]:
    host = urlparse(category_url).netloc.lower()
    if "chint" in host:
        return _collect_product_links_chint(category_url, html)
    if "dek" in host:
        return _collect_product_links_dekraft(category_url, html)
    if "keaz" in host:
        return _collect_product_links_keaz(category_url, html)
    return []


def _extract_chint_product(category_url: str, product_url: str, html: str) -> PriceItem | None:
    soup = BeautifulSoup(html, "html.parser")
    meta = _extract_json_ld_article_and_price(soup)

    title = (
        meta.get("title")
        or _clean_text(soup.select_one("h1").get_text(" ", strip=True) if soup.select_one("h1") else "")
    )

    raw_price_text = None
    for sel in [
        ".catalog-detail__price-current",
        ".product-detail-price",
        ".price",
        "[itemprop='price']",
    ]:
        node = soup.select_one(sel)
        if node:
            raw_price_text = _clean_text(node.get_text(" ", strip=True))
            break

    article = meta.get("article") or ""
    if not article:
        page_text = soup.get_text(" ", strip=True)
        m = re.search(r"(?:Артикул|Код товара)\s*:?\s*([A-Za-zА-Яа-я0-9\-_/]+)", page_text, re.I)
        if m:
            article = m.group(1).strip()

    price = meta.get("price")
    if price is None:
        price = _parse_price_to_float(raw_price_text)

    currency = meta.get("currency") or ("RUB" if price is not None else "")
    availability = meta.get("availability") or ""

    if not title:
        return None

    return PriceItem(
        vendor="CHINT",
        source_domain="chint.ru",
        category_url=category_url,
        product_url=product_url,
        title=title,
        article=article or None,
        price_rub=price,
        currency=currency or None,
        availability=availability or None,
        source_type="official_site",
        raw_price_text=raw_price_text,
    )


def _extract_dekraft_product(category_url: str, product_url: str, html: str) -> PriceItem | None:
    soup = BeautifulSoup(html, "html.parser")
    meta = _extract_json_ld_article_and_price(soup)

    title = (
        meta.get("title")
        or _clean_text(soup.select_one("h1").get_text(" ", strip=True) if soup.select_one("h1") else "")
    )

    raw_price_text = None
    for sel in [
        ".product-buy__price",
        ".product-card-price",
        ".price",
        "[itemprop='price']",
    ]:
        node = soup.select_one(sel)
        if node:
            raw_price_text = _clean_text(node.get_text(" ", strip=True))
            break

    article = meta.get("article") or ""
    article = _normalize_dekraft_article(article)

    if not article:
        page_text = soup.get_text(" ", strip=True)
        m = re.search(r"(?:Артикул|Код товара)\s*:?\s*([A-Za-zА-Яа-я0-9\-_/]+)", page_text, re.I)
        if m:
            article = _normalize_dekraft_article(m.group(1))

    # самый надежный fallback — артикул из URL /tovar/13100DEK
    if not article:
        article = _extract_dekraft_article_from_url(product_url)

    price = meta.get("price")
    if price is None:
        price = _parse_price_to_float(raw_price_text)

    currency = meta.get("currency") or ("RUB" if price is not None else "")
    availability = meta.get("availability") or ""

    if not title:
        return None

    return PriceItem(
        vendor="DEKRAFT",
        source_domain="dek.ru",
        category_url=category_url,
        product_url=product_url,
        title=title,
        article=article or None,
        price_rub=price,
        currency=currency or None,
        availability=availability or None,
        source_type="official_site",
        raw_price_text=raw_price_text,
    )

def _normalize_keaz_article(article: str | None) -> str | None:
    if not article:
        return None

    article = _clean_text(str(article))
    article = article.replace("Артикул", "").replace("Код товара", "").strip(" :#")

    if len(article) < 3:
        return None

    if article.lower() in {"у", "артикул"}:
        return None

    return article

def _normalize_keaz_price(value) -> float | None:
    if value is None:
        return None
    return _parse_price_to_float(str(value))

def _extract_keaz_items_from_json(category_url: str, payload: dict) -> list[PriceItem]:
    products = payload.get("products") or []
    items: list[PriceItem] = []
    seen_keys: set[str] = set()

    for p in products:
        if not isinstance(p, dict):
            continue

        article = _normalize_keaz_article(p.get("article"))
        title = _clean_text(p.get("name") or p.get("title") or "")
        price = _normalize_keaz_price(p.get("priceVat") or p.get("price") or p.get("price_vat"))

        if not title:
            continue
        if not _is_valid_keaz_product_title(title):
            continue

        key = article or title
        if key in seen_keys:
            continue
        seen_keys.add(key)

        product_id = p.get("id")
        product_url = category_url
        if product_id:
            product_url = f"{category_url}#product_id={product_id}"

        items.append(
            PriceItem(
                vendor="KEAZ",
                source_domain="keaz.ru",
                category_url=category_url,
                product_url=product_url,
                title=title,
                article=article,
                price_rub=price,
                currency="RUB" if price is not None else None,
                availability=None,
                source_type="official_site_json",
                raw_price_text=str(p.get("priceVat") or p.get("price") or "") or None,
            )
        )

    return items

def _fetch_keaz_series_json(session: requests.Session, category_url: str, max_pages: int = 20) -> list[PriceItem]:
    all_items: list[PriceItem] = []
    seen_keys: set[str] = set()

    for page in range(1, max_pages + 1):
        json_url = _build_keaz_json_page_url(category_url, page)
        form_data = _build_keaz_json_form_data(page=page, per_page=30)
        payload = _post_json(session, json_url, data=form_data)

        page_items = _extract_keaz_items_from_json(category_url.split("#")[0], payload)
        print(f"  [DEBUG] KEAZ JSON page {page}: {len(page_items)} items")

        total_pages = payload.get("totalPages")
        if total_pages is not None:
            try:
                total_pages = int(total_pages)
            except Exception:
                total_pages = None
        
        if not page_items:
            break

        added_on_page = 0
        for item in page_items:
            key = item.article or item.title
            if key in seen_keys:
                continue
            seen_keys.add(key)
            all_items.append(item)
            added_on_page += 1

        for x in page_items[:5]:
            print(
                f"    [KEAZ_JSON p{page}] {x.title[:70]} | article={x.article or '-'} | price={x.price_rub}"
            )

        # если страница вернула только дубли — дальше, скорее всего, смысла нет
        if added_on_page == 0:
            break
        
        if total_pages is not None and page >= total_pages:
            break
        
        # если страница неполная, вероятно это последняя
        if len(page_items) < 30:
            break

    return all_items

def _build_keaz_json_page_url(category_url: str, page: int) -> str:
    base_url = category_url.split("#")[0]
    sep = "&" if "?" in base_url else "?"
    return f"{base_url}{sep}json=1&countProductsPerPage=30&page={page}"

def _build_keaz_json_form_data(page: int, per_page: int = 30) -> dict:
    return {
        "sort": "statsPercent",
        "reverse": "0",
        "countProductsPerPage": str(per_page),
        "find": "",
        "page": str(page),
        "countPerPage": str(per_page),
        "keys[data]": "products",
        "keys[totalPages]": "totalPages",
        "keys[totalItems]": "totalItems",
        "isAjax": "true",
    }

def _extract_keaz_product(category_url: str, product_url: str, html: str) -> PriceItem | None:
    soup = BeautifulSoup(html, "html.parser")
    meta = _extract_json_ld_article_and_price(soup)

    title = (
        meta.get("title")
        or _clean_text(soup.select_one("h1").get_text(" ", strip=True) if soup.select_one("h1") else "")
    )

    raw_price_text = None
    for sel in [
        ".product-buy__price",
        ".product-price",
        ".price",
        "[itemprop='price']",
        "[data-price]",
    ]:
        node = soup.select_one(sel)
        if node:
            raw_price_text = (
                node.get("content")
                or node.get("data-price")
                or _clean_text(node.get_text(" ", strip=True))
            )
            break

    article = _normalize_keaz_article(meta.get("article"))

    if not article:
        page_text = soup.get_text(" ", strip=True)
        m = re.search(r"(?:Артикул|Код товара)\s*:?\s*([A-Za-zА-Яа-я0-9\-_/]+)", page_text, re.I)
        if m:
            article = _normalize_keaz_article(m.group(1))

    price = meta.get("price")
    if price is None:
        price = _parse_price_to_float(raw_price_text)

    currency = meta.get("currency") or ("RUB" if price is not None else "")
    availability = meta.get("availability") or ""

    if not title:
        return None

    return PriceItem(
        vendor="KEAZ",
        source_domain="keaz.ru",
        category_url=category_url,
        product_url=product_url,
        title=title,
        article=article or None,
        price_rub=price,
        currency=currency or None,
        availability=availability or None,
        source_type="official_site",
        raw_price_text=raw_price_text,
    )


def _extract_product(category_url: str, product_url: str, html: str) -> PriceItem | None:
    host = urlparse(product_url).netloc.lower()
    if "chint" in host:
        return _extract_chint_product(category_url, product_url, html)
    if "dek" in host:
        return _extract_dekraft_product(category_url, product_url, html)
    if "keaz" in host:
        return _extract_keaz_product(category_url, product_url, html)
    return None


def build_price_catalog(
    category_urls: list[str],
    out_path: Path,
    max_links_per_category: int = 120,
) -> None:
    session = requests.Session()
    session.headers.update(HEADERS)

    all_items: list[PriceItem] = []
    seen_product_urls: set[str] = set()

    for category_url in category_urls:
        try:
            print(f"[CATEGORY] {category_url}")
            resp = _get(session, category_url)
            _sleep()
        except Exception as e:
            print(f"  ERROR category: {e}")
            continue

        host = urlparse(category_url).netloc.lower()

        if "chint" in host:
            page_urls = _collect_chint_pagination_urls(category_url, resp.text)
            print(f"  found CHINT category pages: {len(page_urls)}")

            chint_items_all: list[PriceItem] = []
            seen_listing_urls: set[str] = set()

            for page_idx, page_url in enumerate(page_urls, start=1):
                try:
                    if page_idx == 1:
                        page_html = resp.text
                    else:
                        page_resp = _get(session, page_url)
                        _sleep()
                        page_html = page_resp.text

                    listing_items = _extract_chint_listing_items(page_url, page_html)

                    listing_items = [
                        x for x in listing_items
                        if _is_chint_product_url(x.product_url)
                        and _is_valid_product_title(x.title)
                        and x.price_rub is not None
                        and x.price_rub > 0
                        and x.price_rub < 10_000_000
                    ]

                    print(f"    page [{page_idx}/{len(page_urls)}] items: {len(listing_items)}")

                    for item in listing_items:
                        if item.product_url in seen_listing_urls:
                            continue
                        seen_listing_urls.add(item.product_url)
                        chint_items_all.append(item)

                except Exception as e:
                    print(f"    page [{page_idx}/{len(page_urls)}] error: {page_url} -> {e}")

            if max_links_per_category > 0:
                chint_items_all = chint_items_all[:max_links_per_category]

            print(f"  total CHINT listing items: {len(chint_items_all)}")

            for idx, item in enumerate(chint_items_all, start=1):
                try:
                    item = _enrich_chint_article(session, item)

                    if not _is_chint_product_url(item.product_url):
                        continue
                    if not _is_valid_product_title(item.title):
                        continue
                    if item.price_rub is None or item.price_rub <= 0 or item.price_rub >= 10_000_000:
                        continue

                    all_items.append(item)
                    print(
                        f"    [{idx}/{len(chint_items_all)}] ok: "
                        f"{item.vendor} | {item.title[:70]} | "
                        f"article={item.article or '-'} | price={item.price_rub}"
                    )
                except Exception as e:
                    print(f"    [{idx}/{len(chint_items_all)}] error: {item.product_url} -> {e}")

            continue
        
        if "dek.ru" in host:
            page_urls = _collect_dekraft_pagination_urls(category_url, resp.text)
            print(f"  found DEKRAFT category pages: {len(page_urls)}")

            print("  [DEBUG] first 10 DEKRAFT page urls:")
            for u in page_urls[:10]:
                print("   ", u)

            # --- Диагностика: действительно ли page=2 отличается от первой страницы
            first_items = _extract_dekraft_listing_items(category_url, resp.text)
            first_sig = _dekraft_listing_signature(first_items)

            second_items: list[PriceItem] = []
            second_sig: set[str] = set()

            if len(page_urls) >= 2:
                try:
                    page2_resp = _get(session, page_urls[1])
                    _sleep()
                    page2_html = page2_resp.text

                    debug_path1 = Path("data/output/runs/25-05/dekraft_page1_debug.html")
                    debug_path1.parent.mkdir(parents=True, exist_ok=True)
                    debug_path1.write_text(resp.text, encoding="utf-8")

                    debug_path2 = Path("data/output/runs/25-05/dekraft_page2_debug.html")
                    debug_path2.parent.mkdir(parents=True, exist_ok=True)
                    debug_path2.write_text(page2_html, encoding="utf-8")

                    second_items = _extract_dekraft_listing_items(page_urls[1], page2_html)
                    second_sig = _dekraft_listing_signature(second_items)

                    print(f"  [DEBUG] page1 items: {len(first_items)}")
                    print(f"  [DEBUG] page2 items: {len(second_items)}")
                    print(f"  [DEBUG] page1 uniq: {len(first_sig)}")
                    print(f"  [DEBUG] page2 uniq: {len(second_sig)}")
                    print(f"  [DEBUG] intersection: {len(first_sig & second_sig)}")

                except Exception as e:
                    print(f"  [DEBUG] failed to fetch DEKRAFT page2: {e}")

            # Если page2 повторяет page1 — уходим в обход серий
            use_series_fallback = False
            if second_sig and first_sig == second_sig:
                use_series_fallback = True
                print("  [FALLBACK] DEKRAFT page pagination returns duplicated content; switching to series crawl")

            dekraft_items_all: list[PriceItem] = []

            if use_series_fallback:
                dekraft_items_all = _collect_dekraft_items_via_series(
                    session=session,
                    root_category_url=category_url,
                    root_html=resp.text,
                )
            
            # if use_series_fallback:
            #     dekraft_items_all = _collect_dekraft_items_via_series(
            #         session=session,
            #         root_category_url=category_url,
            #         root_html=resp.text,
            #     )
            else:
                seen_listing_urls: set[str] = set()

                for page_idx, page_url in enumerate(page_urls, start=1):
                    try:
                        if page_idx == 1:
                            page_html = resp.text
                        else:
                            page_resp = _get(session, page_url)
                            _sleep()
                            page_html = page_resp.text

                        listing_items = _extract_dekraft_listing_items(page_url, page_html)
                        print(f"    page [{page_idx}/{len(page_urls)}] items: {len(listing_items)}")

                        for item in listing_items:
                            if item.product_url in seen_listing_urls:
                                continue
                            seen_listing_urls.add(item.product_url)
                            dekraft_items_all.append(item)

                    except Exception as e:
                        print(f"    page [{page_idx}/{len(page_urls)}] error: {page_url} -> {e}")

            if max_links_per_category > 0:
                dekraft_items_all = dekraft_items_all[:max_links_per_category]

            print(f"  total DEKRAFT listing items: {len(dekraft_items_all)}")

            for idx, item in enumerate(dekraft_items_all, start=1):
                all_items.append(item)
                print(
                    f"    [{idx}/{len(dekraft_items_all)}] ok: "
                    f"{item.vendor} | {item.title[:70]} | "
                    f"article={item.article or '-'} | price={item.price_rub}"
                )

            continue
        
        if "keaz.ru" in host:
                try:
                    keaz_items = _fetch_keaz_series_json(session, category_url)
                except Exception as e:
                    print(f"  ERROR KEAZ json fetch: {e}")
                    continue

                if max_links_per_category > 0:
                    keaz_items = keaz_items[:max_links_per_category]

                print(f"  total KEAZ json items: {len(keaz_items)}")

                for idx, item in enumerate(keaz_items, start=1):
                    all_items.append(item)
                    print(
                        f"    [{idx}/{len(keaz_items)}] ok: "
                        f"{item.vendor} | {item.title[:70]} | "
                        f"article={item.article or '-'} | price={item.price_rub}"
                    )

                continue

        links = _collect_product_links(category_url, resp.text)
        if max_links_per_category > 0:
            links = links[:max_links_per_category]

        print(f"  found product links: {len(links)}")

        for idx, product_url in enumerate(links, start=1):
            if product_url in seen_product_urls:
                continue
            seen_product_urls.add(product_url)

            try:
                prod_resp = _get(session, product_url)
                _sleep()
                item = _extract_product(category_url, product_url, prod_resp.text)
                if item is None:
                    print(f"    [{idx}/{len(links)}] skip: parse failed -> {product_url}")
                    continue

                if item.vendor == "KEAZ":
                    if not _is_valid_keaz_product_title(item.title):
                        print(f"    [{idx}/{len(links)}] skip: non-breaker -> {item.title}")
                        continue

                all_items.append(item)
                print(
                    f"    [{idx}/{len(links)}] ok: "
                    f"{item.vendor} | {item.title[:70]} | "
                    f"article={item.article or '-'} | price={item.price_rub}"
                )
            except Exception as e:
                print(f"    [{idx}/{len(links)}] error: {product_url} -> {e}")

    out_path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "source": "official_sites",
        "category_urls": category_urls,
        "items_count": len(all_items),
        "items": [asdict(x) for x in all_items],
    }
    out_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"saved: {out_path}")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--out", default=str(OUTPUT_DEFAULT))
    parser.add_argument("--max_links_per_category", type=int, default=120)
    args = parser.parse_args()

    build_price_catalog(
        category_urls=CATEGORY_URLS,
        out_path=Path(args.out),
        max_links_per_category=args.max_links_per_category,
    )


if __name__ == "__main__":
    main()