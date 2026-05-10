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
            "corp_code": corp_code, "bsns_year": str(year),
            "reprt_code": "11011", "fs_div": fs_div,
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
                _async_get(client, "/empSttus.json", {"corp_code": corp_code, "bsns_year": str(year), "reprt_code": "11011"}),
                _async_get(client, "/exctvSttus.json", {"corp_code": corp_code, "bsns_year": str(year), "reprt_code": "11011"}),
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
        corp_name=base["corp_name"], corp_code=corp_code,
        stock_code=base["stock_code"], ceo_name=info.get("ceo_nm", ""),
        industry=(info.get("induty_code") or "").strip(),
        address=info.get("adres", ""), homepage=info.get("hm_url", ""),
        revenue=fin.get("revenue"), revenue_prev=fin.get("revenue_prev"),
        growth_rate=fin.get("growth_rate"),
        operating_profit=fin.get("operating_profit"),
        net_income=fin.get("net_income"),
        employees=total_emp, executives=exec_list,
        establish_date=info.get("est_dt", ""),
    )


def _save_checkpoint(path, results):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        json.dump([{
            "corp_name": c.corp_name, "corp_code": c.corp_code,
            "stock_code": c.stock_code, "ceo_name": c.ceo_name,
            "industry": c.industry, "address": c.address,
            "homepage": c.homepage, "revenue": c.revenue,
            "revenue_prev": c.revenue_prev, "growth_rate": c.growth_rate,
            "operating_profit": c.operating_profit, "net_income": c.net_income,
            "employees": c.employees, "executives": c.
