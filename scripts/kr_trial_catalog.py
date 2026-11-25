"""Numeric-ID champion identity for isolated trials; no production map updates."""
import re

from champion_aliases import CHAMPION_ALIASES
from scraper.lolalytics_build_probe import SourceValidationError


def build_catalog(english, chinese, legacy_mapping, english_names, chinese_names):
    """Join official locales by Riot ID, and legacy labels by explicit English key."""
    if not all(isinstance(payload, dict) and isinstance(payload.get("data"), dict) for payload in (english, chinese)):
        raise SourceValidationError("invalid official champion catalog schema")
    if english.get("version") != chinese.get("version") or not re.fullmatch(
            r"\d+\.\d+\.\d+", str(english.get("version", ""))):
        raise SourceValidationError("official champion versions differ or are invalid")
    locales = []
    for payload in (english, chinese):
        entries = {}
        for row in payload.get("data", {}).values():
            if not isinstance(row, dict):
                raise SourceValidationError("invalid official champion row")
            cid = row.get("key")
            key = str(row.get("id", "")).lower()
            if (not str(cid).isdigit() or int(cid) <= 0 or int(cid) in entries
                    or not re.fullmatch(r"[a-z]+", key)
                    or any(not isinstance(row.get(field), str) or not row[field].strip() for field in ("name", "title"))):
                raise SourceValidationError("invalid or duplicate official champion identity")
            entries[int(cid)] = dict(key=key, asset_id=row["id"], name=row["name"], title=row["title"])
        if not entries:
            raise SourceValidationError("empty official champion catalog")
        locales.append(entries)
    en, cn = locales
    if en.keys() != cn.keys():
        raise SourceValidationError("official locale identities differ")
    names = [name.strip().lower() for name in english_names if name.strip()]
    labels = [name.strip() for name in chinese_names if name.strip()]
    if len(names) != len(labels) or len(names) != len(set(names)) or set(names) != set(legacy_mapping):
        raise SourceValidationError("legacy name lists are truncated or identities differ")
    legacy = dict(zip(names, labels))
    if len(set(legacy_mapping.values())) != len(legacy_mapping):
        raise SourceValidationError("duplicate legacy numeric identity")
    for key, cid in legacy_mapping.items():
        if cid not in en or en[cid]["key"] != key:
            raise SourceValidationError(f"legacy identity conflicts with official catalog: {key}/{cid}")
        if legacy[key] not in {cn[cid]["name"], cn[cid]["title"], *CHAMPION_ALIASES.get(key, [])}:
            raise SourceValidationError(f"legacy Chinese label conflicts with independently identified champion: {key}/{cid}")
    result, seen = {}, set()
    for cid, row in en.items():
        if cn[cid]["key"] != row["key"] or row["key"] in seen:
            raise SourceValidationError("official canonical identity conflict")
        seen.add(row["key"])
        result[str(cid)] = dict(champion_id=cid, key=row["key"], asset_id=row["asset_id"], name=row["name"],
                               cn_name=legacy.get(row["key"], cn[cid]["name"]),
                               localized_name=cn[cid]["name"], localized_title=cn[cid]["title"])
    return dict(version=english["version"], champions=result, production_accepted=False)


def identity_name(catalog, cid):
    row = catalog["champions"].get(str(cid))
    if row is None:
        raise SourceValidationError(f"unmapped official champion id: {cid}")
    return row["key"]


def app_metadata(catalog, aliases, token_builder):
    result = {}
    for row in catalog["champions"].values():
        key, cid = row["key"], row["champion_id"]
        names = list(dict.fromkeys([*aliases.get(key, []), row["name"], row["localized_name"], row["localized_title"]]))
        tokens = token_builder(row["cn_name"], key, cid, names)
        result[key] = dict(id=key, name=row["name"], cn_name=row["cn_name"], aliases=names,
                           champion_id=cid, avatar=f"/static/avatars/{cid}.png",
                           search_tokens=tokens, search_text=" ".join(t["value"] for t in tokens).lower())
    return result
