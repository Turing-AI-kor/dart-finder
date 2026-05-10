import io
import time
import xml.etree.ElementTree as ET
import zipfile

import httpx
from tenacity import retry, stop_after_attempt, wait_exponential

from .config import settings


class DartClient:
    def __init__(self):
        self.client = httpx.Client(
            base_url=settings.DART_BASE_URL,
            timeout=30.0,
            headers={"Accept": "application/json"},
        )
        self._corp_code_cache: dict[str, dict] | None = None

    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.client.close()

    def fetch_all_corp_codes(self) -> dict[str, dict]:
        if self._corp_code_cache is not None:
            return self._corp_code_cache

        response = self.client.get(
            "/corpCode.xml",
            params={"crtfc_key": settings.OPENDART_API_KEY},
        )
        response.raise_for_status()

        if response.content[:1] == b"{":
            raise RuntimeError(f"DART API 에러: {response.text}")

        with zipfile.ZipFile(io.BytesIO(response.content)) as zf:
            with zf.open(zf.namelist()[0]) as f:
                tree = ET.parse(f)

        result = {}
        for item in tree.getroot().findall(".//list"):
            corp_code = item.findtext("corp_code", "").strip()
            if corp_code:
                result[corp_code] = {
                    "corp_name":   item.findtext("corp_name", "").strip(),
                    "stock_code":  item.findtext("stock_code", "").strip(),
                    "modify_date": item.findtext("modify_date", "").strip(),
                }
        self._corp_code_cache = result
        return result

    @retry(stop=stop_after_attempt(3), wait=wait_exponential(min=1, max=10))
    def get_company_info(self, corp_code: str) -> dict | None:
        time.sleep(settings.API_DELAY_SECONDS)
        response = self.client.get(
            "/company.json",
            params={"crtfc_key": settings.OPENDART_API_KEY, "corp_code": corp_code},
        )
        response.raise_for_status()
        data = response.json()
        if data.get("status") != "000":
            return None
        return data

    @retry(stop=stop_after_attempt(3), wait=wait_exponential(min=1, max=10))
    def get_employee_status(self, corp_code: str, year: int) -> list[dict]:
        time.sleep(settings.API_DELAY_SECONDS)
        response = self.client.get(
            "/empSttus.json",
            params={
                "crtfc_key":  settings.OPENDART_API_KEY,
                "corp_code":  corp_code,
                "bsns_year":  str(year),
                "reprt_code": "11011",
            },
        )
        response.raise_for_status()
        data = response.json()
        if data.get("status") != "000":
            return []
        return data.get("list", [])

    @retry(stop=stop_after_attempt(3), wait=wait_exponential(min=1, max=10))
    def get_executives(self, corp_code: str, year: int) -> list[dict]:
        time.sleep(settings.API_DELAY_SECONDS)
        response = self.client.get(
            "/exctvSttus.json",
            params={
                "crtfc_key":  settings.OPENDART_API_KEY,
                "corp_code":  corp_code,
                "bsns_year":  str(year),
                "reprt_code": "11011",
            },
        )
        response.raise_for_status()
        data = response.json()
        if data.get("status") != "000":
            return []
        return data.get("list", [])

    @retry(stop=stop_after_attempt(3), wait=wait_exponential(min=1, max=10))
    def get_financial_summary(self, corp_code: str, year: int) -> dict | None:
        time.sleep(settings.API_DELAY_SECONDS)
        for fs_div in ["CFS", "OFS"]:
            response = self.client.get(
                "/fnlttSinglAcntAll.json",
                params={
                    "crtfc_key":  settings.OPENDART_API_KEY,
                    "corp_code":  corp_code,
                    "bsns_year":  str(year),
                    "reprt_code": "11011",
                    "fs_div":     fs_div,
                },
            )
            response.raise_for_sta
