from __future__ import annotations

import argparse
import hashlib
import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import unquote, urljoin, urlparse

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
    "ID компании",
    "Ключ компании",
    "Повтор компании",
    "Кол-во контактов компании",
    "№ контакта в компании",
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
    "brief",
    "request",
    "newbusiness",
    "business",
    "partners",
    "partnership",
    "marketing",
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
YEAR_RANGE_RE = re.compile(r"^(?:19|20)\d{2}\s*[-–]\s*(?:19|20)\d{2}$")
DATE_RE = re.compile(r"^\d{1,2}[./-]\d{1,2}[./-](?:19|20)\d{2}$")
INN_RE = re.compile(r"^(?:\d{10}|\d{12})$")
FORBIDDEN_SOURCE_CONTEXT_RE = re.compile(
    r"aviation|aircraft|mro|jeddah|kaizen|maintenance|landing\s+gear|"
    r"aircraft\s+maintenance|aviation\s+hub",
    re.I,
)
PLACEHOLDER_DOMAINS = {
    "example.com",
    "example.org",
    "example.net",
    "test.com",
    "localhost",
    "domain.com",
    "yourdomain.com",
    "email.com",
}
DISALLOWED_EMAIL_DOMAINS = {
    "madeontilda.com",
    "masterhost.ru",
}
PLACEHOLDER_LOCAL_PARTS = {
    "example",
    "test",
    "email",
    "mail",
    "name",
    "yourname",
    "username",
}
TECHNICAL_EMAIL_PREFIXES = {
    "noreply",
    "no-reply",
    "donotreply",
    "do-not-reply",
    "privacy",
    "abuse",
    "postmaster",
    "webmaster",
}
ASSET_EXTENSIONS = (
    ".png",
    ".jpg",
    ".jpeg",
    ".svg",
    ".webp",
    ".gif",
    ".css",
    ".js",
    ".woff",
    ".woff2",
    ".ttf",
    ".eot",
)
EMAIL_FORBIDDEN_CHARS_RE = re.compile(r"[\s\"'()<>,;/\\?#]")
LEGAL_FORMS_RE = re.compile(
    r"\b(ооо|ао|пао|ип|llc|ltd|ltd\.|agency|group|studio|bureau|"
    r"агентство|студия|группа|бюро)\b",
    re.I,
)


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
    email = unquote(email).split("?")[0]
    email = re.sub(r"\s+", "", email).strip(" .,;:<>\"'")
    if not email:
        return "", False
    try:
        valid = validate_email(email, check_deliverability=False)
    except EmailNotValidError:
        return "", False
    return valid.normalized.lower(), True


def split_email_candidates(value: str) -> list[str]:
    value = clean_text(value).replace("mailto:", "")
    value = unquote(value)
    value = value.split("?")[0]
    return [item for item in re.split(r"[,;\s]+", value) if item]


def registered_domain(value: str) -> str:
    ext = tldextract.extract(value)
    if not ext.domain or not ext.suffix:
        return value.lower()
    return f"{ext.domain}.{ext.suffix}".lower()


def normalize_company_name(value: str) -> str:
    value = clean_text(value).lower()
    value = re.sub(r"[\"'«»“”„`]", "", value)
    value = LEGAL_FORMS_RE.sub(" ", value)
    value = re.sub(r"[^0-9a-zа-яё&+ -]+", " ", value, flags=re.I)
    return re.sub(r"\s+", " ", value).strip()


def company_key_for(row: dict[str, Any] | pd.Series) -> str:
    website = clean_text(row.get("Сайт", ""))
    if website:
        domain = registered_domain(urlparse(normalize_url(website)).netloc or website)
        if domain:
            return domain

    company = normalize_company_name(row.get("Компания", ""))
    if company:
        country = clean_text(row.get("Страна", "")).lower()
        city = clean_text(row.get("Город", "")).lower()
        return f"{company}|{country}|{city}"

    return f"unknown|{clean_text(row.get('ID', ''))}"


