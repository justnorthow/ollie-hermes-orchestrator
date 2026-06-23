import calendar
import logging
import os

import httpx
from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse

from src.api.geo import normalize_region_key
from src.auth import require_bearer

_logger = logging.getLogger(__name__)

router = APIRouter(tags=["market-data"], dependencies=[Depends(require_bearer)])

_VALID_TYPES = ("zip", "city", "county")


def _num(v):
    if v is None:
        return None
    s = str(v).strip()
    if s == "" or s.upper() in ("NA", "NULL"):
        return None
    try:
        return float(s)
    except ValueError:
        return None


def _month_label(period_end: str) -> str:
    try:
        y, m, _ = period_end.split("-")
        return f"{calendar.month_name[int(m)]} {int(y)}"
    except Exception:
        return ""


def _fmt_price(v) -> str:
    n = _num(v)
    return "" if n is None else "${:,.0f}".format(n)


def _fmt_yoy(v) -> str:
    n = _num(v)
    if n is None:
        return ""
    direction = "up" if n >= 0 else "down"
    return f"{direction} {abs(n) * 100:.1f}% from a year ago"


def _fmt_months(v) -> str:
    n = _num(v)
    return "" if n is None else f"{n:g} months"


def _fmt_dom(v) -> str:
    n = _num(v)
    return "" if n is None else f"{int(round(n))} days"


def _fmt_volume(v) -> str:
    n = _num(v)
    return "" if n is None else "{:,.0f} closed sales".format(n)


def build_response(region_type: str, value: str, row: dict | None, rate: dict | None) -> dict:
    row = row or {}
    rate = rate or {}

    price = _fmt_price(row.get("median_sale_price"))
    yoy = _fmt_yoy(row.get("median_sale_price_yoy"))
    median = price + (f", {yoy}" if price and yoy else "")

    fields = {
        "month": _month_label(row.get("period_end", "")) if row else "",
        "medianSoldPrice": median,
        "inventoryMonths": _fmt_months(row.get("months_of_supply")),
        "daysOnMarket": _fmt_dom(row.get("median_dom")),
        "salesVolume": _fmt_volume(row.get("homes_sold")),
        "rate30yr": rate.get("rate30yr", ""),
        "rateMovement": rate.get("rateMovement", ""),
    }
    available = {k: bool(v) for k, v in fields.items()}
    unavailable = [k for k, v in fields.items() if not v]

    sources = []
    if row:
        label = row.get("region_label") or value
        sources.append(f"Redfin Data Center ({label}, {_month_label(row.get('period_end', ''))})")
    if rate:
        sources.append("FRED MORTGAGE30US (30-yr fixed)")

    warning = None
    if not row:
        warning = f"No market data on file for {value}. Please enter the local figures manually."

    out = dict(fields)
    out["sources"] = sources
    out["as_of"] = row.get("as_of") if row else None
    out["available"] = available
    out["unavailable"] = unavailable
    out["warning"] = warning
    return out


def _fetch_market_row(region_type: str, region_key: str, url: str, key: str) -> dict | None:
    """Latest-period market_data row for (region_type, region_key), service-role."""
    resp = httpx.get(
        f"{url}/rest/v1/market_data",
        params={
            "region_type": f"eq.{region_type}",
            "region_key": f"eq.{region_key}",
            "order": "period_end.desc",
            "limit": "1",
        },
        headers={"apikey": key, "Authorization": f"Bearer {key}"},
        timeout=10.0,
    )
    resp.raise_for_status()
    data = resp.json()
    return data[0] if isinstance(data, list) and data else None


def _fetch_rate(api_key: str) -> dict | None:
    """Latest two FRED MORTGAGE30US observations -> rate30yr + rateMovement."""
    resp = httpx.get(
        "https://api.stlouisfed.org/fred/series/observations",
        params={
            "series_id": "MORTGAGE30US", "api_key": api_key, "file_type": "json",
            "sort_order": "desc", "limit": "2",
        },
        timeout=10.0,
    )
    resp.raise_for_status()
    obs = [o for o in resp.json().get("observations", []) if o.get("value") not in (None, ".", "")]
    if not obs:
        return None
    latest = float(obs[0]["value"])
    out = {"rate30yr": f"{latest:.2f}%", "rateMovement": ""}
    if len(obs) > 1:
        prev = float(obs[1]["value"])
        if latest < prev:
            out["rateMovement"] = f"down from {prev:.2f}%"
        elif latest > prev:
            out["rateMovement"] = f"up from {prev:.2f}%"
        else:
            out["rateMovement"] = f"flat at {latest:.2f}%"
    return out


@router.get("/v1/market-data")
def get_market_data(request: Request, type: str = "", value: str = ""):
    url = os.environ.get("SUPABASE_URL", "").strip().rstrip("/")
    key = os.environ.get("SUPABASE_SERVICE_ROLE_KEY", "").strip()
    if not url or not key:
        return JSONResponse({"detail": "Market data lookup not configured"}, status_code=503)
    # X-Auth-Email is trusted only because require_bearer + nginx strip/inject it (see profile.py).
    email = request.headers.get("X-Auth-Email", "").strip()
    if not email:
        return JSONResponse({"detail": "No authenticated user"}, status_code=401)

    region_type = (type or "").strip().lower()
    if region_type not in _VALID_TYPES:
        return JSONResponse({"detail": "type must be one of zip, city, county"}, status_code=400)
    region_key = normalize_region_key(region_type, value)

    row = None
    try:
        row = _fetch_market_row(region_type, region_key, url, key)
    except Exception:
        _logger.exception("market_data lookup failed for %s/%s", region_type, region_key)
        # degrade: leave local fields unavailable + warning; never 5xx.

    rate = None
    fred_key = os.environ.get("FRED_API_KEY", "").strip()
    if fred_key:
        try:
            rate = _fetch_rate(fred_key)
        except Exception:
            _logger.exception("FRED rate fetch failed")

    return JSONResponse(content=build_response(region_type, value, row, rate),
                        headers={"Cache-Control": "no-store"})
