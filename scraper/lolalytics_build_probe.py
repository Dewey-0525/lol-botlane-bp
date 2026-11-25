"""Isolated KR build-page experiment. Not used by the production collector.

Only the observed Qwik reference format is supported. Unexpected scope, schema,
or population changes fail explicitly; there is no region or snapshot fallback.
"""
from dataclasses import dataclass
from decimal import Decimal
import json
import math
import re
from urllib.parse import urlencode


class SourceValidationError(ValueError):
    pass


@dataclass(frozen=True)
class BuildScope:
    champion: str
    champion_id: int
    lane: str
    patch: str

    def __post_init__(self):
        if not re.fullmatch(r"[a-z]+", self.champion):
            raise ValueError("champion must be a canonical lowercase slug")
        if type(self.champion_id) is not int or self.champion_id <= 0:
            raise ValueError("champion_id must be a positive integer")
        if self.lane not in ("bottom", "support"):
            raise ValueError("lane must be bottom or support")
        if not re.fullmatch(r"\d+\.\d+", self.patch):
            raise ValueError("an explicit numbered patch is required")

    @property
    def slug(self):
        return "wukong" if self.champion == "monkeyking" else self.champion

    def conditions(self):
        return dict(champion=self.slug, champion_id=self.champion_id,
                    lane=self.lane, patch=self.patch, region="kr",
                    tier="emerald_plus", queue="ranked")

    def build_url(self):
        # The page's default tier/queue must be confirmed in its returned data.
        query = urlencode(dict(lane=self.lane, region="kr", patch=self.patch))
        return f"https://lolalytics.com/lol/{self.slug}/build/?{query}"

    def team_url(self):
        query = urlencode(dict(ep="build-team", v="1", patch=self.patch,
                               c=self.champion, lane=self.lane,
                               tier="emerald_plus", queue="ranked", region="kr"))
        return f"https://a1.lolalytics.com/mega/?{query}"


def _number(value, label, low=0, high=None, integer=False):
    if (isinstance(value, bool) or not isinstance(value, (int, float))
            or not math.isfinite(value) or value < low
            or (high is not None and value > high)
            or (integer and value != int(value))):
        raise SourceValidationError(f"invalid {label}: {value!r}")
    return int(value) if integer else value


class _References:
    def __init__(self, objects):
        self.objects = objects
        self.active = set()
        self.cache = {}

    def read(self, reference):
        if not isinstance(reference, str) or not re.fullmatch(r"[0-9a-z]+", reference):
            raise SourceValidationError("unsupported Qwik reference")
        index = int(reference, 36)
        if index >= len(self.objects) or index in self.active or len(self.active) >= 64:
            raise SourceValidationError("invalid or cyclic Qwik reference")
        if index in self.cache:
            return self.cache[index]
        self.active.add(index)
        try:
            value = self.objects[index]
            if isinstance(value, dict):
                value = {key: self.read(ref) for key, ref in value.items()}
            elif isinstance(value, list):
                value = [self.read(ref) for ref in value]
            self.cache[index] = value
            return value
        finally:
            self.active.remove(index)

    def fields(self, obj, keys):
        return {key: self.read(obj[key]) for key in keys if key in obj}


