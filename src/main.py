import asyncio
import csv
import json
import logging
from datetime import datetime, timezone, timedelta
from pathlib import Path

import httpx
import typer
from rich.console import Console
from rich.progress import (BarColumn, MofNCompleteColumn, Progress,
                           SpinnerColumn, TextColumn, TimeElapsedColumn,
                           TimeRemainingColumn)

from .config import settings
from .dart_client import DartClient
from .scoring import (CompanyData, assign_tier, guess_email_patterns,
                      passes_filter, recommend_channel,
                      recommend_target_persona, score_company,
                      extract_company_domain)

app = typer.Typer()
console = Console()
logging.basicConfig(level=logging.WARNING)
KST = timezone(timedelta(hours=9))


async def _async_get(client, path, params):
    full = {"crtfc_key": settings.OPENDART_API_KEY, **params}
    try:
        r = await client.get(path, params=full, timeout=30.0)
        r.raise_for_status()
        return r.json()
    except Exception:
        return None


async def _async_get_financial(client, corp_code, year):
    for fs_div in ["CFS", "OFS"]:
        data = await _async_get(client, "/fnlttSinglAcntAll.json", {
            "corp_code": corp_code,
            "bsns_year": str(year),
            "reprt_code": "11011",
            "fs_div": fs_div,
        })
        if data and data.get("status") == "000":
            return DartClient._parse_financial(data.get("list", []))
    return {}


async def fetch_bundle(client, corp_code, base, year, sem):
    async with sem:
        try:
            info, fin, emp, exc = await asyncio.gather(
                _async_get(client, "/company.json", {"corp_code": corp_code}),
                _async_get_financial(client, corp_code, year),
                _async_get(client, "/empSttus.json", {
                    "corp_code": corp_code,
                    "bsns_year": str(year),
                    "reprt_code": "11011",
                }),
                _async_get(client, "/exctvSttus.json", {
                    "corp_code": corp_code,
                    "bsns_year": str(year),
                    "reprt_code": "11011",
                }),
                return_exceptions=True,
            )
        except Exception:
            return None

    if isinstance(info, Exception) or not info or info.get("status") != "000":
        return None

    fin = fin if isinstance(fin, dict) else {}
    emp_list = emp.get("list", []) if isinstance(emp, dict) and emp.get("status") == "000" else []
    exec_list = exc.get("list", []) if isinstance(exc, dict) and exc.get("status") == "000" else []

    total_emp = sum(
        int((e.get("sumtrmcnt") or "0").replace(",", "") or 0)
        for e in emp_list
        if (e.get("sumtrmcnt") or "0").replace(",", "").isdigit()
    )

    return CompanyData(
        corp_name=base["corp_name"],
        corp_code=corp_code,
        stock_code=base["stock_code"],
        ceo_name=info.get("ceo_nm", ""),
        industry=(info.get("induty_code") or "").strip(),
        address=info.get("adres", ""),
        homepage=info.get("hm_url", ""),
        revenue=fin.get("revenue"),
        revenue_prev=fin.get("revenue_prev"),
        growth_rate=fin.get("growth_rate"),
        operating_profit=fin.get("operating_profit"),
        net_income=fin.get("net_income"),
        employees=total_emp,
        executives=exec_list,
        establish_date=info.get("est_dt", ""),
    )