def is_allowed_email(email: str) -> bool:
    email = clean_text(email).lower()
    if not email or email.count("@") != 1:
        return False
    if "://" in email or email.startswith("www."):
        return False
    if EMAIL_FORBIDDEN_CHARS_RE.search(email):
        return False
    if any(ext in email for ext in ASSET_EXTENSIONS):
        return False

    local, _, domain = email.partition("@")
    if not local or not domain or len(local) > 64:
        return False
    if len(local) < 2 or local in PLACEHOLDER_LOCAL_PARTS:
        return False
    if local.split("+", 1)[0] in TECHNICAL_EMAIL_PREFIXES:
        return False

    domain = domain.rstrip(".")
    ext = tldextract.extract(domain)
    registered = registered_domain(domain)
    if not ext.domain or not ext.suffix or not registered:
        return False
    if registered in PLACEHOLDER_DOMAINS or domain in PLACEHOLDER_DOMAINS:
        return False
    if registered in DISALLOWED_EMAIL_DOMAINS or domain in DISALLOWED_EMAIL_DOMAINS:
        return False
    if any(token in registered for token in ("example", "localhost", "draft")):
        return False

    try:
        validate_email(email, check_deliverability=False)
    except EmailNotValidError:
        return False
    return True


def normalize_allowed_emails(raw_emails: list[str]) -> tuple[list[str], int]:
    emails = []
    discarded = 0
    for raw_email in raw_emails:
        email, is_valid = normalize_email(raw_email)
        if is_valid and is_allowed_email(email):
            if email not in emails:
                emails.append(email)
        elif clean_text(raw_email):
            discarded += 1
    return emails, discarded


def filter_emails_for_company_site(
    raw_emails: list[str], website: str, *, require_site_domain: bool = False
) -> list[str]:
    site_domain = registered_domain(urlparse(normalize_url(website)).netloc)
    if not site_domain:
        return raw_emails

    preferred = []
    for raw_email in raw_emails:
        email, is_valid = normalize_email(raw_email)
        if not is_valid or "@" not in email:
            continue
        email_domain = registered_domain(email.partition("@")[2])
        if email_domain == site_domain:
            preferred.append(raw_email)

    if preferred:
        return list(dict.fromkeys(preferred))
    if require_site_domain:
        return []
    return raw_emails


def filter_catalog_owner_emails(raw_emails: list[str], source_url: str, source_name: str) -> list[str]:
    if "russianbranding.ru" not in source_url and "АБКР" not in source_name:
        return raw_emails
    return [
        email
        for email in raw_emails
        if "russianbranding" not in email.lower() and "abcr@" not in email.lower()
    ]


def classify_email(email: str, is_valid: bool, excluded_domains: set[str]) -> str:
    if not email:
        return "Неизвестно"
    if not is_valid or not is_allowed_email(email):
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
    if "%" in value or YEAR_RANGE_RE.match(value) or DATE_RE.match(value):
        return ""
    value = re.sub(r"[^\d+(). -]", "", value)
    value = re.sub(r"\s+", " ", value).strip()
    digits = re.sub(r"\D", "", value)
    if INN_RE.match(digits):
        return ""
    if len(digits) < 10 or len(digits) > 15:
        return ""
    compact = value.replace(" ", "")
    if YEAR_RANGE_RE.match(compact) or DATE_RE.match(compact):
        return ""
    is_ru_phone = compact.startswith(("+7", "7", "8")) and len(digits) == 11
    is_regional_phone = (
        compact.startswith(("+375", "375", "+380", "380", "+971", "971"))
        and len(digits) == 12
    )
    is_other_international = compact.startswith("+") and 10 <= len(digits) <= 15
    if not (is_ru_phone or is_regional_phone or is_other_international):
        return ""
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

    for tag in soup(["script", "style", "noscript", "svg", "canvas"]):
        tag.decompose()

    text = soup.get_text(" ", strip=True)
    hrefs = [clean_text(a.get("href")) for a in soup.find_all("a", href=True)]
    mailto_emails = []
    for href in hrefs:
        if href.lower().startswith("mailto:"):
            mailto_emails.extend(split_email_candidates(href))
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
    if email_type == "Общий":
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
    raw_emails = filter_catalog_owner_emails(data["emails"], final_url, base["Источник"])
    require_site_domain_email = "index.bbt.news/catalog/" in final_url.lower()
    raw_emails = filter_emails_for_company_site(
        raw_emails, website, require_site_domain=require_site_domain_email
    )
    emails, discarded_email_count = normalize_allowed_emails(raw_emails)
    if discarded_email_count:
        report["email_candidates_discarded"] += discarded_email_count
        base["Комментарий"] = join_unique(
            [base["Комментарий"], "Некорректные email-кандидаты отброшены автоматически."]
        )
    for email in emails or [""]:
        email_valid = bool(email)
        email_type = classify_email(email, email_valid, excluded_domains)
        row = dict(base)
        row["Email"] = email
        row["Тип email"] = email_type
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
        manual_comment = clean_text(item.get("Комментарий", ""))
        if not email_valid or not is_allowed_email(email):
            if clean_text(item.get("Email", "")):
                manual_comment = join_unique(
                    [manual_comment, "Email из ручного ввода не прошёл проверку и был очищен."]
                )
            email = ""
            email_valid = False
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
            "Комментарий": manual_comment,
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


