import asyncio
import csv
import json
import logging
from datetime import datetime, timedelta, timezone

import httpx
import typer
from rich.console import Console
from rich.progress import BarColumn, MofNCompleteColumn, Progress, SpinnerColumn, TextColumn

from .config import settings
from .dart_client import DartClient
from .scoring import (
    CompanyData,
    assign_tier,
    get_domain,
    guess_emails,
    passes_filter,
    recommend_channel,
    recommend_persona,
    score_company,
    self_test_scoring,
)

app = typer.Typer()
console = Console()
logging.basicConfig(level=logging.WARNING)

KST = timezone(timedelta(hours=9))
FIXED_SAMPLE_SIZE = 5000
FIXED_TOP = 5000


async def get_json(client: httpx.AsyncClient, path: str, params: dict) -> dict | None:
    try:
        response = await client.get(
            path,
            params={"crtfc_key": settings.OPENDART_API_KEY, **params},
            timeout=30.0,
        )
        response.raise_for_status()
        return response.json()
    except Exception:
        return None


async def get_financial(client: httpx.AsyncClient, corp_code: str, year: int) -> dict:
    for fs_div in ["CFS", "OFS"]:
        data = await get_json(
            client,
            "/fnlttSinglAcntAll.json",
            {
                "corp_code": corp_code,
                "bsns_year": str(year),
                "reprt_code": "11011",
                "fs_div": fs_div,
            },
        )
        if data and data.get("status") == "000":
            return DartClient.parse_financial(data.get("list", []))
    return {}


async def fetch_company(
    client: httpx.AsyncClient,
    corp_code: str,
    base: dict,
    year: int,
    semaphore: asyncio.Semaphore,
) -> CompanyData | None:
    async with semaphore:
        try:
            company_info, financial, employees, executives = await asyncio.gather(
                get_json(client, "/company.json", {"corp_code": corp_code}),
                get_financial(client, corp_code, year),
                get_json(
                    client,
                    "/empSttus.json",
                    {
                        "corp_code": corp_code,
                        "bsns_year": str(year),
                        "reprt_code": "11011",
                    },
                ),
                get_json(
                    client,
                    "/exctvSttus.json",
                    {
                        "corp_code": corp_code,
                        "bsns_year": str(year),
                        "reprt_code": "11011",
                    },
                ),
                return_exceptions=True,
            )
        except Exception:
            return None

    if isinstance(company_info, Exception):
        return None

    if not company_info or company_info.get("status") != "000":
        return None

    financial = financial if isinstance(financial, dict) else {}

    employee_list = []
    if isinstance(employees, dict) and employees.get("status") == "000":
        employee_list = employees.get("list", []) or []

    executive_list = []
    if isinstance(executives, dict) and executives.get("status") == "000":
        executive_list = executives.get("list", []) or []

    total_employees = 0
    for row in employee_list:
        raw_value = (row.get("sumtrmcnt") or "0").replace(",", "").strip()
        if raw_value.isdigit():
            total_employees += int(raw_value)

    return CompanyData(
        corp_name=base.get("corp_name", ""),
        corp_code=corp_code,
        stock_code=base.get("stock_code", ""),
        ceo_name=company_info.get("ceo_nm", "") or "",
        industry=(company_info.get("induty_code") or "").strip(),
        address=company_info.get("adres", "") or "",
        homepage=company_info.get("hm_url", "") or "",
        revenue=financial.get("revenue"),
        revenue_prev=financial.get("revenue_prev"),
        growth_rate=financial.get("growth_rate"),
        operating_profit=financial.get("operating_profit"),
        net_income=financial.get("net_income"),
        employees=total_employees,
        executives=executive_list,
        establish_date=company_info.get("est_dt", "") or "",
    )


def company_to_dict(company: CompanyData) -> dict:
    return {
        "corp_name": company.corp_name,
        "corp_code": company.corp_code,
        "stock_code": company.stock_code,
        "ceo_name": company.ceo_name,
        "industry": company.industry,
        "address": company.address,
        "homepage": company.homepage,
        "revenue": company.revenue,
        "revenue_prev": company.revenue_prev,
        "growth_rate": company.growth_rate,
        "operating_profit": company.operating_profit,
        "net_income": company.net_income,
        "employees": company.employees,
        "executives": company.executives,
        "establish_date": company.establish_date,
        "recruit_count": company.recruit_count,
    }


