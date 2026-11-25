"""Apply reviewable compatibility fixes to a NEW isolated copy, never live files."""
import difflib
from pathlib import Path
import shutil

from scripts.probe_kr_build_source import REPO_ROOT


def replace_once(source, old, new):
    if source.count(old) != 1:
        raise ValueError(f"runtime source changed; refusing speculative patch: {old[:70]!r}")
    return source.replace(old, new, 1)


def patched_sources(root=REPO_ROOT):
    app = (root / "app.py").read_text()
    start = app.index("def load_champion_meta():")
    end = app.index("\ndef get_champion_meta():", start)
    app = app[:start] + '''def load_champion_meta():
    from scripts.kr_trial_catalog import app_metadata
    with open(os.path.join(BASE_DIR, "data", "trial_catalog.json"), encoding="utf-8") as file:
        catalog = json.load(file)
    return app_metadata(catalog, CHAMPION_ALIASES, build_search_tokens)


def query_lane(db, champion):
    requested = request.args.get("lane", "").strip().lower()
    eligible = [lane for lane in ("bottom", "support")
                if db["tiers"].get(lane, {}).get(champion) not in (None, "?")]
    if requested:
        if requested not in eligible:
            return None, api_error("该英雄在所选位置没有可用数据", eligible_lanes=eligible)
        return requested, None
    if len(eligible) != 1:
        return None, api_error("请指定英雄的位置：ADC 或辅助", eligible_lanes=eligible)
    return eligible[0], None

''' + app[end:]
    for function in ("counter", "synergy", "matchup"):
        marker = f"def {function}(champion):"
        start = app.index(marker)
        stop = app.find("\n@app.", start)
        stop = len(app) if stop < 0 else stop
        body = app[start:stop]
        point = '    if not champion:\n        return api_error("champion 不能为空")\n'
        body = replace_once(body, point, point + '''    lane, error = query_lane(db, champion)
    if error is not None:
        return error
''')
        if function == "counter":
            for role in ("adc", "support"):
                body = replace_once(body, f'find_counter_picks(champion, "{role}", db, top_n=top_n)',
                                    f'find_counter_picks(champion, "{role}", db, top_n=top_n, enemy_lane=lane)')
        elif function == "synergy":
            body = replace_once(body, '    for partner, raw_value in db["synergy"].get(champion, {}).items():\n        lane = "bottom" if partner in db["tiers"].get("bottom", {}) else "support"\n        tier = db["tiers"].get(lane, {}).get(partner)\n        if tier == "?":',
                                '    partner_lane = "support" if lane == "bottom" else "bottom"\n    for partner, raw_value in db["synergy_by_lane"][lane].get(champion, {}).items():\n        tier = db["tiers"].get(partner_lane, {}).get(partner)\n        if tier in (None, "?"):')
        else:
            body = replace_once(body, 'data = db["counter"].get(champion, {})',
                                'data = db["counter_by_lane"][lane].get(champion, {})')
        body = replace_once(body, '            "ok": True,', '            "ok": True,\n            "lane": lane,')
        app = app[:start] + body + app[stop:]

    engine = (root / "bp_engine.py").read_text()
    engine = replace_once(engine, '    by_lane = db.get(f"{matrix_key}_by_lane", {})',
                          '    by_lane = db.get(f"{matrix_key}_by_lane", {})\n    if db.get("meta", {}).get("positions_required"):\n        if lane not in ("bottom", "support"):\n            raise ValueError("explicit candidate lane required")\n        return by_lane.get(lane, {})')
    engine = replace_once(engine, 'get_relation_stat(synergy_matrix, partner, candidate)',
                          'synergy_matrix.get(candidate, {}).get(partner)')
    engine = replace_once(engine, 'local_top_games=top_relation_games(synergy_matrix, partner),',
                          'local_top_games=max((int(extract_stat(raw, legacy_games=LEGACY_RELATION_GAMES)["games"])\n                             for raw in synergy_matrix.get(candidate, {}).values()), default=0),')
    engine = replace_once(engine, 'def find_counter_picks(enemy, role, db, top_n=5):',
                          'def find_counter_picks(enemy, role, db, top_n=5, enemy_lane=None):')
    start = engine.index('    enemy_key_for_candidate = (', engine.index('def find_counter_picks'))
    stop = engine.index('\n    results = []', start)
    engine = engine[:start] + '''    if enemy_lane not in ("bottom", "support"):
        raise ValueError("explicit enemy lane required")
    enemy_key_for_candidate = "vs_adc" if enemy_lane == "bottom" else "vs_sup"
    candidate_key_for_enemy = "vs_adc" if role == "adc" else "vs_sup"
    enemy_matchups = get_lane_relation_matrix(db, "counter", enemy_lane).get(enemy, {}).get(candidate_key_for_enemy, {})
''' + engine[stop:]
    engine = replace_once(engine, 'db.get("counter", {}).get(champ, {}).get(enemy_key_for_candidate, {}).get(enemy)',
                          'get_lane_relation_matrix(db, "counter", meta["lane"]).get(champ, {}).get(enemy_key_for_candidate, {}).get(enemy)')

    html = (root / "templates/index.html").read_text()
    html = replace_once(html, '<h1>LoL 下路 BP 助手</h1>',
                        '<h1>LoL 下路 BP 助手</h1>\n      <p>隔离验证：试验数据，未正式接入。</p>')
    html = replace_once(html, '      <h2 class="section-title">最佳搭档</h2>',
                        '      <p id="synergy_status" role="status" aria-live="polite"></p>\n      <h2 class="section-title">最佳搭档</h2>')
    html = replace_once(html, '      <section class="split">',
                        '      <p id="counter_status" role="status" aria-live="polite"></p>\n      <section class="split">')
    for label in ("synergy", "counter"):
        button = f'        <button class="primary" id="query_{label}"'
        select = f'''        <label>英雄位置
          <select id="{label}_lane">
            <option value="">请选择位置</option>
            <option value="bottom">ADC</option>
            <option value="support">辅助</option>
          </select>
        </label>
'''
        html = replace_once(html, button, select + button)
        url = f'      const data = await (await fetch(`/api/{label}/${{champion}}?top_n=all`)).json();'
        html = replace_once(html, url, f'''      const lane = document.querySelector("#{label}_lane").value;
      const data = await (await fetch(`/api/{label}/${{champion}}?top_n=all&lane=${{lane}}`)).json();
      if (!data.ok) {{
        document.querySelector("#{label}_status").textContent = data.error;
        {('latestSynergyRows = []; renderSynergyRows();' if label == 'synergy' else 'renderSimpleRows(document.querySelector("#counter_adc_results"), [], "win_rate", true, true); renderSimpleRows(document.querySelector("#counter_support_results"), [], "win_rate", true, true); document.querySelector("#counter_adc_count").textContent = "0 条"; document.querySelector("#counter_support_count").textContent = "0 条";')}
        return;
      }}
      document.querySelector("#{label}_status").textContent = "";''')
        status = ('      statusEl.textContent = `协同查询：${data.champion.display_name || data.champion.cn_name || champion}，${sortText}`;'
                  if label == "synergy" else '      statusEl.textContent = `反制查询：${champion}`;')
        html = replace_once(html, status, status + f'\n      document.querySelector("#{label}_status").textContent = statusEl.textContent;')
        feedback = f'      document.querySelector("#{label}_status").textContent = statusEl.textContent;'
        empty = "latestSynergyRows.length === 0" if label == "synergy" else "adcRows.length === 0 && supportRows.length === 0"
        noun = "配合" if label == "synergy" else "对位"
        html = replace_once(html, feedback, feedback + f'\n      if ({empty}) document.querySelector("#{label}_status").textContent += "，暂无达到 100 场的{noun}数据。";')
    return {"app.py": app, "bp_engine.py": engine, "templates/index.html": html}


def prepare_runtime(destination, root=REPO_ROOT):
    destination = Path(destination).resolve()
    if destination == REPO_ROOT or REPO_ROOT in destination.parents:
        raise ValueError("runtime copy must be outside the repository")
    destination.mkdir(parents=True, exist_ok=False)
    # An allowlist prevents accidentally copying credentials, git metadata,
    # production data or active cloud/deployment workflows into the trial.
    for name in ("champion_aliases.py", "champion_tags.py", "test_api.py"):
        shutil.copyfile(root / name, destination / name)
    for name in ("scripts", "scraper", "templates", "tests"):
        shutil.copytree(root / name, destination / name, ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
    # Copy assets so new official portraits can be added inside the trial
    # without any write path back to production static files.
    shutil.copytree(root / "static", destination / "static")
    (destination / "data").mkdir()
    patch = []
    for name, source in patched_sources(root).items():
        (destination / name).write_text(source)
        patch.extend(difflib.unified_diff((root / name).read_text().splitlines(True), source.splitlines(True),
                                        fromfile="a/" + name, tofile="b/" + name))
    (destination / "runtime.patch").write_text("".join(patch))
    return destination
