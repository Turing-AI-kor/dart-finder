import io
import xml.etree.ElementTree as ET
import zipfile

import httpx

from .config import settings


class DartClient:
    def __init__(self):
        self.client = httpx.Client(
            base_url=settings.DART_BASE_URL,
            timeout=30.0,
            headers={"Accept": "application/json"},
        )
        self._cache = None

    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.client.close()

    def fetch_all_corp_codes(self) -> dict:
        if self._cache:
            return self._cache

        response = self.client.get(
            "/corpCode.xml",
            params={"crtfc_key": settings.OPENDART_API_KEY},
        )
        response.raise_for_status()

        with zipfile.ZipFile(io.BytesIO(response.content)) as zf:
            with zf.open(zf.namelist()[0]) as f:
                tree = ET.parse(f)

        result = {}
        for item in tree.getroot().findall(".//list"):
            code = item.findtext("corp_code", "").strip()
            if code:
                result[code] = {
                    "corp_name": item.findtext("corp_name", "").strip(),
                    "stock_code": item.findtext("stock_code", "").strip(),
                    "modify_date": item.findtext("modify_date", "").strip(),
                }

        self._cache = result
        return result

    @staticmethod
    def parse_financial(items: list) -> dict:
        result = {
            "revenue": None,
            "operating_profit": None,
            "net_income": None,
            "revenue_prev": None,
            "growth_rate": None,
        }

        for item in items:
            account_name = item.get("account_nm", "")
            statement_div = item.get("sj_div", "")

            try:
                amount = int((item.get("thstrm_amount") or "0").replace(",", "") or 0)
                prev = int((item.get("frmtrm_amount") or "0").replace(",", "") or 0)
            except ValueError:
                continue

            if statement_div not in ("IS", "CIS"):
                continue

            if account_name in ("매출액", "수익(매출액)") and result["revenue"] is None:
                result["revenue"] = amount
                result["revenue_prev"] = prev or None
            elif account_name == "영업이익" and result["operating_profit"] is None:
                result["operating_profit"] = amount
            elif account_name in ("당기순이익", "당기순이익(손실)") and result["net_income"] is None:
                result["net_income"] = amount

        if result["revenue"] and result["revenue_prev"] and result["revenue_prev"] > 0:
            result["growth_rate"] = (result["revenue"] - result["revenue_prev"]) / result["revenue_prev"]

        return result
