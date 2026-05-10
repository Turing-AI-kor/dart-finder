import re
from dataclasses import dataclass, field
from datetime import datetime
from urllib.parse import urlparse

DIGITAL_INDUSTRY_PREFIXES = {"58", "59", "60", "61", "62", "63", "70", "71", "72", "73", "82"}
CONSERVATIVE_INDUSTRY_PREFIXES = {"64", "65", "66", "84", "85", "86"}

KOREA_REGION_KEYWORDS = [
    "서울", "경기", "인천", "부산", "대구", "광주", "대전", "울산", "세종",
    "강원", "충북", "충남", "전북", "전남", "경북", "경남", "제주",
]

KOREAN_SURNAME_ROMANIZATION = {
    "김": "kim",
    "이": "lee",
    "박": "park",
    "최": "choi",
    "정": "jung",
    "강": "kang",
    "조": "cho",
    "윤": "yoon",
    "장": "jang",
    "임": "lim",
    "한": "han",
    "오": "oh",
    "서": "seo",
    "신": "shin",
    "권": "kwon",
    "황": "hwang",
    "안": "ahn",
    "송": "song",
    "전": "jeon",
    "홍": "hong",
    "유": "yoo",
    "고": "ko",
    "문": "moon",
    "양": "yang",
    "손": "son",
    "배": "bae",
    "백": "baek",
    "허": "huh",
    "남": "nam",
    "심": "shim",
    "노": "no",
    "하": "ha",
    "구": "koo",
    "성": "sung",
    "차": "cha",
    "주": "joo",
    "우": "woo",
    "민": "min",
}


@dataclass
class CompanyData:
    corp_name: str
    corp_code: str
    stock_code: str = ""
    ceo_name: str = ""
    industry: str = ""
    address: str = ""
    homepage: str = ""
    revenue: int | None = None
    revenue_prev: int | None = None
    growth_rate: float | None = None
    operating_profit: int | None = None
    net_income: int | None = None
    employees: int | None = None
    executives: list = field(default_factory=list)
    establish_date: str = ""
    recruit_count: int = 0


def passes_filter(
    company: CompanyData,
    *,
    revenue_min: int,
    revenue_max: int,
    employees_min: int,
    employees_max: int,
) -> bool:
    if company.revenue is None:
        return False

    if not (revenue_min <= company.revenue <= revenue_max):
        return False

    if not company.employees:
        return False

    if not (employees_min <= company.employees <= employees_max):
        return False

    if not company.address:
        return False

    if not any(keyword in company.address for keyword in KOREA_REGION_KEYWORDS):
        return False

    return True


def score_company(company: CompanyData) -> tuple[int, dict]:
    breakdown = {
        "pen_age": score_company_age(company.establish_date),
        "pen_industry": score_industry(company.industry),
        "pen_decision": score_decision_structure(company),
        "pen_recruit": min(company.recruit_count, 5) if company.recruit_count else 0,
        "pen_penalty": score_penalty(company.industry),
        "money_margin": score_margin(company),
        "money_growth": score_growth(company.growth_rate),
        "money_per_capita": score_revenue_per_employee(company),
        "money_scale": score_revenue_scale(company.revenue),
    }

    total = sum(breakdown.values())
    return int(total), breakdown


def score_company_age(establish_date: str) -> int:
    if not establish_date:
        return 7

    digits = re.sub(r"\D", "", establish_date)
    if len(digits) < 4:
        return 7

    try:
        year = int(digits[:4])
        age = datetime.now().year - year
    except ValueError:
        return 7

    if 3 <= age <= 10:
        return 15
    if 10 < age <= 15:
        return 12
    if age < 3:
        return 10
    if 15 < age <= 25:
        return 7
    return 3


def score_industry(industry_code: str) -> int:
    industry_code = industry_code or ""
    if any(industry_code.startswith(prefix) for prefix in DIGITAL_INDUSTRY_PREFIXES):
        return 12
    return 6


def score_penalty(industry_code: str) -> int:
    industry_code = industry_code or ""
    if any(industry_code.startswith(prefix) for prefix in CONSERVATIVE_INDUSTRY_PREFIXES):
        return -8
    return 0


def score_decision_structure(company: CompanyData) -> int:
    executive_count = len(company.executives or [])
    employees = company.employees or 0
    score = 0

    if 3 <= executive_count <= 7:
        score += 5
    elif executive_count < 3:
        score += 4
    elif executive_count <= 12:
        score += 2

    if 50 <= employees <= 200:
        score += 5
    elif 200 < employees <= 400:
        score += 3
    elif employees < 50:
        score += 2
    elif employees <= 600:
        score += 1

    return score


def score_margin(company: CompanyData) -> int:
    if not company.revenue or not company.operating_profit or company.revenue <= 0:
        return 0

    margin = company.operating_profit / company.revenue

    if margin >= 0.20:
        return 18
    if margin >= 0.15:
        return 15
    if margin >= 0.10:
        return 12
    if margin >= 0.05:
        return 8
    if margin >= 0.02:
        return 4
    if margin >= 0:
        return 1
    return 0


