import re
from dataclasses import dataclass, field
from datetime import datetime
from urllib.parse import urlparse

DIGITAL = {"58", "59", "60", "61", "62", "63", "70", "71", "72", "73", "82"}
CONSERVATIVE = {"64", "65", "66", "84", "85", "86"}

KOREA = [
    "서울", "경기", "인천", "부산", "대구", "광주", "대전", "울산", "세종",
    "강원", "충북", "충남", "전북", "전남", "경북", "경남", "제주",
]

SURNAMES = {
    "김": "kim", "이": "lee", "박": "park", "최": "choi", "정": "jung",
    "강": "kang", "조": "cho", "윤": "yoon", "장": "jang", "임": "lim",
    "한": "han", "오": "oh", "서": "seo", "신": "shin", "권": "kwon",
    "황": "hwang", "안": "ahn", "송": "song", "전": "jeon", "홍": "hong",
    "유": "yoo", "고": "ko", "문": "moon", "양": "yang", "손": "son",
    "배": "bae", "백": "baek", "허": "huh", "남": "nam", "심": "shim",
    "노": "no", "하": "ha", "구": "koo", "성": "sung", "차": "cha",
    "주": "joo", "우": "woo", "민": "min",
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
    revenue: int = None
    revenue_prev: int = None
    growth_rate: float = None
    operating_profit: int = None
    net_income: int = None
    employees: int = None
    executives: list = field(default_factory=list)
    establish_date: str = ""
    recruit_count: int = 0


def passes_filter(c, *, revenue_min, revenue_max, employees_min, employees_max) -> bool:
    if c.revenue is None or not (revenue_min <= c.revenue <= revenue_max):
        return False
    if not c.employees or not (employees_min <= c.employees <= employees_max):
        return False
    if not c.address or not any(k in c.address for k in KOREA):
        return False
    return True


def score_company(c) -> tuple:
    breakdown = {}
    breakdown["pen_age"] = _age(c.establish_date)
    breakdown["pen_industry"] = 12 if any(c.industry.startswith(p) for p in DIGITAL) else 6
    breakdown["pen_decision"] = _decision(c)
    breakdown["pen_recruit"] = min(c.recruit_count, 5) if c.recruit_count else 0
    breakdown["pen_penalty"] = -8 if any(c.industry.startswith(p) for p in CONSERVATIVE) else 0
    breakdown["money_margin"] = _margin(c)
    breakdown["money_growth"] = _growth(c.growth_rate)
    breakdown["money_per_capita"] = _per_capita(c)
    breakdown["money_scale"] = _scale(c.revenue)
    return sum(breakdown.values()), breakdown


def _age(establish_date):
    if not establish_date:
        return 7

    digits = re.sub(r"\D", "", establish_date)
    if len(digits) < 4:
        return 7

    try:
        age = datetime.now().year - int(digits[:4])
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


def _decision(c):
    executive_count = len(c.executives)
    employees = c.employees or 0
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


def _margin(c):
    if not c.revenue or not c.operating_profit or c.revenue <= 0:
        return 0

    margin = c.operating_profit / c.revenue

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


def _growth(rate):
    if rate is None:
        return 4
    if rate >= 0.40:
        return 12
    if rate >= 0.25:
        return 10
    if rate >= 0.15:
        return 8
    if rate >= 0.05:
        return 5
    if rate >= -0.05:
        return 3
    return 0


def _per_capita(c):
    if not c.employees or not c.revenue or c.employees <= 0:
        return 0

    per_capita = c.revenue / c.employees / 1e8

    if per_capita >= 15:
        return 10
    if per_capita >= 10:
        return 8
    if per_capita >= 5:
        return 6
    if per_capita >= 3:
        return 4
    if per_capita >= 2:
        return 2
    return 1


def _scale(revenue):
    if not revenue:
        return 0

    eok = revenue / 1e8

    if 200 <= eok <= 1000:
        return 10
    if 1000 < eok <= 3000:
        return 8
    if 100 <= eok < 200:
        return 7
    if 3000 < eok <= 5000:
        return 5
    if 50 <= eok < 100:
        return 4
    if eok > 5000:
        return 2
    return 1


def assign_tier(value: float | int) -> str:
    """
    순위 기준 tier.
    main.py에서 rank를 넣는다.

    S: 1~100
    A: 101~1000
    B: 1001~5000
    C: 그 외
    """
    try:
        rank = int(value or 0)
    except Exception:
        rank = 999999

    if rank <= 100:
        return "S"
    if rank <= 1000:
        return "A"
    if rank <= 5000:
        return "B"
    return "C"


def recommend_channel(tier: str) -> str:
    return {
        "S": "LinkedIn 직접",
        "A": "LinkedIn + 이메일",
        "B": "이메일 자동화",
        "C": "예비",
    }.get(tier, "")


def recommend_persona(c) -> str:
    keywords = [
        "경영지원", "운영", "총무", "경영기획", "기획실", "인사", "HR",
        "CIO", "정보", "IT", "디지털",
    ]

    for executive in c.executives:
        combined = f"{executive.get('chrg_job', '') or ''} {executive.get('ofcps', '') or ''}"
        for keyword in keywords:
            if keyword in combined:
                return f"{executive.get('nm', '')} ({executive.get('chrg_job', '') or executive.get('ofcps', '')})"

    if c.ceo_name:
        return f"{c.ceo_name} (대표이사)"

    if c.executives:
        executive = c.executives[0]
        return f"{executive.get('nm', '')} ({executive.get('ofcps', '') or executive.get('chrg_job', '')})"

    return ""


def get_domain(homepage: str):
    if not homepage:
        return None

    url = homepage.strip()
    if not url.startswith(("http://", "https://")):
        url = "http://" + url

    try:
        netloc = urlparse(url).netloc.lower()
        if netloc.startswith("www."):
            netloc = netloc[4:]
        return netloc if netloc and "." in netloc else None
    except Exception:
        return None


def guess_emails(c) -> list:
    domain = get_domain(c.homepage)
    if not domain:
        return []

    patterns = {f"info@{domain}", f"contact@{domain}", f"sales@{domain}"}
    names = [c.ceo_name] + [e.get("nm", "") for e in c.executives[:3]]

    for name in names:
        if not name or not (2 <= len(name) <= 4):
            continue
        if not all("\uac00" <= ch <= "\ud7a3" for ch in name):
            continue

        surname = SURNAMES.get(name[0])
        if surname:
            patterns.add(f"{surname}@{domain}")

    return sorted(patterns)[:3]
