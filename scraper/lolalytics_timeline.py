"""
Lolalytics time-trend fetching.

The mega endpoints used by lolalytics.py do not expose time/timeWin buckets.
Those fields still live in the champion build page Qwik JSON, so this module
keeps that slower and more fragile path isolated from the core tier/synergy/
counter pipeline.
"""
import html as html_lib
import json
import os
import re
import time
from urllib.parse import urlencode

from . import lolalytics as la


DEFAULT_TIER = "emerald_plus"
DEFAULT_QUEUE = "ranked"
DEFAULT_REGION = "kr"
DEFAULT_PATCH = None
TIME_PROFILE_SOURCE = "lolalytics_build_qwik"


def _champion_slug(champion):
    name = champion.strip().lower()
    if name == "wukong":
        return "monkeyking"
    return name


def build_url(
    champion,
    lane="bottom",
    region=DEFAULT_REGION,
    patch=DEFAULT_PATCH,
    tier=None,
    queue=None,
):
    params = {
        "lane": lane,
        "region": region,
    }
    if tier:
        params["tier"] = tier
    if queue:
        params["queue"] = queue
    if patch:
        params["patch"] = patch
    return f"https://lolalytics.com/lol/{_champion_slug(champion)}/build/?{urlencode(params)}"


def _response_to_html(response):
    for attr in ("html", "text", "content"):
        value = getattr(response, attr, None)
        if callable(value):
            try:
                value = value()
            except TypeError:
                continue
        if isinstance(value, str) and value:
            return value
        if isinstance(value, bytes) and value:
            return value.decode("utf-8", errors="ignore")

    body = getattr(response, "body", None)
    if callable(body):
        body = body()
    if isinstance(body, bytes):
        return body.decode(getattr(response, "encoding", None) or "utf-8", errors="ignore")
    if isinstance(body, str):
        return body

    return str(response)


def _unwrap_view_source(source):
    if "line-content" not in source:
        return source

    cells = re.findall(
        r'<td[^>]*class=["\'][^"\']*line-content[^"\']*["\'][^>]*>(.*?)</td>',
        source,
        flags=re.S,
    )
    if not cells:
        return source

    lines = []
    for cell in cells:
        text = re.sub(r"<[^>]+>", "", cell)
        lines.append(html_lib.unescape(text))
    return "\n".join(lines)


def extract_qwik_json(source):
    html = _unwrap_view_source(source)
    match = re.search(
        r'<script[^>]+type=["\']qwik/json["\'][^>]*>(.*?)</script>',
        html,
        flags=re.S,
    )
    if not match:
        return None
    return html_lib.unescape(match.group(1)).strip()


def parse_time_profile(source):
    raw = extract_qwik_json(source)
    if not raw:
        return []

    payload = json.loads(raw)
    objs = payload.get("objs") if isinstance(payload, dict) else None
    if isinstance(objs, list):
        target_idx = la._find_target(objs)
        if target_idx is not None:
            decoded = la._parse_obj(objs, la._to_base36(target_idx))
            rows = la.extract_stats_by_time(decoded)
            return _clean_rows(rows)

    rows = la.extract_stats_by_time(payload)
    return _clean_rows(rows)


def _clean_rows(rows):
    clean = []
    for row in rows or []:
        try:
            games = float(row.get("games") or 0)
            wins = float(row.get("wins") or 0)
        except (TypeError, ValueError, AttributeError):
            continue
        clean.append(
            {
                "label": row.get("label"),
                "wins": wins,
                "games": games,
            }
        )
    return clean if any(row["games"] > 0 for row in clean) else []


