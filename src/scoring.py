import re
from dataclasses import dataclass, field
from datetime import datetime
from urllib.parse import urlparse

DIGITAL_FRIENDLY_KSIC_PREFIX = {
    "58","59","60","61","62","63","70","71","72","73","82",
}

CONSERVATIVE_KSIC_PREFIX = {
    "64","65","66","84","85","86",
}

KOREA_KEYWORDS = ["서울","경기","인천","부산","대구","광주","대전","울산",
                  "세종","강원","충북","충남","전북","전남","경북","경남","제주"]

KOREAN_SURNAME_ROMANIZATION = {
    "김":"kim","이":"lee","박":"park","최":"choi","정":"jung",
    "강":"kang","조":"cho","윤":"yoon","장":"jang","임":"lim",
    "한":"han","오":"oh","서":"seo","신":"shin","권":"kwon",
    "황":"hwang","안":"ahn","송":"song","전":"jeon","홍":"hong",
    "유":"yoo","고":"ko","문":"moon","양":"yang","손":"son",
    "배":"bae","백":"baek","허":"huh","남":"nam","심":"shim",
    "노":"no","하":"ha","구":"koo","성":"sung","차":"cha",
    "주":"joo","우":"woo","민":"min",
}


@dataclass
class CompanyData:
    corp_name:          str
    corp_code:          str
    stock_code:         str = ""
    ceo_name:           str = ""
    industry:           str = ""
    address:            str = ""
    homepage:           str = ""
    revenue:            int | None = None
    revenue_prev:       int | None = None
    growth_rate:        float | None = None
    operating_profit:   int | None = None
    net_income:         int | None = None
    employees:          int | None = None
    executives:         list[dict] = field(default_factory=list)
    establish_date:     str = ""
    recruit_count:      int = 0


def passes_filter(c, *, revenue_min, revenue_max, employees_min, employees_max) -> bool:
    if c.revenue is None or not (revenue_min <= c.revenue <= revenue_max):
        return False
    if c.employees is None or not (employees_min <= c.employees <= employees_max):
        return False
    if not c.address or not any(kw in c.address for kw in KOREA_KEYWORDS):
        return False
    return True


def score_company(c: CompanyData) -> tuple[int, dict]:
    breakdown = {}
    breakdown["pen_age"] = _score_age(c.establish_date)
    breakdown["pen_industry"] = _score_industry(c.industry)
    breakdown["pen_decision"] = _score_decision_speed(c)
    breakdown["pen_recruit"] = _score_recruit(c.recruit_count)
    breakdown["pen_conservative_penalty"] = _score_conservative_penalty(c.industry)
    breakdown["money_margin"] = _score_margin(c)
    breakdown["money_growth"] = _score_growth(c.growth_rate)
    breakdown["money_per_capita"] = _score_per_capita(c)
    breakdown["money_scale"] = _score_revenue_scale(c.revenue)
    total = sum(breakdown.values())
    return total, breakdown


def _score_age(establish_date: str) -> int:
    if not establish_date:
        return 7
    digits = re.sub(r"\D", "", establish_date)
    if len(digits) < 4:
        return 7
    try:
        year = int(digits[:4])
    except ValueError:
        return 7
    age = datetime.now().year - year
    if 3 <= age <= 10:    return 15
    if 10 < age <= 15:    return 12
    if age < 3:           return 10
    if 15 < age <= 25:    return 7
    return 3
