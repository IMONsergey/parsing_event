from __future__ import annotations

import argparse
import hashlib
import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urljoin, urlparse

import pandas as pd
import requests
import tldextract
from bs4 import BeautifulSoup
from email_validator import EmailNotValidError, validate_email


SOURCES_COLUMNS = [
    "Активен",
    "Сегмент",
    "Тип источника",
    "Источник",
    "Компания",
    "Сайт",
    "URL источника",
    "Страна",
    "Город",
    "Отрасль",
    "Приоритет",
    "Комментарий",
]

MANUAL_COLUMNS = [
    "Сегмент",
    "Компания",
    "Имя",
    "Должность",
    "Email",
    "Телефон",
    "Сайт",
    "LinkedIn",
    "Telegram",
    "Страна",
    "Город",
    "Отрасль",
    "Источник",
    "URL источника",
    "Приоритет",
    "Комментарий",
]

EXCLUDE_COLUMNS = ["Домен", "Причина"]
KEYWORDS_COLUMNS = ["Сегмент", "Ключевые слова", "Минус-слова"]

OUTPUT_COLUMNS = [
    "ID",
    "Статус",
    "Приоритет",
    "Сегмент",
    "Тип компании",
    "Компания",
    "Имя",
    "Должность",
    "Email",
    "Тип email",
    "Телефон",
    "Сайт",
    "LinkedIn",
    "Telegram",
    "Страна",
    "Город",
    "Отрасль",
    "Источник",
    "URL источника",
    "Потенциальный интерес",
    "Дата добавления",
    "Дата обновления",
    "Уверенность",
    "Комментарий",
]

TARGET_SEGMENTS = {
    "event",
    "communications",
    "pr",
    "marketing",
    "branding",
    "digital",
    "production",
    "corporate",
    "consulting",
    "education",
    "sales",
    "hr",
    "community",
    "other",
}

GENERAL_EMAIL_PREFIXES = {
    "info",
    "contact",
    "sales",
    "support",
    "office",
    "hello",
    "team",
    "agency",
    "pr",
    "event",
}

SEGMENT_TYPE = {
    "event": "event agency",
    "communications": "communications agency",
    "pr": "PR agency",
    "marketing": "marketing agency",
    "branding": "branding agency",
    "digital": "digital agency",
    "production": "production agency",
    "corporate": "corporate",
    "consulting": "consulting",
    "education": "education",
    "sales": "sales enablement",
    "hr": "employer brand / HR",
    "community": "business community",
    "other": "other",
}

EMAIL_RE = re.compile(r"(?<![\w.+-])[\w.+-]+@[\w.-]+\.[A-Za-zА-Яа-я]{2,}(?![\w.-])")
PHONE_RE = re.compile(r"(?:\+?\d[\d\s().-]{7,}\d)")


def read_csv(path: Path, columns: list[str]) -> pd.DataFrame:
    if not path.exists():
        print(f"Файл не найден: {path}. Используется пустая таблица.")
        return pd.DataFrame(columns=columns)
    try:
        df = pd.read_csv(path, dtype=str, keep_default_na=False)
    except pd.errors.EmptyDataError:
        return pd.DataFrame(columns=columns)
    for column in columns:
        if column not in df.columns:
            df[column] = ""
    return df[columns]


def clean_text(value: Any) -> str:
    if value is None:
        return ""
    return re.sub(r"\s+", " ", str(value)).strip()


def normalize_url(value: str) -> str:
    value = clean_text(value)
    if not value:
        return ""
    if value.startswith(("http://", "https://")):
        return value
    return f"https://{value}"


def normalize_email(value: str) -> tuple[str, bool]:
    email = clean_text(value).lower().replace("mailto:", "")
    email = email.split("?")[0].strip(" .,;:<>\"'")
    if not email:
        return "", False
    try:
        valid = validate_email(email, check_deliverability=False)
    except EmailNotValidError:
        return email, False
    return valid.normalized.lower(), True


def registered_domain(value: str) -> str:
    ext = tldextract.extract(value)
    if not ext.domain or not ext.suffix:
        return value.lower()
    return f"{ext.domain}.{ext.suffix}".lower()