def _get_stealthy_session(headless=True, solve_cloudflare=True):
    try:
        from scrapling.fetchers import StealthySession
    except ImportError as exc:
        raise RuntimeError(
            "Scrapling 未安装。请先运行: "
            'python3 -m pip install "scrapling[fetchers]" && scrapling install'
        ) from exc

    kwargs = {
        "headless": headless,
        "solve_cloudflare": solve_cloudflare,
        "network_idle": False,
        "load_dom": True,
        "disable_resources": True,
        "timeout": 25000,
        "wait_selector": 'script[type="qwik/json"]',
        "wait_selector_state": "attached",
    }
    while True:
        try:
            return StealthySession(**kwargs)
        except TypeError:
            if "network_idle" in kwargs:
                kwargs.pop("network_idle")
                continue
            if "wait_selector_state" in kwargs:
                kwargs.pop("wait_selector_state")
                continue
            if "wait_selector" in kwargs:
                kwargs.pop("wait_selector")
                continue
            if "load_dom" in kwargs:
                kwargs.pop("load_dom")
                continue
            if "disable_resources" in kwargs:
                kwargs.pop("disable_resources")
                continue
            if "timeout" in kwargs:
                kwargs.pop("timeout")
                continue
            if "solve_cloudflare" in kwargs:
                kwargs.pop("solve_cloudflare")
                continue
            raise


def _session_fetch(session, url):
    try:
        return session.fetch(
            url,
            google_search=False,
            load_dom=True,
            network_idle=False,
            disable_resources=True,
            timeout=25000,
            wait_selector='script[type="qwik/json"]',
            wait_selector_state="attached",
        )
    except TypeError:
        return session.fetch(url)


def fetch_time_profile(
    champion,
    lane="bottom",
    region=DEFAULT_REGION,
    patch=DEFAULT_PATCH,
    session=None,
    headless=True,
    solve_cloudflare=True,
):
    url = build_url(champion, lane=lane, region=region, patch=patch)
    owns_session = session is None

    if session is None:
        session = _get_stealthy_session(
            headless=headless,
            solve_cloudflare=solve_cloudflare,
        )

    if owns_session:
        with session:
            page = _session_fetch(session, url)
            return parse_time_profile(_response_to_html(page))

    page = _session_fetch(session, url)
    return parse_time_profile(_response_to_html(page))


def hydrate_time_profiles(
    hero_stats,
    lanes=("bottom", "support"),
    region=DEFAULT_REGION,
    patch=DEFAULT_PATCH,
    limit=None,
    delay=1.0,
    headless=True,
    solve_cloudflare=True,
):
    """
    Fill hero_stats[lane][champion]["stats_by_time"] from Lolalytics build pages.

    Returns a summary dict. Failures are collected instead of raised so the main
    data update can decide whether to fall back to local snapshots.
    """
    summary = {"attempted": 0, "fetched": 0, "errors": []}
    remaining = limit
    if remaining == 0:
        return summary

    with _get_stealthy_session(
        headless=headless,
        solve_cloudflare=solve_cloudflare,
    ) as session:
        for lane in lanes:
            for champion, record in hero_stats.get(lane, {}).items():
                if remaining is not None and remaining <= 0:
                    return summary
                if record.get("stats_by_time"):
                    continue

                summary["attempted"] += 1
                try:
                    rows = fetch_time_profile(
                        champion,
                        lane=lane,
                        region=region,
                        patch=patch,
                        session=session,
                        headless=headless,
                        solve_cloudflare=solve_cloudflare,
                    )
                    if rows:
                        record["stats_by_time"] = rows
                        summary["fetched"] += 1
                        print(
                            "    时间趋势 OK: "
                            f"{champion}/{lane} ({summary['fetched']}/{summary['attempted']})"
                        )
                    else:
                        print(f"    时间趋势为空: {champion}/{lane}")
                        summary["errors"].append(
                            {
                                "stage": "timeline",
                                "champion": champion,
                                "lane": lane,
                                "error": "未找到 Qwik 时间趋势数据",
                            }
                        )
                except Exception as exc:
                    print(f"    时间趋势失败: {champion}/{lane} - {exc}")
                    summary["errors"].append(
                        {
                            "stage": "timeline",
                            "champion": champion,
                            "lane": lane,
                            "error": str(exc),
                        }
                    )

                if remaining is not None:
                    remaining -= 1
                if delay:
                    time.sleep(delay)

    return summary


def load_time_profile_from_file(path):
    with open(os.path.expanduser(path), "r", encoding="utf-8") as file:
        return parse_time_profile(file.read())
