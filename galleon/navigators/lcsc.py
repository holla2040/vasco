"""LCSC navigator — official LCSC Open API (ips.lcsc.com).

Requires LCSC_API_KEY and LCSC_API_SECRET in .env file.
Apply for credentials at support@lcsc.com.

Auth: signature = sha1("key={key}&nonce={nonce}&secret={secret}&timestamp={ts}")
Timestamp must be within 60s of server time.

Commands:
  search <keyword>                     Keyword/MPN search (max 30 results/page)
  details <product_number>             Full product details (e.g. C15742)
  categories                           List all categories
  category <category_id>               List products in a category
"""

import asyncio
import hashlib
import json
import os
import secrets
import sys
import time

import httpx
from dotenv import load_dotenv

from galleon.cache import get as cache_get, put as cache_put

load_dotenv()

BASE_URL = "https://ips.lcsc.com"
SOURCE = "lcsc"
HEADERS = {"User-Agent": "Galleon/0.1 (component-sourcing-agent)"}


# ---------------------------------------------------------------------------
# Auth
# ---------------------------------------------------------------------------

def _get_credentials() -> tuple[str, str]:
    key = os.environ.get("LCSC_API_KEY", "")
    secret = os.environ.get("LCSC_API_SECRET", "")
    if not key or not secret:
        raise RuntimeError(
            "LCSC_API_KEY and LCSC_API_SECRET must be set in .env. "
            "Apply at support@lcsc.com."
        )
    return key, secret


def _sign(key: str, secret: str) -> dict:
    """Generate auth query params: key, nonce, timestamp, signature."""
    nonce = secrets.token_hex(8)  # 16 hex chars
    timestamp = str(int(time.time()))
    raw = f"key={key}&nonce={nonce}&secret={secret}&timestamp={timestamp}"
    signature = hashlib.sha1(raw.encode()).hexdigest()
    return {
        "key": key,
        "nonce": nonce,
        "timestamp": timestamp,
        "signature": signature,
    }


def _auth_params(**extra) -> dict:
    """Build query params with auth + any extra params."""
    key, secret = _get_credentials()
    params = _sign(key, secret)
    params.update(extra)
    return params


# ---------------------------------------------------------------------------
# HTTP
# ---------------------------------------------------------------------------

MAX_RETRIES = 3
RETRY_DELAYS = [1, 2, 4]


async def _request(method: str, path: str, **kwargs) -> dict:
    """Make authenticated request with retry logic. Returns parsed JSON result."""
    url = f"{BASE_URL}{path}"
    last_exc = None
    for attempt in range(MAX_RETRIES):
        try:
            async with httpx.AsyncClient(timeout=20, headers=HEADERS) as client:
                resp = await getattr(client, method)(url, **kwargs)
                if resp.status_code in (502, 503, 504) and attempt < MAX_RETRIES - 1:
                    await asyncio.sleep(RETRY_DELAYS[attempt])
                    continue
                resp.raise_for_status()
                body = resp.json()
                if not body.get("success"):
                    code = body.get("code", "?")
                    msg = body.get("message", "Unknown error")
                    raise RuntimeError(f"LCSC API error {code}: {msg}")
                return body.get("result", {})
        except httpx.HTTPStatusError:
            raise
        except httpx.RequestError as e:
            last_exc = e
            if attempt < MAX_RETRIES - 1:
                await asyncio.sleep(RETRY_DELAYS[attempt])
                continue
            raise
    raise last_exc  # type: ignore[misc]


# ---------------------------------------------------------------------------
# Normalization
# ---------------------------------------------------------------------------

def _normalize_product(p: dict) -> dict:
    """Normalize an LCSC product record to standard format."""
    pricing = []
    for tier in p.get("productPriceList", []) or []:
        qty = tier.get("ladder") or tier.get("startNumber")
        price = tier.get("productPrice") or tier.get("discountPrice")
        if qty is not None and price is not None:
            pricing.append({"qty": qty, "unit_price": float(price)})

    attrs = {}
    for param in p.get("paramVOList", []) or []:
        name = param.get("paramNameEn", "")
        value = param.get("paramValueEn", "")
        if name and value:
            attrs[name] = value

    return {
        "mpn": p.get("productModel", ""),
        "manufacturer": p.get("brandNameEn", ""),
        "lcsc_code": p.get("productCode", ""),
        "description": p.get("productIntroEn", ""),
        "category": p.get("parentCatalogName", ""),
        "subcategory": p.get("catalogName", ""),
        "stock": p.get("stockNumber", 0),
        "package": p.get("encapStandard", ""),
        "datasheet_url": p.get("pdfUrl", ""),
        "product_url": p.get("productUrl", ""),
        "pricing": pricing,
        "attributes": attrs,
        "image_url": p.get("productImageUrl", ""),
    }


def _make_envelope(products_raw: list, query: str, total: int | None = None) -> dict:
    products = [_normalize_product(p) for p in products_raw]
    return {
        "source": SOURCE,
        "query": query,
        "total_count": total if total is not None else len(products),
        "products": products,
    }


# ---------------------------------------------------------------------------
# API commands
# ---------------------------------------------------------------------------