def classify_email(email: str, is_valid: bool, excluded_domains: set[str]) -> str:
    if not email or not is_valid:
        return "Неизвестно"
    local, _, domain = email.partition("@")
    domain = registered_domain(domain)
    if domain in excluded_domains:
        return "Личный"
    if local in GENERAL_EMAIL_PREFIXES:
        return "Общий"
    return "Корпоративный"


def normalize_phone(value: str) -> str:
    value = clean_text(value)
    if not value:
        return ""
    value = re.sub(r"[^\d+(). -]", "", value)
    value = re.sub(r"\s+", " ", value).strip()
    return value


def split_csv_cell(value: str) -> list[str]:
    return [item.strip().lower() for item in clean_text(value).split(",") if item.strip()]


def fetch_page(url: str) -> tuple[str, str]:
    response = requests.get(
        url,
        headers={
            "User-Agent": "BAEV-leads-pipeline/1.0 (+https://github.com/IMONsergey/parsing_event)"
        },
        timeout=15,
    )
    response.raise_for_status()
    return response.text, response.url


def extract_page_data(html: str, base_url: str) -> dict[str, Any]:
    soup = BeautifulSoup(html, "lxml")
    title = clean_text(soup.title.string if soup.title else "")
    meta_description = ""
    meta = soup.find("meta", attrs={"name": re.compile("^description$", re.I)})
    if meta and meta.get("content"):
        meta_description = clean_text(meta.get("content"))

    text = soup.get_text(" ", strip=True)
    hrefs = [clean_text(a.get("href")) for a in soup.find_all("a", href=True)]
    mailto_emails = [href for href in hrefs if href.lower().startswith("mailto:")]
    raw_emails = EMAIL_RE.findall(" ".join([text, " ".join(hrefs), meta_description]))
    phones = [normalize_phone(phone) for phone in PHONE_RE.findall(text)]

    linkedin = []
    telegram = []
    for href in hrefs:
        absolute = urljoin(base_url, href)
        lower = absolute.lower()
        if "linkedin.com/" in lower:
            linkedin.append(absolute)
        if "t.me/" in lower or "telegram.me/" in lower:
            telegram.append(absolute)

    return {
        "title": title,
        "meta_description": meta_description,
        "text": " ".join([title, meta_description, text]),
        "emails": list(dict.fromkeys(raw_emails + mailto_emails)),
        "phones": list(dict.fromkeys([phone for phone in phones if phone])),
        "linkedin": list(dict.fromkeys(linkedin)),
        "telegram": list(dict.fromkeys(telegram)),
    }


def potential_interest(segment: str) -> str:
    if segment == "event":
        return (
            "Может быть партнёром или клиентом BAEV: агентству регулярно нужны "
            "презентации для тендеров, питчей, клиентов и мероприятий."
        )
    if segment in {"communications", "pr"}:
        return (
            "Релевантно для BAEV: коммуникационным командам часто нужны презентации, "
            "питчи, визуальная упаковка смыслов и материалы для клиентов."
        )
    if segment in {"corporate", "sales", "hr"}:
        return (
            "Потенциальный клиент BAEV: компании могут требоваться презентации для "
            "продаж, руководства, HR, инвесторов и публичных выступлений."
        )
    if segment == "consulting":
        return (
            "Потенциальный клиент BAEV: консалтинговым командам нужны структурированные "
            "презентации, коммерческие материалы и визуальная упаковка выводов."
        )
    return (
        "Потенциально релевантно для BAEV: команде могут быть нужны презентации, "
        "коммерческие материалы и визуальная коммуникация для бизнеса."
    )


def priority_for(segment: str, value: str) -> str:
    value = clean_text(value).upper()
    if value in {"A", "B", "C"}:
        return value
    if segment in {"event", "communications", "pr", "corporate", "consulting"}:
        return "A"
    if segment in {"marketing", "branding", "digital", "production", "education"}:
        return "B"
    return "C"


def has_keywords(segment: str, text: str, keywords: dict[str, dict[str, list[str]]]) -> bool:
    segment_keywords = keywords.get(segment, {}).get("keywords", [])
    lowered = text.lower()
    return any(keyword and keyword in lowered for keyword in segment_keywords)


