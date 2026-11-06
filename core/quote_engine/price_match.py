"""Track 8 P6-7: 품셈 자원(초안의 `L-0001` 이름표) <-> 가져온 단가(`L-1027` 등) 이름 대응.

A resource of the 품셈 draft has a code made while extracting and no price; the imported prices have
their own codes. Here a draft resource without any price gets the prices of the imported row whose
*name* is the same (spaces, brackets and punctuation ignored) and whose kind and unit agree. One
imported row only: two different candidates are listed as ambiguous, never picked. The copies keep
every 기준일 of the match and say in `source` where they came from."""

import re
from typing import Any

_DROP = re.compile(r"[\s()\[\]（）,·.\-_/]+")


def norm(name: str) -> str:
    return _DROP.sub("", name).lower()


def match(
    unpriced: list[dict[str, Any]], priced: list[dict[str, Any]]
) -> tuple[list[dict[str, Any]], dict[str, list[str]]]:
    """-> (new price rows for the draft codes, {'matched': [...], 'ambiguous': [...], 'unmatched': [...]})."""
    by_name: dict[tuple[str, str], dict[str, list[dict[str, Any]]]] = {}
    for p in priced:
        by_name.setdefault((p["resource_type"], norm(p["resource_name"])), {}).setdefault(
            p["resource_code"], []
        ).append(p)
    out: list[dict[str, Any]] = []
    report: dict[str, list[str]] = {"matched": [], "ambiguous": [], "unmatched": []}
    for u in unpriced:
        who = f"{u['resource_code']} {u['resource_name']}"
        cands = by_name.get((u["resource_type"], norm(u["resource_name"])), {})
        cands = {c: rows for c, rows in cands.items() if rows[0]["unit"] == u["unit"]}
        if len(cands) > 1:
            report["ambiguous"].append(f"{who}: {', '.join(sorted(cands))}")
        elif not cands:
            report["unmatched"].append(who)
        else:
            ((code, rows),) = cands.items()
            for r in rows:
                out.append(
                    {
                        "resource_code": u["resource_code"],
                        "resource_type": u["resource_type"],
                        "resource_name": u["resource_name"],
                        "unit": u["unit"],
                        "unit_price": r["unit_price"],
                        "price_date": r["price_date"],
                        "source": f"대응 {code}: {r.get('source') or ''}"[:100],
                    }
                )
            report["matched"].append(f"{who} <- {code}")
    return out, report