def _save_checkpoint(path, results):
    path.parent.mkdir(parents=True, exist_ok=True)
    data = []
    for c in results:
        data.append({
            "corp_name": c.corp_name,
            "corp_code": c.corp_code,
            "stock_code": c.stock_code,
            "ceo_name": c.ceo_name,
            "industry": c.industry,
            "address": c.address,
            "homepage": c.homepage,
            "revenue": c.revenue,
            "revenue_prev": c.revenue_prev,
            "growth_rate": c.growth_rate,
            "operating_profit": c.operating_profit,
            "net_income": c.net_income,
            "employees": c.employees,
            "executives": c.executives,
            "establish_date": c.establish_date,
            "recruit_count": c.recruit_count,
        })
    with path.open("w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False)


async def collect_all(sample, year, concurrency, checkpoint_path):
    sem = asyncio.Semaphore(concurrency)
    results = []
    done = set()

    if checkpoint_path.exists():
        try:
            cached = json.loads(checkpoint_path.read_text(encoding="utf-8"))
            for item in cached:
                results.append(CompanyData(**item))
                done.add(item["corp_code"])
            console.print(f"[cyan]체크포인트: {len(results)}개 로드[/cyan]")
        except Exception:
            pass

    remaining = [(cc, b) for cc, b in sample if cc not in done]
    if not remaining:
        return results

    console.print(f"[cyan]수집: {len(remaining)}개[/cyan]")
    save_cnt = 0

    async with httpx.AsyncClient(
        base_url=settings.DART_BASE_URL,
        timeout=30.0
    ) as client:
        with Progress(
            SpinnerColumn(),
            TextColumn("{task.description}"),
            BarColumn(),
            MofNCompleteColumn(),
            TimeElapsedColumn(),
            TimeRemainingColumn(),
        ) as prog:
            task = prog.add_task("수집 중", total=len(remaining))
            for i in range(0, len(remaining), 100):
                batch = remaining[i:i + 100]
                batch_results = await asyncio.gather(
                    *[fetch_bundle(client, cc, b, year, sem) for cc, b in batch],
                    return_exceptions=True,
                )
                for r in batch_results:
                    if isinstance(r, CompanyData):
                        results.append(r)
                        save_cnt += 1
                prog.update(task, advance=len(batch))
                if save_cnt >= 200:
                    _save_checkpoint(checkpoint_path, results)
                    save_cnt = 0

    _save_checkpoint(checkpoint_path, results)
    return results


@app.command()
def find(
    industry: str = typer.Option("all"),
    revenue_min: int = typer.Option(10_000_000_000),
    revenue_max: int = typer.Option(500_000_000_000),
    employees_min: int = typer.Option(30),
    employees_max: int = typer.Option(800),
    top: int = typer.Option(5000),
    sample_size: int = typer.Option(0),
    include_unlisted: bool = typer.Option(True),
    concurrency: int = typer.Option(30),
    year: int = typer.Option(datetime.now(KST).year - 1),
    min_score: int = typer.Option(25),
    resume: bool = typer.Option(False),
):
    settings.validate()
    settings.OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    settings.CHECKPOINT_DIR.mkdir(parents=True, exist_ok=True)

    target_prefixes = None
    if industry != "all":
        target_prefixes = settings.INDUSTRY_KSIC_PREFIX.get(industry)

    cp_name = f"{industry}_{revenue_min}_{revenue_max}_{employees_min}_{employees_max}_{year}.json"
    checkpoint_path = settings.CHECKPOINT_DIR / cp_name

    console.print("[bold cyan]DART Target Finder v3.5[/bold cyan]")
    console.print(f"  매출: {revenue_min/1e8:.0f}억~{revenue_max/1e8:.0f}억")
    console.print(f"  직원: {employees_min}~{employees_max}  목표: {top}개\n")

    with DartClient() as dart:
        console.print("[1/3] 회사 코드 다운로드...")
        all_corps = dart.fetch_all_corp_codes()

    if include_unlisted:
        corps = all_corps
    else:
        corps = {k: v for k, v in all_corps.items() if v["stock_code"]}

    console.print(f"  대상: {len(corps):,}개")

    sample = sorted(
        corps.items(),
        key=lambda x: x[1].get("modify_date", ""),
        reverse=True,
    )
    if sample_size > 0:
        sample = sample[:sample_size]

    console.print(f"[2/3] 정보 수집 (병렬 {concurrency})...")
    all_data = asyncio.run(collect_all(sample, year, concurrency, checkpoint_path))
    console.print(f"  수집: {len(all_data):,}개\n")

    console.print("[3/3] 필터 + 점수 + CSV...")
    candidates = []
    for c in all_data:
        if target_prefixes is not None:
            if not any(c.industry.startswith(p) for p in target_prefixes):
                continue
        if not passes_filter(
            c,
            revenue_min=revenue_min,
            revenue_max=revenue_max,
            employees_min=employees_min,
            employees_max=employees_max,
        ):
            continue
        candidates.append(c)

    console.print(f"  필터 통과: {len(candidates)}개")

    scored_raw = []
    for c in candidates:
        total, bd = score_company(c)
        if total >= min_score:
            scored_raw.append((total, bd, c))

    scored_raw.sort(key=lambda x: x[0], reverse=True)
    scored = scored_raw[:top]

    if not scored:
        console.print("[yellow]결과 없음. --min-score 낮춰보세요.[/yellow]")
        return

    ts = datetime.now(KST).strftime("%Y-%m-%d_%H%M")
    out = settings.OUTPUT_DIR / f"targets_{industry}_{ts}.csv"

    with out.open("w", encoding="utf-8-sig", newline="") as f:
        w = csv.writer(f)
        w.writerow([
            "순위", "Tier", "영업채널", "회사명", "종목코드", "점수",
            "점수_잘뚫림", "점수_매출", "매출(억)", "매출성장률(%)",
            "영업이익(억)", "영업이익률(%)", "1인당매출(억)",
            "직원수", "임원수", "설립일", "추천컨택", "대표자",
            "도메인", "이메일후보", "주소", "홈페이지", "업종코드", "임원명단",
        ])
        for rank, (total, bd, c) in enumerate(scored, 1):
            tier = assign_tier(rank, len(scored))
            pen = sum(bd.get(k, 0) for k in [
                "pen_age", "pen_industry", "pen_decision",
                "pen_recruit", "pen_conservative_penalty",
            ])
            money = sum(bd.get(k, 0) for k in [
                "money_margin", "money_growth",
                "money_per_capita", "money_scale",
            ])
            margin = ""
            if c.revenue and c.operating_profit and c.revenue > 0:
                margin = f"{c.operating_profit / c.revenue * 100:.1f}"
            growth = f"{c.growth_rate * 100:.1f}" if c.growth_rate else ""
            per_cap = ""
            if c.revenue and c.employees and c.employees > 0:
                per_cap = f"{c.revenue / c.employees / 1e8:.1f}"
            emails = " | ".join(guess_email_patterns(c)[:3])
            execs = " | ".join(
                f"{e.get('nm', '')}({e.get('ofcps', '') or e.get('chrg_job', '')})"
                for e in c.executives[:8]
            )
            w.writerow([
                rank, tier, recommend_channel(tier),
                c.corp_name, c.stock_code, total,
                pen, money,
                f"{(c.revenue or 0) / 1e8:.0f}",
                growth,
                f"{(c.operating_profit or 0) / 1e8:.0f}",
                margin, per_cap,
                c.employees, len(c.executives), c.establish_date,
                recommend_target_persona(c), c.ceo_name,
                extract_company_domain(c.homepage) or "",
                emails, c.address, c.homepage, c.industry, execs,
            ])

    console.print(f"\n[bold green]완료! {len(scored)}개[/bold green] → {out}\n")
    console.print("[bold]상위 10:[/bold]")
    for rank, (total, bd, c) in enumerate(scored[:10], 1):
        pen = sum(bd.get(k, 0) for k in [
            "pen_age", "pen_industry", "pen_decision",
            "pen_recruit", "pen_conservative_penalty",
        ])
        money = sum(bd.get(k, 0) for k in [
            "money_margin", "money_growth",
            "money_per_capita", "money_scale",
        ])
        console.print(
            f"  {rank:>2}. [cyan]{c.corp_name:<20}[/cyan] "
            f"총[yellow]{total}[/yellow] "
            f"뚫[green]{pen}[/green] "
            f"매[magenta]{money}[/magenta] "
            f"{(c.revenue or 0) / 1e8:.0f}억 "
            f"{c.employees}명"
        )


@app.command()
def check_key():
    settings.validate()
    with DartClient() as dart:
        corps = dart.fetch_all_corp_codes()
        listed = sum(1 for v in corps.values() if v["stock_code"])
        console.print(
            f"[green]✓ API 키 정상[/green] "
            f"전체 {len(corps):,}개 "
            f"상장 {listed:,}개 "
            f"비상장 {len(corps) - listed:,}개"
        )


if __name__ == "__main__":
    app()