def confidence_score(row: dict[str, Any], email_valid: bool, relevant_keywords: bool, active_source: bool) -> int:
    score = 0
    email_type = row.get("Тип email", "")
    if email_valid:
        score += 35
    if email_type == "Корпоративный":
        score += 25
    if email_type == "Общий":
        score += 10
    if email_type == "Личный":
        score -= 20
    if row.get("Компания"):
        score += 10
    if row.get("Сайт"):
        score += 10
    if row.get("Телефон"):
        score += 5
    if row.get("LinkedIn"):
        score += 5
    if row.get("Telegram"):
        score += 5
    if row.get("Сегмент") in TARGET_SEGMENTS:
        score += 10
    if relevant_keywords:
        score += 10
    if active_source:
        score += 5
    return max(0, min(100, score))


def status_for(row: dict[str, Any]) -> str:
    confidence = int(row.get("Уверенность") or 0)
    email_type = row.get("Тип email", "")
    if email_type == "Корпоративный" and confidence >= 70:
        return "Готово"
    if email_type == "Общий" and confidence >= 60:
        return "Проверить"
    if email_type == "Личный":
        return "Проверить"
    if not row.get("Email") and (row.get("Телефон") or row.get("Сайт")):
        return "Проверить"
    filled = sum(1 for key in ["Компания", "Email", "Телефон", "Сайт"] if row.get(key))
    if filled <= 1:
        return "Исключить"
    return "Новый"


def stable_id(row: dict[str, Any]) -> str:
    email = clean_text(row.get("Email", "")).lower()
    company = clean_text(row.get("Компания", "")).lower()
    source_url = clean_text(row.get("URL источника", "")).lower()
    website = clean_text(row.get("Сайт", "")).lower()
    raw = f"{email}|{company}|{source_url}" if email else f"{company}|{website}|{source_url}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]


def source_to_rows(
    source: pd.Series,
    excluded_domains: set[str],
    keywords: dict[str, dict[str, list[str]]],
    report: dict[str, Any],
) -> list[dict[str, Any]]:
    source_url = normalize_url(source.get("URL источника", ""))
    if not source_url:
        return []

    try:
        html, final_url = fetch_page(source_url)
        report["pages_fetched"] += 1
    except Exception as exc:
        report["errors"].append({"url": source_url, "error": str(exc)})
        return []

    data = extract_page_data(html, final_url)
    segment = clean_text(source.get("Сегмент", "")).lower() or "other"
    if segment not in TARGET_SEGMENTS:
        segment = "other"
    company = clean_text(source.get("Компания", "")) or data["title"]
    website = normalize_url(source.get("Сайт", "")) or f"{urlparse(final_url).scheme}://{urlparse(final_url).netloc}"
    relevant_keywords = has_keywords(segment, data["text"], keywords)
    base = {
        "Приоритет": priority_for(segment, source.get("Приоритет", "")),
        "Сегмент": segment,
        "Тип компании": SEGMENT_TYPE.get(segment, "other"),
        "Компания": company,
        "Имя": "",
        "Должность": "",
        "Телефон": "; ".join(data["phones"][:3]),
        "Сайт": website,
        "LinkedIn": "; ".join(data["linkedin"][:3]),
        "Telegram": "; ".join(data["telegram"][:3]),
        "Страна": clean_text(source.get("Страна", "")),
        "Город": clean_text(source.get("Город", "")),
        "Отрасль": clean_text(source.get("Отрасль", "")),
        "Источник": clean_text(source.get("Источник", "")),
        "URL источника": final_url,
        "Потенциальный интерес": potential_interest(segment),
        "Комментарий": clean_text(source.get("Комментарий", "")),
    }

    rows = []
    emails = data["emails"] or [""]
    for raw_email in emails:
        email, email_valid = normalize_email(raw_email)
        email_type = classify_email(email, email_valid, excluded_domains)
        row = dict(base)
        row["Email"] = email
        row["Тип email"] = email_type
        if raw_email and not email_valid:
            row["Комментарий"] = join_unique([row["Комментарий"], "Email требует ручной проверки"])
        row["Уверенность"] = confidence_score(row, email_valid, relevant_keywords, active_source=True)
        row["Статус"] = status_for(row)
        row["ID"] = stable_id(row)
        rows.append(row)

    return rows