def mark_company_groups(df: pd.DataFrame) -> pd.DataFrame:
    if df.empty:
        return pd.DataFrame(columns=OUTPUT_COLUMNS)

    result = df.copy()
    keys = result.apply(lambda row: company_key_for(row), axis=1)
    result["Ключ компании"] = keys
    result["ID компании"] = keys.apply(
        lambda key: hashlib.sha256(clean_text(key).encode("utf-8")).hexdigest()[:12]
    )
    counts = keys.map(keys.value_counts())
    result["Кол-во контактов компании"] = counts.astype(int).astype(str)
    result["Повтор компании"] = counts.apply(lambda count: "Да" if int(count) > 1 else "Нет")
    result["№ контакта в компании"] = result.groupby(keys, sort=False).cumcount().add(1).astype(str)
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


def build_email_quality_report(df: pd.DataFrame) -> dict[str, Any]:
    checked_at = datetime.now(timezone.utc).isoformat()
    emails = [clean_text(email).lower() for email in df.get("Email", pd.Series(dtype=str)).tolist()]
    filled_emails = [email for email in emails if email]
    suspicious_examples = []
    invalid_count = 0
    disallowed_count = 0
    placeholder_count = 0
    noreply_count = 0

    for idx, email in enumerate(emails):
        if not email:
            continue

        normalized, is_valid = normalize_email(email)
        local = normalized.partition("@")[0] if normalized else email.partition("@")[0]
        domain = normalized.partition("@")[2] if normalized else email.partition("@")[2]
        registered = registered_domain(domain) if domain else ""
        is_placeholder = (
            registered in PLACEHOLDER_DOMAINS
            or domain in PLACEHOLDER_DOMAINS
            or local in PLACEHOLDER_LOCAL_PARTS
            or any(token in registered for token in ("example", "localhost", "draft"))
        )
        is_noreply = local.split("+", 1)[0] in TECHNICAL_EMAIL_PREFIXES
        is_disallowed = not is_allowed_email(normalized)

        if not is_valid:
            invalid_count += 1
        if is_disallowed:
            disallowed_count += 1
        if is_placeholder:
            placeholder_count += 1
        if is_noreply:
            noreply_count += 1
        if (not is_valid or is_disallowed or is_placeholder or is_noreply) and len(suspicious_examples) < 20:
            row = df.iloc[idx]
            suspicious_examples.append(
                {
                    "row": int(idx + 2),
                    "Компания": clean_text(row.get("Компания", "")),
                    "Email": email,
                    "URL источника": clean_text(row.get("URL источника", "")),
                }
            )

    duplicate_count = pd.Series(filled_emails).duplicated().sum() if filled_emails else 0
    return {
        "checked_at": checked_at,
        "total_rows": int(len(df)),
        "rows_with_email": int(len(filled_emails)),
        "rows_without_email": int(len(df) - len(filled_emails)),
        "invalid_email_count": int(invalid_count),
        "disallowed_email_count": int(disallowed_count),
        "placeholder_email_count": int(placeholder_count),
        "noreply_email_count": int(noreply_count),
        "duplicate_email_count": int(duplicate_count),
        "suspicious_examples": suspicious_examples,
    }


def value_counts_dict(series: pd.Series) -> dict[str, int]:
    cleaned = series.fillna("").map(clean_text)
    return {str(key): int(value) for key, value in cleaned.value_counts().items() if key}


def best_status(statuses: list[str]) -> str:
    order = {"Готово": 4, "Проверить": 3, "Новый": 2, "Исключить": 1}
    return max((clean_text(status) for status in statuses), key=lambda status: order.get(status, 0), default="")


