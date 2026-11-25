"""Build an evidence-backed, position-specific dataset for isolated runtime checks."""
import hashlib
import json
from decimal import Decimal, ROUND_CEILING, ROUND_FLOOR
from pathlib import Path

from scraper.lolalytics import TIER_KEY_LABELS
from scraper.lolalytics_build_probe import BuildScope, SourceValidationError, parse_build_page, compare_team_payload
from scripts.kr_trial_catalog import identity_name


def read_json(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def verified_body(directory, name, metadata):
    body = (directory / name).read_bytes()
    if metadata.get("http_status") != 200 or hashlib.sha256(body).hexdigest() != metadata.get("sha256"):
        raise SourceValidationError(f"raw evidence failed verification: {directory / name}")
    return body


def win_interval(games, rate):
    # Source rates have two decimal places. Include both rounding boundaries;
    # this is displayed precision, not a tolerance for different populations.
    n, wr, half = Decimal(games), Decimal(str(rate)), Decimal("0.005")
    low = max(0, int((n*(wr-half)/100).to_integral_value(rounding=ROUND_CEILING)))
    high = min(games, int((n*(wr+half)/100).to_integral_value(rounding=ROUND_FLOOR)))
    if low > high:
        raise SourceValidationError("source displayed rate cannot represent integer wins")
    return low, high


def validate_win_arithmetic(bundle, team):
    base = bundle["hero_stats"]
    base_low, base_high = win_interval(base["games"], base["win_rate"])
    if base["stats_by_time"] is not None:
        exact = sum(row["wins"] for row in base["stats_by_time"])
        if not base_low <= exact <= base_high:
            raise SourceValidationError("base displayed rate differs from exact timeline wins")
        base_low = base_high = exact
    checked = 1
    for rows in [*bundle["counter_by_enemy_lane"].values(), team["relation_rows"]]:
        intervals = [win_interval(row["games"], row["win_rate"]) for row in rows]
        if sum(pair[0] for pair in intervals) > base_high or sum(pair[1] for pair in intervals) < base_low:
            raise SourceValidationError("position partition wins cannot match same-page base")
        checked += len(rows)
    return checked


def build_dataset(trial, round_number, catalog):
    trial = Path(trial)
    manifest = read_json(trial / "manifest.json")
    report = read_json(trial / f"round-{round_number}" / "report.json")
    patch = manifest["patch"]
    if (manifest.get("region") != "kr" or manifest.get("tier") != "emerald_plus"
            or manifest.get("queue") != "ranked" or not catalog["version"].startswith(patch + ".")):
        raise SourceValidationError("manifest or identity patch scope mismatch")
    expected = {(row["champion_id"], row["lane"]): row for row in manifest["candidates"]}
    if len(expected) != len(manifest["candidates"]) or not expected:
        raise SourceValidationError("duplicate or empty candidate manifest")
    if not report.get("completed_at_utc"):
        raise SourceValidationError("incomplete round")
    db = {key: {lane: {} for lane in ("bottom", "support")}
          for key in ("tiers", "hero_stats", "synergy_by_lane", "counter_by_lane")}
    # Deliberately leave ambiguous position-free structures empty. Trial readers
    # must consume the explicit positions; no completion-order merge is allowed.
    db.update(synergy={}, counter={})
    audit = dict(raw_responses_verified=0, case_count=0, source_rate_rows_checked=0, relations={}, unknown_ids=[],
                 retained_below_100_in_raw=True, production_accepted=False)
    seen = set()
    legal = {lane: {cid for cid, pos in expected if pos == lane} for lane in ("bottom", "support")}
    for case in report["cases"]:
        requested = case["requested_scope"]
        key = (requested["champion_id"], requested["lane"])
        if key in seen or key not in expected or case["status"] != "population_matches_scope_not_echoed":
            raise SourceValidationError("duplicate, unexpected, or failed trial case")
        seen.add(key)
        row = expected[key]
        name = identity_name(catalog, key[0])
        if row["champion"] != name or row["games"] < 100 or row["tier_key"] not in TIER_KEY_LABELS:
            raise SourceValidationError("candidate identity, threshold, or tier mismatch")
        lane = key[1]
        scope = BuildScope(name, key[0], lane, patch)
        if requested != scope.conditions():
            raise SourceValidationError("case request scope mismatch")
        directory = trial / f"round-{round_number}" / f"{name}_{lane}"
        # Retry evidence has its own immutable directory, never silently use
        # the initially mismatched files after a successful recorded retry.
        if case.get("previous_attempt") is not None:
            directory = directory.parent / "after-cache-expiry" / f"{name}_{lane}"
        for label, url in (("build", scope.build_url()), ("team", scope.team_url())):
            if case["sources"][label].get("url") != url or case["sources"][label].get("final_url") != url:
                raise SourceValidationError("request URL or redirect scope mismatch")
        bundle = parse_build_page(verified_body(directory, "build.html", case["sources"]["build"]).decode(), scope)
        if bundle["hero_stats"]["games"] < 100:
            raise SourceValidationError("same-page candidate base is below the existing 100-game threshold")
        team = compare_team_payload(json.loads(verified_body(directory, "team.json", case["sources"]["team"])), bundle, scope)
        if not team["population_matches"]:
            raise SourceValidationError("team population mismatch")
        audit["source_rate_rows_checked"] += validate_win_arithmetic(bundle, team)
        if bundle != read_json(directory / "bundle.json") or team != read_json(directory / "team_comparison.json"):
            raise SourceValidationError("stored derived data differs from raw replay")
        audit["raw_responses_verified"] += 2
        db["tiers"][lane][name] = TIER_KEY_LABELS[row["tier_key"]]
        db["hero_stats"][lane][name] = bundle["hero_stats"]
        db["counter_by_lane"][lane][name] = {}
        for enemy_lane, rows in bundle["counter_by_enemy_lane"].items():
            field = "vs_adc" if enemy_lane == "bottom" else "vs_sup"
            db["counter_by_lane"][lane][name][field] = _export_rows(rows, legal[enemy_lane], catalog, audit, f"{lane}.{field}")
        db["synergy_by_lane"][lane][name] = _export_rows(
            team["relation_rows"], None, catalog, audit, f"{lane}.synergy")
        audit["case_count"] += 1
    if seen != expected.keys():
        raise SourceValidationError("missing full-pool trial cases")
    db["meta"] = dict(update_time=report["completed_at_utc"], patch=patch, region="kr",
                      tier="emerald_plus", queue="ranked", source="KR isolated build trial",
                      production_accepted=False, trial_only=True, min_relation_games=100,
                      team_scope_status="not_echoed_by_endpoint", positions_required=True,
                      identity_version=catalog["version"], fetch_errors_count=0)
    audit["manifest_sha256"] = hashlib.sha256((trial / "manifest.json").read_bytes()).hexdigest()
    return db, audit


def _export_rows(rows, legal_ids, catalog, audit, label):
    counts = audit["relations"].setdefault(label, dict(exported=0, below_100=0, ineligible_position=0))
    output = {}
    for row in rows:
        # Resolve every identity, even below threshold; do not hide missing IDs.
        name = identity_name(catalog, row["champion_id"])
        if row["games"] < 100:
            counts["below_100"] += 1
        elif legal_ids is not None and row["champion_id"] not in legal_ids:
            counts["ineligible_position"] += 1
        else:
            output[name] = dict(win_rate=row["win_rate"], games=row["games"])
            counts["exported"] += 1
    return output


def existing_admission_checks(db):
    """Apply existing production coverage floors to position-specific trial data."""
    from update_data import (MIN_BOTTOM_CHAMPIONS, MIN_SUPPORT_CHAMPIONS,
                             MIN_HERO_STATS_RATIO, MIN_SYNERGY_ROWS,
                             MIN_COUNTER_ADC_ROWS, MIN_COUNTER_SUP_ROWS)
    counts = {lane: len(db["tiers"][lane]) for lane in ("bottom", "support")}
    # The existing floors counted the old flat matrices. Preserve that same
    # unique directed-pair denominator without actually merging any statistics.
    synergy = len({(source, target) for records in db["synergy_by_lane"].values()
                   for source, rows in records.items() for target in rows})
    adc = len({(source, target) for records in db["counter_by_lane"].values()
               for source, rows in records.items() for target in rows["vs_adc"]})
    sup = len({(source, target) for records in db["counter_by_lane"].values()
               for source, rows in records.items() for target in rows["vs_sup"]})
    checks = dict(bottom_candidates=counts["bottom"] >= MIN_BOTTOM_CHAMPIONS,
                  support_candidates=counts["support"] >= MIN_SUPPORT_CHAMPIONS,
                  hero_stats_coverage=all(counts[lane] and len(db["hero_stats"][lane])/counts[lane] >= MIN_HERO_STATS_RATIO for lane in counts),
                  synergy_rows=synergy >= MIN_SYNERGY_ROWS,
                  counter_adc_rows=adc >= MIN_COUNTER_ADC_ROWS,
                  counter_support_rows=sup >= MIN_COUNTER_SUP_ROWS,
                  no_fetch_errors=db["meta"]["fetch_errors_count"] == 0)
    return dict(checks=checks, passed=all(checks.values()),
                counts=dict(**counts, synergy_rows=synergy,counter_adc_rows=adc,counter_support_rows=sup),
                thresholds=dict(bottom=MIN_BOTTOM_CHAMPIONS,support=MIN_SUPPORT_CHAMPIONS,
                                hero_stats_ratio=MIN_HERO_STATS_RATIO,synergy_rows=MIN_SYNERGY_ROWS,
                                counter_adc_rows=MIN_COUNTER_ADC_ROWS,counter_support_rows=MIN_COUNTER_SUP_ROWS),
                row_count_definition="unique directed hero pairs as in legacy coverage floors; position statistics remain separate",
                publication="DISABLED; cloud and formal integration are separate gates")
