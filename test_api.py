#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
基础 API 回归测试。

运行:
  python3 test_api.py
"""

from app import app


def assert_ok(response, path):
    assert response.status_code == 200, f"{path} status={response.status_code}"
    data = response.get_json()
    assert data and data.get("ok") is True, f"{path} returned not ok: {data}"
    return data


def test_health(client):
    data = assert_ok(client.get("/api/health"), "/api/health")
    assert data["service"] == "lol-botlane-bp"
    assert "update_time" in data["data_meta"]


def test_champions(client):
    data = assert_ok(client.get("/api/champions"), "/api/champions")
    assert data["bottom"], "bottom champion list is empty"
    assert data["support"], "support champion list is empty"
    assert all(champion["tier"] != "?" for champion in data["bottom"])
    assert any("日女" in champion["search_text"] for champion in data["support"])


def test_recommend(client):
    payload = {
        "role": "support",
        "top_n": 5,
        "bp_state": {
            "ally_ad": "lucian",
            "ally_sup": None,
            "enemy_ad": "jinx",
            "enemy_sup": "leona",
        },
    }
    data = assert_ok(client.post("/api/recommend", json=payload), "/api/recommend")
    assert data["state"] == "S8"
    assert data["state_info"]["label"] == "下路信息完整"
    assert len(data["results"]) == 5
    names = {row["name"] for row in data["results"]}
    assert not names.intersection({"lucian", "jinx", "leona"})
    first = data["results"][0]
    for key in ["display_name", "avatar", "evidence_label", "confidence_label"]:
        assert key in first, f"missing {key}"
    for key in [
        "final_rating",
        "display_winrate",
        "base_rating",
        "synergy_bonus",
        "counter_bonus",
        "duo_games",
        "matchup_games",
        "explanation",
    ]:
        assert key in first, f"missing {key}"


def test_side_features(client):
    assert_ok(client.get("/api/synergy/jinx?top_n=3"), "/api/synergy/jinx")
    assert_ok(client.get("/api/counter/jinx?top_n=3"), "/api/counter/jinx")
    data = assert_ok(client.get("/api/tier/support"), "/api/tier/support")
    assert data["results"], "tier results are empty"


def main():
    client = app.test_client()
    test_health(client)
    test_champions(client)
    test_recommend(client)
    test_side_features(client)
    print("OK: API smoke tests passed")


if __name__ == "__main__":
    main()