def build_company_quality_report(df: pd.DataFrame) -> dict[str, Any]:
    checked_at = datetime.now(timezone.utc).isoformat()
    if df.empty:
        return {
            "checked_at": checked_at,
            "total_contact_rows": 0,
            "unique_companies": 0,
            "companies_with_email": 0,
            "companies_without_email": 0,
            "companies_with_website": 0,
            "companies_without_website": 0,
            "companies_with_multiple_contacts": 0,
            "max_contacts_per_company": 0,
            "average_contacts_per_company": 0,
            "companies_by_segment": {},
            "companies_by_priority": {},
            "companies_by_country": {},
            "companies_by_status_best": {},
            "duplicate_company_candidates": [],
            "top_domains_with_multiple_companies": [],
            "top_companies_by_contacts": [],
        }

    grouped = df.groupby("Ключ компании", dropna=False, sort=False)
    company_rows = []
    for key, group in grouped:
        emails = [email for email in group["Email"].map(clean_text).tolist() if email]
        company_rows.append(
            {
                "company_key": clean_text(key),
                "Компания": clean_text(group["Компания"].iloc[0]),
                "Сайт": clean_text(group["Сайт"].iloc[0]),
                "contacts_count": int(len(group)),
                "emails": sorted(set(emails)),
                "segment": clean_text(group["Сегмент"].iloc[0]),
                "priority": clean_text(group["Приоритет"].iloc[0]),
                "country": clean_text(group["Страна"].iloc[0]),
                "status_best": best_status(group["Статус"].tolist()),
            }
        )

    companies = pd.DataFrame(company_rows)
    contact_counts = companies["contacts_count"] if not companies.empty else pd.Series(dtype=int)
    duplicate_candidates = companies[
        companies["company_key"].str.startswith("unknown|", na=False) | companies["Компания"].eq("")
    ].head(30)
    domain_counts = (
        companies[companies["Сайт"].ne("")]
        .assign(domain=lambda frame: frame["Сайт"].map(lambda site: registered_domain(urlparse(normalize_url(site)).netloc or site)))
        .groupby("domain", dropna=False)
        .size()
        .reset_index(name="companies_count")
    )
    top_domains = domain_counts[domain_counts["companies_count"] > 1].sort_values(
        "companies_count", ascending=False
    ).head(30)

    return {
        "checked_at": checked_at,
        "total_contact_rows": int(len(df)),
        "unique_companies": int(len(companies)),
        "companies_with_email": int(companies["emails"].map(bool).sum()),
        "companies_without_email": int((~companies["emails"].map(bool)).sum()),
        "companies_with_website": int(companies["Сайт"].map(bool).sum()),
        "companies_without_website": int((~companies["Сайт"].map(bool)).sum()),
        "companies_with_multiple_contacts": int((contact_counts > 1).sum()),
        "max_contacts_per_company": int(contact_counts.max()) if not contact_counts.empty else 0,
        "average_contacts_per_company": round(float(contact_counts.mean()), 2) if not contact_counts.empty else 0,
        "companies_by_segment": value_counts_dict(companies["segment"]),
        "companies_by_priority": value_counts_dict(companies["priority"]),
        "companies_by_country": value_counts_dict(companies["country"]),
        "companies_by_status_best": value_counts_dict(companies["status_best"]),
        "duplicate_company_candidates": duplicate_candidates[
            ["company_key", "Компания", "Сайт", "contacts_count", "segment", "priority"]
        ].to_dict(orient="records"),
        "top_domains_with_multiple_companies": top_domains.to_dict(orient="records"),
        "top_companies_by_contacts": companies.sort_values("contacts_count", ascending=False)
        .head(30)[["company_key", "Компания", "Сайт", "contacts_count", "emails", "segment", "priority"]]
        .to_dict(orient="records"),
    }


