"""Merge compiled report indexes without needing historical Markdown sources."""

from __future__ import annotations

import gzip
import re
from collections import Counter
from copy import deepcopy
from pathlib import Path
from urllib.parse import unquote, urlsplit
from xml.etree import ElementTree as ET

from bs4 import BeautifulSoup, Tag

from scripts.report_workflow import WorkflowError

DATE_HEADING = re.compile(r"^(\d{4}-\d{2}-\d{2})-decision-summary$")


def required(page: Tag, selector: str) -> Tag:
    element = page.select_one(selector)
    if element is None:
        raise WorkflowError(f"Cannot preserve published history: missing {selector}")
    return element


def read_page(path: Path) -> BeautifulSoup:
    return BeautifulSoup(path.read_text(encoding="utf-8"), "html.parser")


def report_paths(site_dir: Path) -> set[str]:
    return {
        path.relative_to(site_dir).as_posix()
        for path in site_dir.glob("*/*/complete_report/index.html")
    }


def daily_sections(page: BeautifulSoup) -> dict[str, list[Tag]]:
    sections: dict[str, list[Tag]] = {}
    current = None
    for tag in required(page, ".daily-summary-tables").find_all(recursive=False):
        match = DATE_HEADING.fullmatch(str(tag.get("id", "")))
        if tag.name == "h2" and match:
            current = match[1]
            if current in sections:
                raise WorkflowError(f"Duplicate published summary date: {current}")
            sections[current] = []
        if current is not None:
            sections[current].append(deepcopy(tag))
    if not sections:
        raise WorkflowError("Cannot preserve history: no published daily summaries found")
    for date, tags in sections.items():
        tables = [tag for tag in tags if tag.name == "table"]
        if len(tables) != 1:
            raise WorkflowError(f"Cannot preserve history: expected one summary table for {date}")
        for row in required(tables[0], "tbody").find_all("tr"):
            summary_key(row)
    return sections


def summary_key(row: Tag) -> tuple[str, str]:
    cells = row.find_all("td", recursive=False)
    if len(cells) != 9:
        raise WorkflowError("Cannot preserve history: unexpected decision table columns")
    return cells[0].get_text(strip=True), cells[1].get_text(strip=True)


def merge_summary(old: Tag, new: Tag) -> None:
    rows = {summary_key(row): deepcopy(row) for row in required(old, "tbody").find_all("tr")}
    for row in required(new, "tbody").find_all("tr"):
        key = summary_key(row)
        # Report folders end in a sortable run timestamp. A backfilled earlier
        # run must not replace the newer recommendation for the same model/date.
        old_href = str(required(rows[key], "a[href]")["href"]) if key in rows else ""
        new_href = str(required(row, "a[href]")["href"])
        if unquote(urlsplit(new_href).path).lstrip("./") >= unquote(urlsplit(old_href).path).lstrip(
            "./"
        ):
            rows[key] = deepcopy(row)
    body = required(old, "tbody")
    body.clear()
    for key in sorted(rows):
        body.append(rows[key])


def navigation(page: BeautifulSoup, tickers: list[str], current: str = "") -> None:
    """Refresh navigation on indexes; immutable historical report pages stay intact."""
    prefix = "../" if current else ""
    links = [("", "Home", prefix or ".")] + [
        (ticker, ticker, f"{prefix}{ticker}/") for ticker in tickers
    ]
    for selector, item_class, link_class, active_class in (
        (".md-tabs__list", "md-tabs__item", "md-tabs__link", "md-tabs__item--active"),
        (
            "nav.md-nav--primary > .md-nav__list",
            "md-nav__item",
            "md-nav__link",
            "md-nav__item--active",
        ),
    ):
        container = page.select_one(selector)
        if container is None:
            continue
        container.clear()
        for key, label, href in links:
            item = page.new_tag("li", attrs={"class": [item_class]})
            anchor = page.new_tag("a", href=href, attrs={"class": [link_class]})
            anchor.string = label
            if key == current:
                item["class"].append(active_class)
            item.append(anchor)
            container.append(item)


