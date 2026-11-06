#!/usr/bin/env python3
"""index.html 안의 생산·정지 데이터(var D=...)를 읽어 부문포털용 읽기 전용 JSON(api/)을 만듭니다.

사용: python3 tools/build_api.py [index.html] [출력 폴더]
  기본값: index.html, 출력 폴더 _site (index.html 도 함께 복사)

만드는 파일
  api/meta.json                      호기·데이터 기준일·월 목록·필드 설명
  api/kpi/latest.json                가장 최근 달 KPI
  api/kpi/YYYY-MM.json               월별 호기 KPI (생산량·생산시간·정지·정지비율·가동률)
  api/data/production/YYYY.json      일자·호기·제품별 생산 실적
  api/data/stops/YYYY.json           정지 이력
"""
import json
import os
import re
import shutil
import sys
from collections import defaultdict
from datetime import datetime, timezone

MC = ["A", "B", "C", "D", "E", "F"]
# 페이지와 같은 규칙: 전처리·제품적재로봇 정지는 가동률 계산에서 제외
EXRE = re.compile(r"전처리|제품적[재잭]로봇|^로봇트?$")


def is_ex(name):
    return bool(EXRE.search(re.sub(r"\s+", "", str(name or ""))))


def load_data(path):
    s = open(path, encoding="utf-8").read()
    m = re.search(r"var D=(\{.*?\});\n", s, re.S)
    if not m:
        sys.exit("index.html 에서 데이터(var D=...)를 찾지 못했습니다.")
    return json.loads(m.group(1))


def iso(d6):
    d = "20" + str(d6)
    return f"{d[:4]}-{d[4:6]}-{d[6:8]}"


def r1(x):
    return round(x, 1)


def kpi_block(o):
    run, s, x = o["run_min"], o["stop_min"], o["excluded_stop_min"]
    return {
        **o,
        "stop_ratio_pct": r1(s / run * 100) if run > 0 else None,
        "availability_pct": r1(max(0, run - max(0, s - x)) / run * 100) if run > 0 else None,
        "defect": None,
    }


def write(path, obj):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False, separators=(",", ":"))


def main():
    src = sys.argv[1] if len(sys.argv) > 1 else "index.html"
    out = sys.argv[2] if len(sys.argv) > 2 else "_site"
    D = load_data(src)
    items, equip = D["I"], D["Q"]

    prod = [
        {"date": iso(r[0]), "machine": MC[r[1]], "item": items[r[2]], "qty": r[3],
         "run_min": r[4], "stop_count": r[5], "stop_min": r[6]}
        for r in D["P"]
    ]
    stops = [
        {"date": iso(r[0]), "machine": MC[r[1]], "equipment": equip[r[2]], "location": r[3],
         "min": r[4], "problem": r[5], "action": r[6], "start": r[7], "end": (r[8] if len(r) > 8 else "") or ""}
        for r in D["S"]
    ]
    last = max(p["date"] for p in prod)
    now = datetime.now(timezone.utc).replace(microsecond=0).isoformat()

    # 월 × 호기 집계
    blank = lambda: {"production_qty": 0, "run_min": 0, "stop_min": 0, "stop_count": 0,
                     "excluded_stop_min": 0, "_days": set(), "_items": set()}
    agg = defaultdict(lambda: defaultdict(blank))
    for p in prod:
        for key in (p["machine"], "ALL"):
            o = agg[p["date"][:7]][key]
            o["production_qty"] += p["qty"]; o["run_min"] += p["run_min"]
            o["stop_min"] += p["stop_min"]; o["stop_count"] += p["stop_count"]
            o["_days"].add(p["date"]); o["_items"].add(p["item"])
    for st in stops:
        if is_ex(st["equipment"]):
            for key in (st["machine"], "ALL"):
                agg[st["date"][:7]][key]["excluded_stop_min"] += st["min"] or 0

    definitions = {
        "production_qty": "생산량(개)",
        "run_min": "생산시간(분) = 생산 시작~종료",
        "stop_min": "정지시간(분)",
        "stop_count": "정지 건수",
        "excluded_stop_min": "가동률 계산에서 제외한 정지(분): 전처리·제품적재로봇",
        "stop_ratio_pct": "정지 비율(%) = 정지시간 ÷ 생산시간",
        "availability_pct": "가동률(%) = (생산시간 − (정지시간 − 제외 정지)) ÷ 생산시간",
        "defect": "불량: 포장팀 포털에 자료 없음(null)",
        "production_days": "생산한 날 수",
        "item_count": "생산 제품 종류 수",
    }

    months = sorted(agg)
    for mo in months:
        def fin(key):
            o = dict(agg[mo][key])
            o["production_days"] = len(o.pop("_days")); o["item_count"] = len(o.pop("_items"))
            return kpi_block(o)
        doc = {
            "team": "상온제품포장팀", "month": mo, "data_until": last, "generated_at": now,
            "total": fin("ALL"),
            "machines": [{"machine": m, "name": m + "호기", **fin(m)} for m in MC if m in agg[mo]],
            "definitions": definitions,
        }
        write(f"{out}/api/kpi/{mo}.json", doc)
    write(f"{out}/api/kpi/latest.json", json.loads(open(f"{out}/api/kpi/{months[-1]}.json", encoding="utf-8").read()))

    years = sorted({p["date"][:4] for p in prod} | {s["date"][:4] for s in stops})
    for y in years:
        write(f"{out}/api/data/production/{y}.json",
              {"year": y, "data_until": last, "rows": [p for p in prod if p["date"][:4] == y]})
        write(f"{out}/api/data/stops/{y}.json",
              {"year": y, "data_until": last, "rows": [s for s in stops if s["date"][:4] == y]})

    write(f"{out}/api/meta.json", {
        "team": "상온제품포장팀",
        "machines": [{"machine": m, "name": m + "호기"} for m in MC],
        "data_until": last, "generated_at": now,
        "months": months, "years": years,
        "endpoints": {
            "kpi_latest": "api/kpi/latest.json",
            "kpi_month": "api/kpi/{YYYY-MM}.json",
            "production": "api/data/production/{YYYY}.json",
            "stops": "api/data/stops/{YYYY}.json",
        },
        "definitions": definitions,
        "fields": {
            "production": "date, machine, item, qty, run_min, stop_count, stop_min",
            "stops": "date, machine, equipment, location, min, problem, action, start, end",
        },
    })

    if os.path.abspath(src) != os.path.abspath(os.path.join(out, "index.html")):
        shutil.copyfile(src, os.path.join(out, "index.html"))
    print(f"완료: {len(months)}개월 KPI, {len(prod)}건 생산, {len(stops)}건 정지 → {out}/api (기준일 {last})")


if __name__ == "__main__":
    main()