def score_growth(growth_rate: float | None) -> int:
    if growth_rate is None:
        return 4
    if growth_rate >= 0.40:
        return 12
    if growth_rate >= 0.25:
        return 10
    if growth_rate >= 0.15:
        return 8
    if growth_rate >= 0.05:
        return 5
    if growth_rate >= -0.05:
        return 3
    return 0


def score_revenue_per_employee(company: CompanyData) -> int:
    if not company.employees or not company.revenue or company.employees <= 0:
        return 0

    per_capita_eok = company.revenue / company.employees / 100_000_000

    if per_capita_eok >= 15:
        return 10
    if per_capita_eok >= 10:
        return 8
    if per_capita_eok >= 5:
        return 6
    if per_capita_eok >= 3:
        return 4
    if per_capita_eok >= 2:
        return 2
    return 1


def score_revenue_scale(revenue: int | None) -> int:
    if not revenue:
        return 0

    revenue_eok = revenue / 100_000_000

    if 200 <= revenue_eok <= 1000:
        return 10
    if 1000 < revenue_eok <= 3000:
        return 8
    if 100 <= revenue_eok < 200:
        return 7
    if 3000 < revenue_eok <= 5000:
        return 5
    if 50 <= revenue_eok < 100:
        return 4
    if revenue_eok > 5000:
        return 2
    return 1


def assign_tier(rank: int | float) -> str:
    try:
        rank_int = int(rank or 0)
    except Exception:
        rank_int = 999999

    if rank_int <= 100:
        return "S"
    if rank_int <= 1000:
        return "A"
    if rank_int <= 5000:
        return "B"
    return "C"


def recommend_channel(tier: str) -> str:
    return {
        "S": "LinkedIn 직접",
        "A": "LinkedIn + 이메일",
        "B": "이메일 자동화",
        "C": "예비",
    }.get(tier, "예비")


def recommend_persona(company: CompanyData) -> str:
    keywords = [
        "경영지원",
        "운영",
        "총무",
        "경영기획",
        "기획실",
        "인사",
        "HR",
        "CIO",
        "정보",
        "IT",
        "디지털",
    ]

    for executive in company.executives or []:
        name = executive.get("nm", "")
        role = executive.get("chrg_job", "") or executive.get("ofcps", "")
        combined = f"{role}"

        for keyword in keywords:
            if keyword in combined:
                return f"{name} ({role})".strip()

    if company.ceo_name:
        return f"{company.ceo_name} (대표이사)"

    if company.executives:
        executive = company.executives[0]
        name = executive.get("nm", "")
        role = executive.get("ofcps", "") or executive.get("chrg_job", "")
        return f"{name} ({role})".strip()

    return ""


def get_domain(homepage: str) -> str | None:
    if not homepage:
        return None

    url = homepage.strip()
    if not url.startswith(("http://", "https://")):
        url = "http://" + url

    try:
        netloc = urlparse(url).netloc.lower()
        if netloc.startswith("www."):
            netloc = netloc[4:]
        if "." in netloc:
            return netloc
    except Exception:
        return None

    return None


def guess_emails(company: CompanyData) -> list[str]:
    domain = get_domain(company.homepage)
    if not domain:
        return []

    patterns = {
        f"info@{domain}",
        f"contact@{domain}",
        f"sales@{domain}",
    }

    names = [company.ceo_name] + [executive.get("nm", "") for executive in (company.executives or [])[:3]]

    for name in names:
        if not name:
            continue

        name = name.strip()
        if not (2 <= len(name) <= 4):
            continue

        if not all("\uac00" <= ch <= "\ud7a3" for ch in name):
            continue

        romanized_surname = KOREAN_SURNAME_ROMANIZATION.get(name[0])
        if romanized_surname:
            patterns.add(f"{romanized_surname}@{domain}")

    return sorted(patterns)[:3]


def self_test_scoring() -> dict:
    sample = CompanyData(
        corp_name="테스트회사",
        corp_code="00000000",
        industry="620000",
        address="서울특별시 강남구",
        homepage="https://example.com",
        revenue=30_000_000_000,
        revenue_prev=25_000_000_000,
        growth_rate=0.2,
        operating_profit=3_000_000_000,
        employees=120,
        executives=[{"nm": "김철수", "ofcps": "대표이사", "chrg_job": "경영"}],
        establish_date="20180101",
    )
    total, breakdown = score_company(sample)
    return {
        "filter": passes_filter(
            sample,
            revenue_min=10_000_000_000,
            revenue_max=500_000_000_000,
            employees_min=30,
            employees_max=800,
        ),
        "score": total,
        "breakdown": breakdown,
        "tier_1": assign_tier(1),
        "tier_500": assign_tier(500),
        "tier_5000": assign_tier(5000),
        "domain": get_domain(sample.homepage),
        "emails": guess_emails(sample),
        "persona": recommend_persona(sample),
    }