def build_source_quality_report(sources: pd.DataFrame) -> dict[str, Any]:
    checked_at = datetime.now(timezone.utc).isoformat()
    sources = sources.copy()
    active = sources["Активен"].str.strip().str.lower().eq("да") if "Активен" in sources else pd.Series(dtype=bool)
    source_urls = sources["URL источника"].map(lambda value: normalize_url(value).lower())
    websites = sources["Сайт"].map(lambda value: normalize_url(value).lower())
    domains = websites.map(lambda value: registered_domain(urlparse(value).netloc or value) if value else "")

    duplicate_source_urls = [
        {"url": url, "count": int(count)}
        for url, count in source_urls[source_urls.ne("")].value_counts().items()
        if count > 1
    ]
    duplicate_domains = [
        {"domain": domain, "count": int(count)}
        for domain, count in domains[domains.ne("")].value_counts().items()
        if count > 1
    ]

    suspicious_sources = []
    for idx, row in sources.iterrows():
        text = " ".join(clean_text(row.get(column, "")) for column in SOURCES_COLUMNS)
        reasons = []
        if FORBIDDEN_SOURCE_CONTEXT_RE.search(text):
            reasons.append("forbidden_aviation_context")
        if clean_text(row.get("Сегмент", "")).lower() not in TARGET_SEGMENTS:
            reasons.append("unknown_segment")
        if not clean_text(row.get("Компания", "")):
            reasons.append("empty_company")
        if not clean_text(row.get("URL источника", "")):
            reasons.append("empty_source_url")
        if reasons and len(suspicious_sources) < 50:
            suspicious_sources.append(
                {
                    "row": int(idx + 2),
                    "Компания": clean_text(row.get("Компания", "")),
                    "Сайт": clean_text(row.get("Сайт", "")),
                    "URL источника": clean_text(row.get("URL источника", "")),
                    "reasons": reasons,
                }
            )

    return {
        "checked_at": checked_at,
        "sources_total": int(len(sources)),
        "sources_active": int(active.sum()) if len(active) else 0,
        "sources_inactive": int(len(sources) - active.sum()) if len(active) else int(len(sources)),
        "sources_by_segment": value_counts_dict(sources["Сегмент"].str.lower()),
        "sources_by_priority": value_counts_dict(sources["Приоритет"].str.upper()),
        "sources_by_country": value_counts_dict(sources["Страна"]),
        "sources_by_source_type": value_counts_dict(sources["Тип источника"]),
        "duplicate_source_urls": duplicate_source_urls[:50],
        "duplicate_domains": duplicate_domains[:50],
        "empty_company_count": int(sources["Компания"].map(clean_text).eq("").sum()),
        "empty_website_count": int(sources["Сайт"].map(clean_text).eq("").sum()),
        "empty_source_url_count": int(sources["URL источника"].map(clean_text).eq("").sum()),
        "suspicious_sources": suspicious_sources,
    }


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
        "email_candidates_discarded": 0,
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
        df = mark_company_groups(df)
    else:
        df = pd.DataFrame(columns=OUTPUT_COLUMNS)

    report["contacts_total_after_dedupe"] = int(len(df))
    report["finished_at"] = datetime.now(timezone.utc).isoformat()

    output_path = Path(args.output)
    json_output_path = Path(args.json_output)
    report_path = Path(args.report)
    email_report_path = Path(args.email_report)
    company_report_path = Path(args.company_report)
    source_report_path = Path(args.source_report)
    ensure_parent(output_path)
    ensure_parent(json_output_path)
    ensure_parent(report_path)
    ensure_parent(email_report_path)
    ensure_parent(company_report_path)
    ensure_parent(source_report_path)

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
    email_quality_report = build_email_quality_report(df)
    email_report_path.write_text(
        json.dumps(email_quality_report, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    company_quality_report = build_company_quality_report(df)
    company_report_path.write_text(
        json.dumps(company_quality_report, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    source_quality_report = build_source_quality_report(sources)
    source_report_path.write_text(
        json.dumps(source_quality_report, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    print(f"Готово: контактов после дедупликации — {len(df)}")
    print(f"CSV: {output_path}")
    print(f"JSON: {json_output_path}")
    print(f"Отчёт: {report_path}")
    print(f"Email QA: {email_report_path}")
    print(f"Company QA: {company_report_path}")
    print(f"Source QA: {source_report_path}")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Сбор и нормализация контактов для BAEV.")
    parser.add_argument("--sources", default="config/sources.csv")
    parser.add_argument("--manual", default="config/manual_contacts.csv")
    parser.add_argument("--exclude-domains", default="config/exclude_domains.csv")
    parser.add_argument("--keywords", default="config/keywords.csv")
    parser.add_argument("--output", default="data/contacts_ru.csv")
    parser.add_argument("--json-output", default="data/contacts_ru.json")
    parser.add_argument("--report", default="data/run_report.json")
    parser.add_argument("--email-report", default="data/email_quality_report.json")
    parser.add_argument("--company-report", default="data/company_quality_report.json")
    parser.add_argument("--source-report", default="data/source_quality_report.json")
    return parser.parse_args()


if __name__ == "__main__":
    run(parse_args())