async def search(
    keyword: str,
    page: int = 1,
    page_size: int = 30,
    match_type: str = "fuzzy",
    in_stock: bool = False,
) -> dict:
    """Keyword search. Returns up to 30 products per page."""
    cache_key = f"search:{keyword}:{page}:{match_type}:{in_stock}"
    cached = await cache_get(SOURCE, cache_key)
    if cached is not None:
        return cached

    params = _auth_params(
        keyword=keyword,
        current_page=str(page),
        page_size=str(page_size),
        match_type=match_type,
        currency="USD",
        is_available=str(in_stock).lower(),
    )
    data = await _request("get", "/rest/wmsc2agent/search/product", params=params)

    products_raw = data.get("productList", []) or []
    total = data.get("totalCount", len(products_raw))
    result = _make_envelope(products_raw, keyword, total)
    await cache_put(SOURCE, cache_key, result)
    return result


async def details(product_number: str) -> dict:
    """Get full product details by LCSC part number (e.g. C15742)."""
    cache_key = f"details:{product_number}"
    cached = await cache_get(SOURCE, cache_key)
    if cached is not None:
        return cached

    params = _auth_params(
        product_number=product_number,
        currency="USD",
    )
    data = await _request(
        "get",
        f"/rest/wmsc2agent/product/info/{product_number}",
        params=params,
    )

    result = _make_envelope([data] if data else [], product_number)
    await cache_put(SOURCE, cache_key, result)
    return result


async def list_categories() -> dict:
    """List all product categories."""
    cached = await cache_get(SOURCE, "categories")
    if cached is not None:
        return cached

    params = _auth_params()
    data = await _request("get", "/rest/wmsc2agent/category", params=params)

    # data is expected to be a list of category objects
    cats = data if isinstance(data, list) else []
    result = {"source": SOURCE, "categories": cats, "total_count": len(cats)}
    await cache_put(SOURCE, "categories", result)
    return result


async def category_products(
    category_id: int,
    page: int = 1,
    page_size: int = 30,
    in_stock: bool = False,
) -> dict:
    """List products in a specific category."""
    cache_key = f"category:{category_id}:{page}:{in_stock}"
    cached = await cache_get(SOURCE, cache_key)
    if cached is not None:
        return cached

    params = _auth_params(
        current_page=str(page),
        page_size=str(page_size),
        currency="USD",
        is_available=str(in_stock).lower(),
    )
    data = await _request(
        "get",
        f"/rest/wmsc2agent/category/product/{category_id}",
        params=params,
    )

    products_raw = data.get("productList", []) or []
    total = data.get("totalCount", len(products_raw))
    result = _make_envelope(products_raw, f"category:{category_id}", total)
    await cache_put(SOURCE, cache_key, result)
    return result


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def _error(msg: str) -> None:
    print(json.dumps({"error": msg}), file=sys.stderr)
    sys.exit(1)


USAGE = """Usage: python -m galleon.navigators.lcsc <command> [args]

Commands:
  search <keyword> [--page N] [--exact] [--in-stock]
  details <lcsc_code>
  categories
  category <id> [--page N] [--in-stock]

Examples:
  python -m galleon.navigators.lcsc search "STM32F405RGT6"
  python -m galleon.navigators.lcsc search "optocoupler" --in-stock
  python -m galleon.navigators.lcsc details C15742
  python -m galleon.navigators.lcsc categories
  python -m galleon.navigators.lcsc category 11329 --in-stock"""


async def _main() -> None:
    args = sys.argv[1:]
    if not args or args[0] in ("-h", "--help"):
        print(USAGE)
        sys.exit(0)

    command = args[0]

    try:
        if command == "search":
            if len(args) < 2:
                _error("Missing keyword. Usage: search <keyword>")
            keyword = args[1]
            page = 1
            match_type = "fuzzy"
            in_stock = False
            i = 2
            while i < len(args):
                if args[i] == "--page" and i + 1 < len(args):
                    page = int(args[i + 1])
                    i += 2
                elif args[i] == "--exact":
                    match_type = "exact"
                    i += 1
                elif args[i] == "--in-stock":
                    in_stock = True
                    i += 1
                else:
                    _error(f"Unknown argument: {args[i]}")
            result = await search(keyword, page=page, match_type=match_type, in_stock=in_stock)
            print(json.dumps(result, indent=2))

        elif command == "details":
            if len(args) < 2:
                _error("Missing product number. Usage: details <lcsc_code>")
            print(json.dumps(await details(args[1]), indent=2))

        elif command == "categories":
            print(json.dumps(await list_categories(), indent=2))

        elif command == "category":
            if len(args) < 2:
                _error("Missing category ID. Usage: category <id>")
            cat_id = int(args[1])
            page = 1
            in_stock = False
            i = 2
            while i < len(args):
                if args[i] == "--page" and i + 1 < len(args):
                    page = int(args[i + 1])
                    i += 2
                elif args[i] == "--in-stock":
                    in_stock = True
                    i += 1
                else:
                    _error(f"Unknown argument: {args[i]}")
            result = await category_products(cat_id, page=page, in_stock=in_stock)
            print(json.dumps(result, indent=2))

        else:
            _error(f"Unknown command: {command}. Run with --help for usage.")

    except RuntimeError as e:
        _error(str(e))
    except httpx.HTTPStatusError as e:
        _error(f"HTTP error: {e}")
    except httpx.RequestError as e:
        _error(f"Request failed: {e}")


if __name__ == "__main__":
    asyncio.run(_main())
