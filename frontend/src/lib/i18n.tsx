"use client";

import { createContext, useCallback, useContext, useState, type ReactNode } from "react";

export type Locale = "ko" | "en";

type Dict = Record<string, string>;

const ko: Dict = {
  // common
  "common.close": "닫기",
  "common.cancel": "취소",
  "common.ok": "확인",
  "common.save": "저장",
  "common.delete": "삭제",
  "common.create": "만들기",
  "common.edit": "편집",
  "common.search": "검색",
  "common.loading": "로딩 중...",
  "common.error": "오류",
  "common.unit_mm": "mm",
  // nav
  "nav.projects": "프로젝트",
  "nav.documents": "도면 목록",
  "nav.model": "모델",
  "nav.draft": "제도",
  "nav.quote": "견적",
  "nav.admin": "관리",
  // model workbench
  "model.sketch": "스케치",
  "model.extrude": "돌출",
  "model.revolve": "회전",
  "model.primitive": "원시 형상",
  "model.standard": "규격품",
  "model.boolean": "Boolean",
  "model.machining": "가공 Feature",
  "model.sheet": "판금",
  "model.plane_select": "평면이나 부품 면에 구속 스케치를 그립니다",
  "model.select_feature": "Feature를 선택하세요.",
  "model.measure": "계측",
  "model.no_measure": "계측값이 없습니다.",
  "model.volume": "부피",
  "model.surface_area": "표면적",
  // feature tree / timeline
  "feature.suppressed": "억제",
  "feature.error": "오류",
  "feature.rollback_hint": "롤백 위치 (클릭: 끝으로 이동)",
  "feature.go_end": "끝으로",
  // dynamic input
  "dynput.toggle": "# 키로 전환",
  // drawing / viewer
  "viewer.layers": "레이어",
  "viewer.properties": "속성",
  "viewer.snap": "스냅",
  "viewer.grid": "그리드",
  // quote
  "quote.title": "견적서",
  "quote.material_cost": "재료비",
  "quote.process_cost": "가공비",
  "quote.outsource_cost": "외주비",
  "quote.overhead": "경비",
  "quote.total": "합계",
  // footer
  "footer.triangles": "삼각형",
  "footer.selected": "선택",
};

const en: Dict = {
  "common.close": "Close",
  "common.cancel": "Cancel",
  "common.ok": "OK",
  "common.save": "Save",
  "common.delete": "Delete",
  "common.create": "Create",
  "common.edit": "Edit",
  "common.search": "Search",
  "common.loading": "Loading...",
  "common.error": "Error",
  "common.unit_mm": "mm",
  "nav.projects": "Projects",
  "nav.documents": "Drawings",
  "nav.model": "Model",
  "nav.draft": "Draft",
  "nav.quote": "Quote",
  "nav.admin": "Admin",
  "model.sketch": "Sketch",
  "model.extrude": "Extrude",
  "model.revolve": "Revolve",
  "model.primitive": "Primitive",
  "model.standard": "Standard Part",
  "model.boolean": "Boolean",
  "model.machining": "Machining Feature",
  "model.sheet": "Sheet Metal",
  "model.plane_select": "Draw a constrained sketch on a plane or part face",
  "model.select_feature": "Select a feature.",
  "model.measure": "Measure",
  "model.no_measure": "No measurements available.",
  "model.volume": "Volume",
  "model.surface_area": "Surface Area",
  "feature.suppressed": "Suppressed",
  "feature.error": "Error",
  "feature.rollback_hint": "Rollback position (click: go to end)",
  "feature.go_end": "Go to end",
  "dynput.toggle": "Press # to toggle",
  "viewer.layers": "Layers",
  "viewer.properties": "Properties",
  "viewer.snap": "Snap",
  "viewer.grid": "Grid",
  "quote.title": "Quotation",
  "quote.material_cost": "Material Cost",
  "quote.process_cost": "Process Cost",
  "quote.outsource_cost": "Outsource Cost",
  "quote.overhead": "Overhead",
  "quote.total": "Total",
  "footer.triangles": "Triangles",
  "footer.selected": "Selected",
};

const DICTS: Record<Locale, Dict> = { ko, en };

type I18nCtx = {
  locale: Locale;
  setLocale: (l: Locale) => void;
  t: (key: string, fallback?: string) => string;
};

const Ctx = createContext<I18nCtx>({
  locale: "ko",
  setLocale: () => {},
  t: (key) => key,
});

export function I18nProvider({ children, initial = "ko" }: { children: ReactNode; initial?: Locale }) {
  const [locale, setLocale] = useState<Locale>(initial);
  const t = useCallback(
    (key: string, fallback?: string) => DICTS[locale][key] ?? fallback ?? DICTS.ko[key] ?? key,
    [locale],
  );
  return <Ctx value={{ locale, setLocale, t }}>{children}</Ctx>;
}

export function useI18n() {
  return useContext(Ctx);
}

export const LOCALES: { code: Locale; label: string }[] = [
  { code: "ko", label: "한국어" },
  { code: "en", label: "English" },
];
