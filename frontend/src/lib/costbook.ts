// 표준품셈 list filtering for the master-data screen (P6-3): thousands of items, so search, filter, page.
export type CbItem = {
  item_code: string;
  item_name: string;
  spec?: string | null;
  category?: string | null;
  page_no?: number | null;
  reviewer?: string | null;
};
export type CbFilter = { q: string; category: string; review: "ALL" | "DONE" | "TODO"; pageFrom?: number; pageTo?: number };

export const NO_FILTER: CbFilter = { q: "", category: "", review: "ALL" };

/** Items matching the text (code, name, spec), 분류, 검수 state and original-page range. */
export function filterCostbook<T extends CbItem>(items: T[], f: CbFilter): T[] {
  const q = f.q.trim().toLowerCase();
  return items.filter((i) => {
    if (q && !`${i.item_code} ${i.item_name} ${i.spec ?? ""}`.toLowerCase().includes(q)) return false;
    if (f.category && i.category !== f.category) return false;
    if (f.review === "DONE" && !i.reviewer) return false;
    if (f.review === "TODO" && i.reviewer) return false;
    if (f.pageFrom != null && (i.page_no == null || i.page_no < f.pageFrom)) return false;
    if (f.pageTo != null && (i.page_no == null || i.page_no > f.pageTo)) return false;
    return true;
  });
}

/** Distinct 분류 values, sorted, for the filter list. */
export const categories = (items: CbItem[]): string[] => [...new Set(items.map((i) => i.category).filter((c): c is string => !!c))].sort();