def merge_home(old_path: Path, new_path: Path, paths: set[str], focus: str) -> None:
    old, new = read_page(old_path), read_page(new_path)
    sections = daily_sections(old)
    for date, tags in daily_sections(new).items():
        if date in sections:
            old_table = next(tag for tag in sections[date] if tag.name == "table")
            new_table = next(tag for tag in tags if tag.name == "table")
            merge_summary(old_table, new_table)
        else:
            sections[date] = tags

    tables = required(new, ".daily-summary-tables")
    rail = required(new, ".daily-summary-rail ul")
    tables.clear()
    rail.clear()
    for date in sorted(sections, reverse=True):
        for tag in sections[date]:
            tables.append(tag)
        count = len(next(tag for tag in sections[date] if tag.name == "table").select("tbody tr"))
        item = new.new_tag("li")
        anchor = new.new_tag("a", href=f"#{date}-decision-summary")
        anchor["class"] = ["daily-summary-date"]
        if date == focus:
            anchor["class"].append("daily-summary-date--active")
        anchor.string = date
        item.append(anchor)
        label = new.new_tag("span")
        label.string = f"{count} report{'s' if count != 1 else ''}"
        item.append(label)
        rail.append(item)

    counts = Counter(path.split("/", 1)[0] for path in paths)
    article = required(new, "article.md-content__inner")
    required(article, "p em").string = f"{len(paths)} runs across {len(counts)} tickers."
    ticker_list = required(article, "#tickers").find_next_sibling("ul")
    if ticker_list is None:
        raise WorkflowError("Cannot preserve history: missing ticker list")
    ticker_list.clear()
    for ticker in sorted(counts):
        item = new.new_tag("li")
        anchor = new.new_tag("a", href=f"{ticker}/")
        anchor.string = ticker
        item.append(anchor)
        item.append(f" · {counts[ticker]} runs")
        ticker_list.append(item)
    # Replace the summary TOC too; otherwise restored dates have no TOC links.
    for toc in new.select("nav.md-nav--secondary > ul"):
        toc.clear()
        for heading in article.select("h2[id]"):
            item = new.new_tag("li", attrs={"class": "md-nav__item"})
            anchor = new.new_tag("a", href=f"#{heading['id']}", attrs={"class": "md-nav__link"})
            anchor.string = heading.get_text(" ", strip=True).rstrip("¶").strip()
            item.append(anchor)
            toc.append(item)
    navigation(new, sorted(counts))
    new_path.write_text(str(new), encoding="utf-8")


def merge_ticker(old_path: Path, new_path: Path, tickers: list[str]) -> None:
    old, new = read_page(old_path), read_page(new_path)
    rows: dict[str, Tag] = {}
    for page in (old, new):
        for row in required(page, "article table tbody").find_all("tr"):
            rows[str(required(row, "a[href]")["href"])] = deepcopy(row)
    body = required(new, "article table tbody")
    body.clear()

    def run_order(row: Tag) -> tuple[str, str]:
        cells = row.find_all("td", recursive=False)
        return cells[0].get_text(strip=True), cells[2].get_text(strip=True)

    for row in sorted(rows.values(), key=run_order, reverse=True):
        body.append(row)
    required(new, "article p em").string = f"{len(rows)} run(s)."
    navigation(new, tickers, new_path.parent.name)
    new_path.write_text(str(new), encoding="utf-8")


def merge_sitemap(old_path: Path, new_path: Path) -> None:
    namespace = "http://www.sitemaps.org/schemas/sitemap/0.9"
    ET.register_namespace("", namespace)
    urls: dict[str, ET.Element] = {}
    for path in (old_path, new_path):
        if not path.is_file():
            raise WorkflowError(f"Missing publication sitemap: {path}")
        try:
            root = ET.parse(path).getroot()
        except ET.ParseError as exc:
            raise WorkflowError(f"Invalid publication sitemap: {path}") from exc
        for entry in root:
            location = entry.findtext(f"{{{namespace}}}loc")
            if location:
                urls[location] = entry
    root = ET.Element(f"{{{namespace}}}urlset")
    root.extend(urls[url] for url in sorted(urls))
    data = ET.tostring(root, encoding="utf-8", xml_declaration=True)
    new_path.write_bytes(data)
    new_path.with_suffix(".xml.gz").write_bytes(gzip.compress(data, mtime=0))


def validate_indexes(site_dir: Path, paths: set[str]) -> None:
    """Require every report to remain reachable from its ticker hub."""
    tickers = {path.split("/", 1)[0] for path in paths}
    for relative, expected in [("index.html", None)] + [
        (f"{ticker}/index.html", {p for p in paths if p.startswith(f"{ticker}/")})
        for ticker in sorted(tickers)
    ]:
        path = site_dir / relative
        page = read_page(path)
        linked = set()
        for anchor in required(page, "article").select("a[href]"):
            url = urlsplit(str(anchor["href"]))
            if url.scheme or url.netloc or "complete_report" not in url.path:
                continue
            target = (path.parent / unquote(url.path)).resolve()
            if url.path.endswith("/"):
                target /= "index.html"
            if not target.is_relative_to(site_dir.resolve()) or not target.is_file():
                raise WorkflowError(f"Broken report link in {relative}: {url.path}")
            linked.add(target.relative_to(site_dir.resolve()).as_posix())
        if expected is not None and linked != expected:
            raise WorkflowError(f"Ticker index does not cover all published reports: {relative}")