def manual_to_rows(
    manual: pd.DataFrame,
    excluded_domains: set[str],
    keywords: dict[str, dict[str, list[str]]],
) -> list[dict[str, Any]]:
    rows = []
    for _, item in manual.iterrows():
        if not any(clean_text(item.get(column, "")) for column in MANUAL_COLUMNS):
            continue
        segment = clean_text(item.get("Сегмент", "")).lower() or "other"
        if segment not in TARGET_SEGMENTS:
            segment = "other"
        email, email_valid = normalize_email(item.get("Email", ""))
        email_type = classify_email(email, email_valid, excluded_domains)
        source_text = " ".join(clean_text(item.get(column, "")) for column in MANUAL_COLUMNS)
        row = {
            "Приоритет": priority_for(segment, item.get("Приоритет", "")),
            "Сегмент": segment,
            "Тип компании": SEGMENT_TYPE.get(segment, "other"),
            "Компания": clean_text(item.get("Компания", "")),
            "Имя": clean_text(item.get("Имя", "")),
            "Должность": clean_text(item.get("Должность", "")),
            "Email": email,
            "Тип email": email_type,
            "Телефон": normalize_phone(item.get("Телефон", "")),
            "Сайт": normalize_url(item.get("Сайт", "")),
            "LinkedIn": clean_text(item.get("LinkedIn", "")),
            "Telegram": clean_text(item.get("Telegram", "")),
            "Страна": clean_text(item.get("Страна", "")),
            "Город": clean_text(item.get("Город", "")),
            "Отрасль": clean_text(item.get("Отрасль", "")),
            "Источник": clean_text(item.get("Источник", "")) or "Ручное добавление",
            "URL источника": normalize_url(item.get("URL источника", "")),
            "Потенциальный интерес": potential_interest(segment),
            "Комментарий": clean_text(item.get("Комментарий", "")),
        }
        relevant_keywords = has_keywords(segment, source_text, keywords)
        row["Уверенность"] = confidence_score(row, email_valid, relevant_keywords, active_source=True)
        row["Статус"] = status_for(row)
        row["ID"] = stable_id(row)
        rows.append(row)
    return rows


def join_unique(values: list[str]) -> str:
    result = []
    for value in values:
        for part in clean_text(value).split(";"):
            part = clean_text(part)
            if part and part not in result:
                result.append(part)
    return "; ".join(result)


def completeness(row: pd.Series) -> int:
    return sum(1 for column in OUTPUT_COLUMNS if clean_text(row.get(column, "")))


def dedupe(df: pd.DataFrame) -> pd.DataFrame:
    if df.empty:
        return pd.DataFrame(columns=OUTPUT_COLUMNS)

    groups: dict[str, list[int]] = {}
    for idx, row in df.iterrows():
        email = clean_text(row.get("Email", "")).lower()
        if email:
            key = f"email:{email}"
        else:
            key = "fallback:" + "|".join(
                [
                    clean_text(row.get("Компания", "")).lower(),
                    clean_text(row.get("Сайт", "")).lower(),
                    clean_text(row.get("URL источника", "")).lower(),
                ]
            )
        groups.setdefault(key, []).append(idx)

    merged_rows = []
    for indexes in groups.values():
        group = df.loc[indexes].copy()
        best = group.loc[group.apply(completeness, axis=1).idxmax()].to_dict()
        for column in ["Источник", "URL источника", "Комментарий"]:
            best[column] = join_unique([clean_text(value) for value in group[column].tolist()])
        for column in OUTPUT_COLUMNS:
            if not clean_text(best.get(column, "")):
                for value in group[column].tolist():
                    if clean_text(value):
                        best[column] = clean_text(value)
                        break
        merged_rows.append(best)

    result = pd.DataFrame(merged_rows)
    return result.reindex(columns=OUTPUT_COLUMNS)


def load_existing_dates(output_path: Path) -> dict[str, str]:
    if not output_path.exists():
        return {}
    try:
        existing = pd.read_csv(output_path, dtype=str, keep_default_na=False)
    except Exception:
        return {}
    if "ID" not in existing.columns or "Дата добавления" not in existing.columns:
        return {}
    return dict(zip(existing["ID"], existing["Дата добавления"]))