def save_checkpoint(path, results: list[CompanyData]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = [company_to_dict(company) for company in results]
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")


def load_checkpoint(path) -> list[CompanyData]:
    if not path.exists():
        return []

    try:
        items = json.loads(path.read_text(encoding="utf-8"))
        return [CompanyData(**item) for item in items]
    except Exception:
        return []


async def collect_companies(
    sample: list[tuple[str, dict]],
    year: int,
    concurrency: int,
    checkpoint_path,
) -> list[CompanyData]:
    semaphore = asyncio.Semaphore(concurrency)
    results = load_checkpoint(checkpoint_path)
    done_codes = {company.corp_code for company in results}

    if results:
        console.print(f"[cyan]체크포인트 로드: {len(results):,}개[/cyan]")

    remaining = [(corp_code, base) for corp_code, base in sample if corp_code not in done_codes]

    if not remaining:
        return results

    console.print(f"[cyan]남은 수집 대상: {len(remaining):,}개[/cyan]")

    checkpoint_counter = 0

    async with httpx.AsyncClient(base_url=settings.DART_BASE_URL, timeout=30.0) as client:
        with Progress(
            SpinnerColumn(),
            TextColumn("{task.description}"),
            BarColumn(),
            MofNCompleteColumn(),
        ) as progress:
            task = progress.add_task("OpenDART 수집 중", total=len(remaining))

            for index in range(0, len(remaining), 100):
                batch = remaining[index : index + 100]

                gathered = await asyncio.gather(
                    *[
                        fetch_company(client, corp_code, base, year, semaphore)
                        for corp_code, base in batch
                    ],
                    return_exceptions=True,
                )

                for item in gathered:
                    if isinstance(item, CompanyData):
                        results.append(item)
                        checkpoint_counter += 1

                progress.update(task, advance=len(batch))

                if checkpoint_counter >= 200:
                    save_checkpoint(checkpoint_path, results)
                    checkpoint_counter = 0

    save_checkpoint(checkpoint_path, results)
    return results


def write_csv(output_path, scored: list[tuple[int, dict, CompanyData]]) -> None:
    output_path.parent.mkdir(parents=True, exist_ok=True)

    headers = [
        "순위",
        "Tier",
        "영업채널",
        "회사명",
        "종목코드",
        "점수",
        "점수_잘뚫림",
        "점수_매출",
        "매출(억)",
        "매출성장률(%)",
        "영업이익(억)",
        "영업이익률(%)",
        "1인당매출(억)",
        "직원수",
        "임원수",
        "설립일",
        "추천컨택",
        "대표자",
        "도메인",
        "이메일후보",
        "주소",
        "홈페이지",
        "업종코드",
        "임원명단",
    ]

    with output_path.open("w", encoding="utf-8-sig", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(headers)

        for rank, (total, breakdown, company) in enumerate(scored, 1):
            tier = assign_tier(rank)

            penetration_score = sum(
                breakdown.get(key, 0)
                for key in ["pen_age", "pen_industry", "pen_decision", "pen_recruit", "pen_penalty"]
            )
            money_score = sum(
                breakdown.get(key, 0)
                for key in ["money_margin", "money_growth", "money_per_capita", "money_scale"]
            )

            margin = ""
            if company.revenue and company.operating_profit and company.revenue > 0:
                margin = f"{company.operating_profit / company.revenue * 100:.1f}"

            growth = ""
            if company.growth_rate is not None:
                growth = f"{company.growth_rate * 100:.1f}"

            per_capita = ""
            if company.revenue and company.employees:
                per_capita = f"{company.revenue / company.employees / 100_000_000:.1f}"

            emails = " | ".join(guess_emails(company))

            executive_text = " | ".join(
                f"{executive.get('nm', '')}({executive.get('ofcps', '') or executive.get('chrg_job', '')})"
                for executive in (company.executives or [])[:8]
            )

            writer.writerow(
                [
                    rank,
                    tier,
                    recommend_channel(tier),
                    company.corp_name,
                    company.stock_code,
                    total,
                    penetration_score,
                    money_score,
                    f"{(company.revenue or 0) / 100_000_000:.0f}",
                    growth,
                    f"{(company.operating_profit or 0) / 100_000_000:.0f}",
                    margin,
                    per_capita,
                    company.employees or "",
                    len(company.executives or []),
                    company.establish_date,
                    recommend_persona(company),
                    company.ceo_name,
                    get_domain(company.homepage) or "",
                    emails,
                    company.address,
                    company.homepage,
                    company.industry,
                    executive_text,
                ]
            )


@app.command()
def find(
    industry: str = typer.Option("all"),
    revenue_min: int = typer.Option(10_000_000_000),
    revenue_max: int = typer.Option(500_000_000_000),
    employees_min: int = typer.Option(30),
    employees_max: int = typer.Option(800),
    top: int = typer.Option(FIXED_TOP),
    sample_size: int = typer.Option(FIXED_SAMPLE_SIZE),
    include_unlisted: bool = typer.Option(True),
    concurrency: int = typer.Option(10),
    year: int = typer.Option(datetime.now(KST).year - 1),
    min_score: int = typer.Option(25),
):
    settings.validate()
    settings.OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    settings.CHECKPOINT_DIR.mkdir(parents=True, exist_ok=True)

    fixed_sample_size = FIXED_SAMPLE_SIZE
    fixed_top = FIXED_TOP

    console.print("[bold cyan]DART Target Finder fixed 5000[/bold cyan]")
    console.print(f"  처리 대상: 무조건 최대 {fixed_sample_size:,}개")
    console.print(f"  저장 결과: 무조건 최대 {fixed_top:,}개")
    console.print(f"  매출: {revenue_min / 100_000_000:.0f}억 ~ {revenue_max / 100_000_000:.0f}억")
    console.print(f"  직원: {employees_min}명 ~ {employees_max}명")
    console.print(f"  비상장 포함: {include_unlisted}")
    console.print("")

    with DartClient() as dart:
        console.print("[1/3] 회사 코드 다운로드...")
        all_corps = dart.fetch_all_corp_codes()

    if include_unlisted:
        corps = all_corps
    else:
        corps = {key: value for key, value in all_corps.items() if value.get("stock_code")}

    console.print(f"  전체 후보: {len(corps):,}개")

    sample = sorted(
        corps.items(),
        key=lambda item: item[1].get("modify_date", ""),
        reverse=True,
    )
    sample = sample[:fixed_sample_size]

    checkpoint_path = settings.CHECKPOINT_DIR / (
        f"fixed5000_{industry}_{revenue_min}_{revenue_max}_{employees_min}_{employees_max}_{year}.json"
    )

    console.print(f"[2/3] OpenDART 상세 수집: {len(sample):,}개")
    all_data = asyncio.run(
        collect_companies(
            sample=sample,
            year=year,
            concurrency=concurrency,
            checkpoint_path=checkpoint_path,
        )
    )
    console.print(f"  수집 성공: {len(all_data):,}개")

    prefixes = None
    if industry != "all":
        prefixes = settings.INDUSTRY_KSIC_PREFIX.get(industry)

    console.print("[3/3] 필터링 + 점수 계산 + CSV 생성")

    candidates = [
        company
        for company in all_data
        if (prefixes is None or any(company.industry.startswith(prefix) for prefix in prefixes))
        and passes_filter(
            company,
            revenue_min=revenue_min,
            revenue_max=revenue_max,
            employees_min=employees_min,
            employees_max=employees_max,
        )
    ]

    console.print(f"  필터 통과: {len(candidates):,}개")

    scored = []
    for company in candidates:
        total, breakdown = score_company(company)
        if total >= min_score:
            scored.append((total, breakdown, company))

    scored.sort(key=lambda item: item[0], reverse=True)
    scored = scored[:fixed_top]

    timestamp = datetime.now(KST).strftime("%Y-%m-%d_%H%M")
    output_path = settings.OUTPUT_DIR / f"targets_fixed5000_{industry}_{timestamp}.csv"

    write_csv(output_path, scored)

    if not scored:
        console.print(f"[yellow]조건에 맞는 결과가 없습니다. 빈 CSV 생성: {output_path}[/yellow]")
        return

    console.print(f"\n[bold green]완료! 결과 {len(scored):,}개[/bold green]")
    console.print(f"CSV: {output_path}\n")

    console.print("[bold]상위 10개:[/bold]")
    for rank, (total, breakdown, company) in enumerate(scored[:10], 1):
        penetration_score = sum(
            breakdown.get(key, 0)
            for key in ["pen_age", "pen_industry", "pen_decision", "pen_recruit", "pen_penalty"]
        )
        money_score = sum(
            breakdown.get(key, 0)
            for key in ["money_margin", "money_growth", "money_per_capita", "money_scale"]
        )
        console.print(
            f"  {rank:>2}. [cyan]{company.corp_name:<20}[/cyan] "
            f"총[yellow]{total}[/yellow] "
            f"뚫[green]{penetration_score}[/green] "
            f"매[magenta]{money_score}[/magenta] "
            f"{(company.revenue or 0) / 100_000_000:.0f}억 "
            f"{company.employees or 0}명"
        )


@app.command()
def check_key():
    settings.validate()

    with DartClient() as dart:
        corps = dart.fetch_all_corp_codes()

    listed = sum(1 for value in corps.values() if value.get("stock_code"))
    unlisted = len(corps) - listed

    console.print(
        f"[green]✓ OpenDART API 키 정상[/green] "
        f"전체 {len(corps):,}개 / 상장 {listed:,}개 / 비상장 {unlisted:,}개"
    )


@app.command()
def self_test():
    result = self_test_scoring()
    console.print("[green]SELF_TEST_OK[/green]")
    console.print(result)


if __name__ == "__main__":
    app()
