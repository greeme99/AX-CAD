import Link from "next/link";

// Wireframe §3 / NFR-06: CAD workbenches need a desktop (precise mouse, wide panels). Below the md
// breakpoint (phones) they show a notice instead of an unusable, overflowing layout.
export default function DesktopOnly({ back, children }: { back: string; children: React.ReactNode }) {
  return (
    <>
      <main className="flex min-h-screen flex-col items-center justify-center gap-4 bg-background p-6 text-center md:hidden">
        <p className="font-semibold text-foreground">이 화면은 데스크톱에서 사용하세요</p>
        <p className="text-sm text-body">도면 작도·3D 모델링은 1280px 이상의 화면과 마우스가 필요합니다.</p>
        <Link href={back} className="text-[var(--color-primary)] underline focus-visible:outline-2 focus-visible:outline-ring">
          목록으로 돌아가기
        </Link>
      </main>
      <div className="hidden md:contents">{children}</div>
    </>
  );
}