def load_keywords(path: Path) -> dict[str, dict[str, list[str]]]:
    df = read_csv(path, KEYWORDS_COLUMNS)
    result: dict[str, dict[str, list[str]]] = {}
    for _, row in df.iterrows():
        segment = clean_text(row.get("Сегмент", "")).lower()
        if not segment:
            continue
        result[segment] = {
            "keywords": split_csv_cell(row.get("Ключевые слова", "")),
            "minus": split_csv_cell(row.get("Минус-слова", "")),
        }
    return result


def ensure_parent(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)


def run(args: argparse.Namespace) -> None:
    started_at = datetime.now(timezone.utc)
    today = started_at.date().isoformat()
    report: dict[str, Any] = {
        "started_at": started_at.isoformat(),
        "finished_at": "",
        "sources_total": 0,
        "sources_active": 0,
        "pages_fetched": 0,
        "contacts_auto_found": 0,
        "contacts_manual_loaded": 0,
        "contacts_total_before_dedupe": 0,
        "contacts_total_after_dedupe": 0,
        "errors": [],
    }

    sources = read_csv(Path(args.sources), SOURCES_COLUMNS)
    manual = read_csv(Path(args.manual), MANUAL_COLUMNS)
    exclude_domains = read_csv(Path(args.exclude_domains), EXCLUDE_COLUMNS)
    keywords = load_keywords(Path(args.keywords))
    excluded = {registered_domain(domain) for domain in exclude_domains["Домен"].tolist() if clean_text(domain)}

    report["sources_total"] = int(len(sources))
    active_sources = sources[sources["Активен"].str.strip().str.lower().eq("да")]
    report["sources_active"] = int(len(active_sources))

    rows: list[dict[str, Any]] = []
    for _, source in active_sources.iterrows():
        rows.extend(source_to_rows(source, excluded, keywords, report))

    report["contacts_auto_found"] = int(len(rows))
    manual_rows = manual_to_rows(manual, excluded, keywords)
    report["contacts_manual_loaded"] = int(len(manual_rows))
    rows.extend(manual_rows)
    report["contacts_total_before_dedupe"] = int(len(rows))

    df = pd.DataFrame(rows)
    if df.empty:
        df = pd.DataFrame(columns=OUTPUT_COLUMNS)
    for column in OUTPUT_COLUMNS:
        if column not in df.columns:
            df[column] = ""
    df = df[OUTPUT_COLUMNS]

    df = dedupe(df)
    old_dates = load_existing_dates(Path(args.output))
    if not df.empty:
        df["ID"] = df.apply(lambda row: row["ID"] or stable_id(row.to_dict()), axis=1)
        df["Дата добавления"] = df["ID"].map(old_dates).fillna(today)
        df["Дата обновления"] = today
        df["Уверенность"] = pd.to_numeric(df["Уверенность"], errors="coerce").fillna(0).astype(int)
        df = df.sort_values(["Приоритет", "Сегмент", "Компания", "Email"], kind="stable")
    else:
        df = pd.DataFrame(columns=OUTPUT_COLUMNS)

    report["contacts_total_after_dedupe"] = int(len(df))
    report["finished_at"] = datetime.now(timezone.utc).isoformat()

    output_path = Path(args.output)
    json_output_path = Path(args.json_output)
    report_path = Path(args.report)
    ensure_parent(output_path)
    ensure_parent(json_output_path)
    ensure_parent(report_path)

    df.to_csv(output_path, index=False, encoding="utf-8")
    records = df.to_dict(orient="records")
    json_output_path.write_text(
        json.dumps(records, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    report_path.write_text(
        json.dumps(report, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    print(f"Готово: контактов после дедупликации — {len(df)}")
    print(f"CSV: {output_path}")
    print(f"JSON: {json_output_path}")
    print(f"Отчёт: {report_path}")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Сбор и нормализация контактов для BAEV.")
    parser.add_argument("--sources", default="config/sources.csv")
    parser.add_argument("--manual", default="config/manual_contacts.csv")
    parser.add_argument("--exclude-domains", default="config/exclude_domains.csv")
    parser.add_argument("--keywords", default="config/keywords.csv")
    parser.add_argument("--output", default="data/contacts_ru.csv")
    parser.add_argument("--json-output", default="data/contacts_ru.json")
    parser.add_argument("--report", default="data/run_report.json")
    return parser.parse_args()


if __name__ == "__main__":
    run(parse_args())
