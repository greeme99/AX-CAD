import { describe, expect, it } from "vitest";
import { categories, filterCostbook, NO_FILTER, type CbItem } from "./costbook";

const items: CbItem[] = [
  { item_code: "P0010-1", item_name: "벽 미장", category: "미장", page_no: 10, reviewer: "김검수" },
  { item_code: "P0011-1", item_name: "바닥 몰탈", spec: "t30", category: "미장", page_no: 11 },
  { item_code: "P0200-2", item_name: "거푸집 설치", category: "콘크리트", page_no: 200 },
  { item_code: "X-NOPAGE", item_name: "쪽 없음" },
];

describe("filterCostbook", () => {
  it("no filter keeps everything", () => expect(filterCostbook(items, NO_FILTER)).toHaveLength(4));
  it("searches code, name and spec, ignoring case and spaces around", () => {
    expect(filterCostbook(items, { ...NO_FILTER, q: " 미장 " }).map((i) => i.item_code)).toEqual(["P0010-1"]);
    expect(filterCostbook(items, { ...NO_FILTER, q: "T30" })).toHaveLength(1);
    expect(filterCostbook(items, { ...NO_FILTER, q: "p0200" })).toHaveLength(1);
  });
  it("filters by 분류 and by 검수 state", () => {
    expect(filterCostbook(items, { ...NO_FILTER, category: "미장" })).toHaveLength(2);
    expect(filterCostbook(items, { ...NO_FILTER, review: "DONE" }).map((i) => i.item_code)).toEqual(["P0010-1"]);
    expect(filterCostbook(items, { ...NO_FILTER, review: "TODO" })).toHaveLength(3);
  });
  it("filters by original page range; an item with no page never matches a range", () => {
    expect(filterCostbook(items, { ...NO_FILTER, pageFrom: 10, pageTo: 11 })).toHaveLength(2);
    expect(filterCostbook(items, { ...NO_FILTER, pageFrom: 100 }).map((i) => i.item_code)).toEqual(["P0200-2"]);
    expect(filterCostbook(items, { ...NO_FILTER, pageTo: 5 })).toHaveLength(0);
  });
  it("lists the distinct 분류", () => expect(categories(items)).toEqual(["미장", "콘크리트"]));
});
