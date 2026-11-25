"""Exercise the actual Flask routes against an isolated, real-source dataset."""
import itertools
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import app as service
from scripts.kr_trial_catalog import identity_name


def check_runtime():
    db = service.get_db()
    if db.get("meta", {}).get("trial_only") is not True or db["meta"]["production_accepted"] is not False:
        raise ValueError("checker only accepts explicitly isolated data")
    catalog = json.loads((Path(service.BASE_DIR) / "data/trial_catalog.json").read_text())
    client = service.app.test_client()
    result = dict(mode="saved_real_source_replay", source_patch=db["meta"]["patch"],
                  production_accepted=False, recommendation_accuracy="NOT TESTED",
                  candidate_positions=0, route_queries=0, recommendation_scenarios=0,
                  relation_values_checked=0, missing_sample_checks=0)

    def get(path):
        response = client.get(path)
        assert response.status_code == 200, (path, response.status_code, response.get_json())
        payload = response.get_json()
        assert payload["ok"] is True
        result["route_queries"] += 1
        return payload

    champions = get("/api/champions")
    for lane in ("bottom", "support"):
        returned = {row["id"]: row for row in champions[lane]}
        assert returned.keys() == db["tiers"][lane].keys()
        for name, row in returned.items():
            assert identity_name(catalog, row["champion_id"]) == name
            assert row["cn_name"] == catalog["champions"][str(row["champion_id"])]["cn_name"]
            assert row["games"] == db["hero_stats"][lane][name]["games"]
            assert (Path(service.BASE_DIR) / row["avatar"].lstrip("/")).is_file(), row["avatar"]
            result["candidate_positions"] += 1
            opposite = "support" if lane == "bottom" else "bottom"
            synergy = get(f"/api/synergy/{name}?lane={lane}&top_n=all")
            assert synergy["lane"] == lane
            expected = {key: value for key, value in db["synergy_by_lane"][lane][name].items()
                        if key in db["tiers"][opposite]}
            assert {row["name"] for row in synergy["results"]} == expected.keys()
            for relation in synergy["results"]:
                raw = expected[relation["name"]]
                assert relation["games"] == raw["games"] >= 100
                assert relation["win_rate"] == round(raw["win_rate"], 2)
                result["relation_values_checked"] += 1
            matchup = get(f"/api/matchup/{name}?lane={lane}&top_n=all")
            for api_key, storage_key in (("vs_adc", "vs_adc"), ("vs_support", "vs_sup")):
                expected = db["counter_by_lane"][lane][name][storage_key]
                assert {row["name"] for row in matchup[api_key]} == expected.keys()
                for relation in matchup[api_key]:
                    raw = expected[relation["name"]]
                    assert relation["games"] == raw["games"] >= 100
                    assert relation["win_rate"] == round(raw["win_rate"], 2)
                    result["relation_values_checked"] += 1
            counter = get(f"/api/counter/{name}?lane={lane}&top_n=all")
            for api_key, candidate_lane in (("adc", "bottom"), ("support", "support")):
                forward_key = "vs_adc" if lane == "bottom" else "vs_sup"
                reverse_key = "vs_adc" if candidate_lane == "bottom" else "vs_sup"
                for relation in counter[api_key]:
                    candidate = relation["name"]
                    forward = db["counter_by_lane"][candidate_lane][candidate][forward_key].get(name)
                    reverse = db["counter_by_lane"][lane][name][reverse_key].get(candidate)
                    assert relation["forward_games"] == (forward["games"] if forward else 0)
                    assert relation["reverse_games"] == (reverse["games"] if reverse else 0)
                    if forward:
                        assert relation["forward_win_rate"] == round(forward["win_rate"], 2)
                    if reverse:
                        assert relation["reverse_win_rate"] == round(100 - reverse["win_rate"], 2)
                    result["relation_values_checked"] += 1
    for role in ("adc", "support"):
        for ally, enemy_ad, enemy_sup in itertools.product((False, True), repeat=3):
            state = dict(ally_ad="ashe" if role == "support" and ally else None,
                         ally_sup="leona" if role == "adc" and ally else None,
                         enemy_ad="jinx" if enemy_ad else None,
                         enemy_sup="lulu" if enemy_sup else None)
            response = client.post("/api/recommend", json=dict(role=role, top_n="all", bp_state=state))
            assert response.status_code == 200, response.get_json()
            payload = response.get_json()
            assert payload["ok"] and payload["results"]
            selected = {name for name in state.values() if name}
            assert not selected.intersection(row["name"] for row in payload["results"])
            for row in payload["results"]:
                lane = "bottom" if role == "adc" else "support"
                partner = state["ally_sup"] if role == "adc" else state["ally_ad"]
                if partner:
                    raw = db["synergy_by_lane"][lane][row["name"]].get(partner)
                    assert row["duo_games"] == (raw["games"] if raw else 0)
                if role == "adc" and row["name"] == "karthus" and (enemy_ad or enemy_sup):
                    raw = db["counter_by_lane"][lane]["karthus"]
                    if not raw["vs_adc"] and not raw["vs_sup"]:
                        assert row["matchup_games"] == 0 and "counter" in row["missing_fields"]
                        result["missing_sample_checks"] += 1
            result["recommendation_scenarios"] += 1
    flex = next(name for name in db["tiers"]["bottom"] if name in db["tiers"]["support"])
    for endpoint in ("synergy", "counter", "matchup"):
        response = client.get(f"/api/{endpoint}/{flex}")
        assert response.status_code == 400 and set(response.get_json()["eligible_lanes"]) == {"bottom", "support"}
    bad = client.post("/api/recommend", json=dict(role="support", bp_state={"ally_ad": "notachamp"}))
    assert bad.status_code == 400
    # A catalog entry alone must never expand the ranked candidate/input pool.
    if "locke" not in db["tiers"]["bottom"]:
        bad = client.post("/api/recommend", json=dict(role="support", bp_state={"enemy_ad": "locke"}))
        assert bad.status_code == 400
    assert service.get_champion_meta()["locke"]["champion_id"] == 805
    assert "洛克" in service.get_champion_meta()["locke"]["search_text"]
    assert (Path(service.BASE_DIR) / "static/avatars/805.png").is_file()
    result["passed"] = True
    return result


if __name__ == "__main__":
    result = check_runtime()
    (Path(service.BASE_DIR) / "runtime-check.json").write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n")
    print(json.dumps(result, ensure_ascii=False))
