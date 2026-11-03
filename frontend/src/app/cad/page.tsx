"use client";

import AppShell from "@/components/shell/AppShell";
import QuickCad from "@/components/cad/QuickCad";

export default function CadMenuPage() {
  return (
    <AppShell title="캐드 메뉴">
      <QuickCad />
    </AppShell>
  );
}
