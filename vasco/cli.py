"""Thin typer CLI wrapper for vasco navigators."""

import asyncio
import json
from typing import Optional

import typer
from rich.console import Console
from rich.syntax import Syntax

app = typer.Typer(name="vasco", help="Electronic component sourcing agent")
console = Console()


def _print_json(data: dict) -> None:
    console.print(Syntax(json.dumps(data, indent=2), "json"))


# -- LCSC subcommand (official API) -------------------------------------------
lcsc_app = typer.Typer(help="LCSC component search (official API, requires credentials)")
app.add_typer(lcsc_app, name="lcsc")


@lcsc_app.command("search")
def lcsc_search(
    keyword: str = typer.Argument(..., help="MPN or keyword"),
    page: int = typer.Option(1, help="Page number"),
    exact: bool = typer.Option(False, help="Exact match only"),
    in_stock: bool = typer.Option(False, help="Only in-stock parts"),
):
    """Search LCSC by keyword or MPN."""
    from vasco.navigators.lcsc import search
    _print_json(asyncio.run(search(keyword, page=page, match_type="exact" if exact else "fuzzy", in_stock=in_stock)))


@lcsc_app.command("details")
def lcsc_details(product_number: str = typer.Argument(..., help="LCSC code (e.g. C15742)")):
    """Get full product details."""
    from vasco.navigators.lcsc import details
    _print_json(asyncio.run(details(product_number)))


@lcsc_app.command("categories")
def lcsc_categories():
    """List all product categories."""
    from vasco.navigators.lcsc import list_categories
    _print_json(asyncio.run(list_categories()))


@lcsc_app.command("category")
def lcsc_category(
    category_id: int = typer.Argument(..., help="Category ID"),
    page: int = typer.Option(1, help="Page number"),
    in_stock: bool = typer.Option(False, help="Only in-stock parts"),
):
    """List products in a category."""
    from vasco.navigators.lcsc import category_products
    _print_json(asyncio.run(category_products(category_id, page=page, in_stock=in_stock)))


# -- jlcsearch subcommand (fallback, no auth) ---------------------------------
jlcsearch_app = typer.Typer(help="jlcsearch.tscircuit.com (fallback, no auth, may be flaky)")
app.add_typer(jlcsearch_app, name="jlcsearch")


@jlcsearch_app.command("search")
def jlcsearch_search(keyword: str = typer.Argument(..., help="MPN or keyword")):
    """Keyword search."""
    from vasco.navigators.jlcsearch import search
    _print_json(asyncio.run(search(keyword)))


@jlcsearch_app.command("filter")
def jlcsearch_filter(
    category: Optional[str] = typer.Option(None, "-c", "--category", help="Subcategory name"),
    package: Optional[str] = typer.Option(None, "-p", "--package", help="Package type"),
    search: Optional[str] = typer.Option(None, "-s", "--search", help="Keyword filter"),
    limit: int = typer.Option(50, "-l", "--limit", help="Max results"),
):
    """Parametric search by category/package."""
    from vasco.navigators.jlcsearch import filter_parts
    _print_json(asyncio.run(filter_parts(category, package, search, limit)))


@jlcsearch_app.command("categories")
def jlcsearch_categories(query: Optional[str] = typer.Argument(None, help="Filter by keyword")):
    """List categories."""
    from vasco.navigators.jlcsearch import categories
    _print_json(asyncio.run(categories(query)))


@jlcsearch_app.command("health")
def jlcsearch_health():
    """Check API status."""
    from vasco.navigators.jlcsearch import health
    _print_json(asyncio.run(health()))


# -- DigiKey subcommand --------------------------------------------------------
digikey_app = typer.Typer(help="DigiKey component search (requires API credentials)")
app.add_typer(digikey_app, name="digikey")


@digikey_app.command("search")
def digikey_search(
    keyword: str = typer.Argument(..., help="MPN or keyword (max 250 chars)"),
    limit: int = typer.Option(50, help="Results per page (1-50)"),
    category_id: Optional[int] = typer.Option(None, help="Filter by category ID"),
    manufacturer_id: Optional[int] = typer.Option(None, help="Filter by manufacturer ID"),
    in_stock: bool = typer.Option(False, help="Only in-stock parts"),
    sort: Optional[str] = typer.Option(None, help="Sort: Price, QuantityAvailable, Manufacturer"),
):
    """Search DigiKey by keyword or MPN with optional filters."""
    from vasco.navigators.digikey import search
    _print_json(asyncio.run(search(
        keyword, limit=limit, category_id=category_id,
        manufacturer_id=manufacturer_id, in_stock=in_stock, sort_by=sort,
    )))


@digikey_app.command("details")
def digikey_details(product_number: str = typer.Argument(..., help="DigiKey product number")):
    """Get full product details + parameters."""
    from vasco.navigators.digikey import details
    _print_json(asyncio.run(details(product_number)))


@digikey_app.command("substitutions")
def digikey_substitutions(product_number: str = typer.Argument(..., help="DigiKey product number")):
    """Find alternate/substitute products."""
    from vasco.navigators.digikey import substitutions
    _print_json(asyncio.run(substitutions(product_number)))


@digikey_app.command("categories")
def digikey_categories():
    """List all product categories."""
    from vasco.navigators.digikey import list_categories
    _print_json(asyncio.run(list_categories()))


@digikey_app.command("manufacturers")
def digikey_manufacturers():
    """List all manufacturers."""
    from vasco.navigators.digikey import list_manufacturers
    _print_json(asyncio.run(list_manufacturers()))


# -- Cache subcommand ----------------------------------------------------------
cache_app = typer.Typer(help="Cache management")
app.add_typer(cache_app, name="cache")


@cache_app.command("cleanup")
def cache_cleanup():
    """Remove expired cache entries."""
    from vasco.cache import cleanup
    deleted = asyncio.run(cleanup())
    console.print(f"Deleted {deleted} expired entries.")


if __name__ == "__main__":
    app()