def _timeline(sidebar, base_games, base_wr):
    container = sidebar.get("time") if isinstance(sidebar, dict) else None
    if container is None:
        return None
    if (not isinstance(container, dict)
            or not isinstance(container.get("time"), dict)
            or not isinstance(container.get("timeWin"), dict)):
        raise SourceValidationError("unsupported timeline schema")
    games, wins = container["time"], container["timeWin"]
    expected = {str(i) for i in range(1, 8)}
    if not set(games).issubset(expected) or not set(wins).issubset(games):
        raise SourceValidationError("unexpected timeline bucket keys")
    for key in games:
        _number(games[key], f"timeline games[{key}]", integer=True)
        if key in wins:
            _number(wins[key], f"timeline wins[{key}]", high=games[key], integer=True)
    if sum(games.values()) != base_games:
        raise SourceValidationError("timeline total differs from same-response base")
    missing_wins = [key for key in games if key not in wins and games[key] > 0]
    if missing_wins:
        # Include exact ties in the displayed two-decimal rate interval.
        # Zero is proven only if one additional win is outside that interval.
        # Cross-multiply to avoid division rounding near the boundary.
        known = sum(wins.values())
        actual = Decimal(100) * known
        reported = Decimal(str(base_wr)) * base_games
        half_unit = Decimal("0.005") * base_games
        if not (abs(actual - reported) <= half_unit and actual + 100 - reported > half_unit):
            raise SourceValidationError("omitted timeline wins cannot be proven zero")
    groups = [("0-20", ("1", "2")), ("20-25", ("3",)),
              ("25-30", ("4",)), ("30-35", ("5",)), ("35+", ("6", "7"))]
    # The reported buckets already account for every game in this response.
    # An omitted known bucket therefore has no games, rather than unknown games.
    # Preserve the omission explicitly; never infer zero if totals do not match.
    result = []
    for label, keys in groups:
        observed = [key for key in keys if key in games]
        row = dict(label=label, games=sum(games[k] for k in observed),
                   wins=sum(wins[k] for k in observed if k in wins))
        omitted = [key for key in keys if key not in games]
        if omitted:
            row["source_omitted_zero_game_buckets"] = omitted
        omitted_wins = [key for key in keys if key in missing_wins]
        if omitted_wins:
            row["source_omitted_zero_win_buckets"] = omitted_wins
        result.append(row)
    return result


def parse_build_page(source, expected):
    matches = re.findall(
        r'<script\b[^>]*\btype\s*=\s*["\']qwik/json["\'][^>]*>(.*?)</script\s*>',
        source, flags=re.S | re.I,
    )
    if len(matches) != 1:
        raise SourceValidationError("expected exactly one Qwik JSON script")
    try:
        # Script content is raw text. html.unescape would corrupt '&region=kr'.
        payload = json.loads(matches[0])
    except (TypeError, json.JSONDecodeError) as exc:
        raise SourceValidationError("invalid Qwik JSON") from exc
    objects = payload.get("objs") if isinstance(payload, dict) else None
    if not isinstance(objects, list):
        raise SourceValidationError("missing Qwik object table")
    refs = _References(objects)
    nav_keys = ("page", "champion", "cid", "lane", "patch", "tier", "region", "mode")
    navs = [refs.fields(obj, nav_keys) for obj in objects
            if isinstance(obj, dict) and all(key in obj for key in nav_keys)]
    required_nav = dict(page="build", champion=expected.slug, cid=expected.champion_id,
                        lane=expected.lane, patch=expected.patch,
                        tier="emerald_plus", region="kr", mode="ranked")
    if len(navs) != 1 or navs[0] != required_nav:
        raise SourceValidationError(f"page scope mismatch or ambiguity: {navs!r}")
    targets = [obj for obj in objects if isinstance(obj, dict)
               and all(key in obj for key in ("header", "analysed", "avgWr", "enemy"))]
    if len(targets) != 1:
        raise SourceValidationError("missing or ambiguous build data object")
    raw = targets[0]
    # Decode only relevant fields; unrelated UI/runtime objects may be cyclic.
    data = refs.fields(raw, ("header", "analysed", "avgWr", "enemy_h", "enemy", "response"))
    header = data.get("header")
    if not isinstance(header, dict):
        raise SourceValidationError("missing build header")
    required_header = dict(cid=expected.champion_id, lane=expected.lane,
                           patch=expected.patch, queue=420)
    if any(header.get(key) != value for key, value in required_header.items()):
        raise SourceValidationError("build header scope mismatch")
    if not isinstance(data.get("response"), dict) or data["response"].get("valid") is not True:
        raise SourceValidationError("build response is not valid")
    n = _number(header.get("n"), "base games", low=1, integer=True)
    wr = _number(header.get("wr"), "base win rate", high=100)
    analysed = _number(data.get("analysed"), "analysed", low=n, integer=True)
    avg_wr = _number(data.get("avgWr"), "rank average win rate", high=100)
    if data.get("enemy_h") != ["id", "wr", "d1", "d2", "pr", "n"]:
        raise SourceValidationError("unexpected counter column schema")
    enemy = data.get("enemy")
    if not isinstance(enemy, dict):
        raise SourceValidationError("missing enemy position data")
    counter = {}
    for lane in ("bottom", "support"):
        counter[lane] = _relations(enemy.get(lane), f"enemy.{lane}", n)
        if sum(row["games"] for row in counter[lane]) != n:
            raise SourceValidationError(f"enemy.{lane} total differs from same-response base")
    sidebar = {}
    if "sidebar" in raw:
        sidebar_ref = raw["sidebar"]
        if not isinstance(sidebar_ref, str) or not re.fullmatch(r"[0-9a-z]+", sidebar_ref):
            raise SourceValidationError("unsupported sidebar reference")
        index = int(sidebar_ref, 36)
        if index >= len(objects) or not isinstance(objects[index], dict):
            raise SourceValidationError("invalid sidebar")
        sidebar = refs.fields(objects[index], ("time",))
    timeline = _timeline(sidebar, n, wr)
    return dict(
        requested_scope=expected.conditions(), returned_page_scope=navs[0],
        returned_header_scope={key: header[key] for key in required_header},
        hero_stats=dict(win_rate=wr, games=n, stats_by_time=timeline),
        counter_by_enemy_lane=counter,
        population=dict(analysed=analysed, rank_avg_win_rate=avg_wr),
        timeline_status="present" if timeline is not None else "missing",
        source="lolalytics_build_qwik_trial", production_accepted=False,
    )


def _relations(rows, label, max_games=None):
    if not isinstance(rows, list) or not rows:
        raise SourceValidationError(f"missing {label} relations")
    result, seen = [], set()
    for row in rows:
        if not isinstance(row, list) or len(row) != 6:
            raise SourceValidationError(f"unexpected {label} row schema")
        cid = _number(row[0], f"{label} champion id", low=1, integer=True)
        if cid in seen:
            raise SourceValidationError(f"duplicate {label} champion id: {cid}")
        seen.add(cid)
        wr = _number(row[1], f"{label} win rate", high=100)
        games = _number(row[5], f"{label} games", high=max_games, integer=True)
        result.append(dict(champion_id=cid, win_rate=wr, games=games))
    return result


def compare_team_payload(payload, bundle, scope):
    """Report consistency, without inventing a drift allowance or scope echo."""
    if bundle.get("requested_scope") != scope.conditions():
        raise SourceValidationError("team request and build bundle scopes differ")
    if (not isinstance(payload, dict) or not isinstance(payload.get("response"), dict)
            or payload["response"].get("valid") is not True):
        raise SourceValidationError("invalid team response")
    if payload.get("team_h") != ["id", "wr", "d1", "d2", "pr", "n"]:
        raise SourceValidationError("unexpected team column schema")
    lane = "bottom" if scope.lane == "support" else "support"
    if not isinstance(payload.get("team"), dict):
        raise SourceValidationError("missing team position data")
    rows = _relations(payload["team"].get(lane), f"team.{lane}")
    total = sum(row["games"] for row in rows)
    base = bundle["hero_stats"]["games"]
    return dict(partner_lane=lane, requested_scope=scope.conditions(),
                returned_scope_status="not_echoed_by_endpoint",
                relation_rows=rows, sum_games=total, build_base_games=base,
                difference_games=total-base, population_matches=(total == base),
                status="population_matches_scope_not_echoed" if total == base else "population_mismatch",
                production_accepted=False)
