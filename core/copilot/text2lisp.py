"""Track 11 L3: Text to Draw — a Korean sentence to AX-LISP by rules (plan.md §17). The result is
readable LISP the user previews (and may edit) before the 2D screen runs it; anything the rules do
not understand goes to the LLM (when on) or back to the user.

Clauses joined by ',', '그리고', '와/과', '다음' are read in order; a shape may refer to the last
closed shape ('가운데', '모서리에서 15', '오른쪽 간격 20'). Coordinates in mm, origin (0, 0)."""

import math
import re
from typing import Any

MAX_TEXT = 500
MAX_MM = 100_000.0
MAX_COUNT = 360

N = r"[-+]?(?:\d+(?:\.\d*)?|\.\d+)"
PAIR = re.compile(rf"\(\s*({N})\s*,\s*({N})\s*\)")
SPLIT = re.compile(r"\s*(?:,|그리고|및|다음에?|후에?|하고)\s+|(?<=[형원선판])(?:과|와)\s+")
COUNT = re.compile(r"(\d{1,3})\s*(?:개|곳|군데|ea)")
KOR_SIDES = {"삼": 3, "사": 4, "오": 5, "육": 6, "칠": 7, "팔": 8, "구": 9, "십": 10}
LAYERS = {
    "외형선": "OUTLINE",
    "중심선": "CENTER",
    "숨은선": "HIDDEN",
    "가상선": "PHANTOM",
    "치수": "DIM",
    "문자": "TEXT",
    "해치": "HATCH",
}
Box = tuple[float, float, float, float]


class Fail(Exception):
    pass


def num(pattern: str, text: str) -> float | None:
    m = re.search(pattern, text)
    return float(m.group(1)) if m else None


def f(v: float) -> str:
    return str(round(v, 6)).removesuffix(".0")


def p(x: float, y: float) -> str:
    return f"(list {f(x)} {f(y)})"


def ok(*vals: float | None) -> bool:
    return all(v is not None and 0 < abs(v) <= MAX_MM for v in vals)


def diameter(t: str) -> float | None:
    d = num(rf"(?<!반)(?:지름|직경|외경|[ΦφøØ]|D)\s*({N})", t)  # not the 지름 in 반지름
    if d is None:
        r = num(rf"반지름\s*({N})|R\s*({N})", t)
        d = 2 * r if r is not None else None
    return d


class Ctx:
    def __init__(self) -> None:
        self.lines: list[str] = []
        self.said: list[str] = []
        self.box: Box | None = None

    def at(self, t: str, default: tuple[float, float]) -> tuple[float, float]:
        """An explicit (x,y), the centre of the last shape for 가운데, or the default."""
        m = PAIR.search(t)
        if m:
            return float(m.group(1)), float(m.group(2))
        if self.box and re.search(r"가운데|중앙|중심에|안에", t):
            x0, y0, x1, y1 = self.box
            return (x0 + x1) / 2, (y0 + y1) / 2
        gap = num(rf"(?:간격|떨어진|떨어져)\s*({N})", t) or 0.0
        if self.box and "오른쪽" in t and "이동" not in t:
            return self.box[2] + gap, self.box[1]
        if self.box and re.search(r"위에|위쪽", t) and "이동" not in t:
            return self.box[0], self.box[3] + gap
        return default

    def emit(self, said: str, *lines: str) -> None:
        self.said.append(said)
        self.lines.extend(lines)


def rect(c: Ctx, t: str) -> bool:
    m = re.search(rf"({N})\s*[xX×*]\s*({N})", t)
    w, h = (float(m.group(1)), float(m.group(2))) if m else (None, None)
    if w is None:
        w, h = num(rf"가로\s*({N})", t), num(rf"세로\s*({N})", t)
    if w is None and "정사각" in t:
        w = h = num(rf"({N})", t)
    if not ok(w, h):
        raise Fail("사각형 크기를 '100x50'이나 '가로 100 세로 50'으로 적어 주세요")
    assert w is not None and h is not None
    corner = c.at(t, (0.0, 0.0))
    if c.box and re.search(r"가운데|중앙|중심", t) and not PAIR.search(t):
        corner = (corner[0] - w / 2, corner[1] - h / 2)
    x, y = corner
    c.emit(f"사각형 {f(w)}×{f(h)} ({f(x)},{f(y)})", f'(command "REC" {p(x, y)} {p(x + w, y + h)})')
    c.box = (x, y, x + w, y + h)
    holes(c, t)
    return True


def holes(c: Ctx, t: str) -> None:
    """'구멍 지름 10 모서리에서 15' on the last box: four corner holes."""
    if not c.box or not re.search(r"구멍|홀", t):
        return
    d = diameter(t[t.find("구멍") if "구멍" in t else t.find("홀") :])
    if not ok(d):
        raise Fail("구멍 지름을 적어 주세요 (예: 구멍 지름 10)")
    assert d is not None
    x0, y0, x1, y1 = c.box
    e = num(rf"(?:모서리|가장자리|끝)\D{{0,6}}({N})", t) or max(d, 10.0)
    if 2 * e >= min(x1 - x0, y1 - y0):
        raise Fail("모서리 거리가 판 크기에 비해 너무 큽니다")
    pts = [(x0 + e, y0 + e), (x1 - e, y0 + e), (x1 - e, y1 - e), (x0 + e, y1 - e)]
    c.emit(
        f"모서리 구멍 Φ{f(d)} 4개 (모서리 {f(e)})",
        *(f'(command "C" {p(x, y)} {f(d / 2)})' for x, y in pts),
    )


def circles(c: Ctx, t: str) -> bool:
    d = diameter(t)
    if not ok(d):
        raise Fail("원의 지름이나 반지름을 적어 주세요 (예: 지름 20 원, 반지름 10 원)")
    assert d is not None
    m = COUNT.search(t)
    n = int(m.group(1)) if m else 1
    if not 1 <= n <= MAX_COUNT:
        raise Fail(f"개수는 1~{MAX_COUNT}개입니다")
    if c.box and re.search(r"모서리", t):
        c_box, c.box = c.box, c.box  # holes() reads the box
        holes(c, "구멍 " + t)
        c.box = c_box
        return True
    cx, cy = c.at(t, (0.0, 0.0))
    pcd = num(rf"(?:PCD|피치원(?:\s*지름)?)\s*({N})", t)
    if pcd is not None:  # bolt circle around the position
        if not ok(pcd) or n < 2:
            raise Fail("원형 배열은 PCD와 개수(2개 이상)를 적어 주세요")
        start = num(rf"시작\s*각도?\s*({N})", t) or 0.0
        c.emit(
            f"PCD {f(pcd)} 위 Φ{f(d)} {n}개",
            f'(setq i 0) (repeat {n} (command "C" (polar {p(cx, cy)} (+ {f(math.radians(start))} (* i (/ (* 2 pi) {n}))) {f(pcd / 2)}) {f(d / 2)}) (setq i (1+ i)))',
        )
        c.box = (
            cx - pcd / 2 - d / 2,
            cy - pcd / 2 - d / 2,
            cx + pcd / 2 + d / 2,
            cy + pcd / 2 + d / 2,
        )
        return True
    g = num(rf"(?:간격|피치)\s*({N})", t) or (d * 2 if n > 1 else 0.0)
    if n > 1 and not ok(g):
        raise Fail("간격을 적어 주세요 (예: 간격 30)")
    vertical = bool(re.search(r"세로로|세로 방향|위로", t))
    c.emit(
        f"원 Φ{f(d)} {n}개" + (f" 간격 {f(g)}" if n > 1 else "") + f" ({f(cx)},{f(cy)})",
        f'(setq i 0) (repeat {n} (command "C" (list (+ {f(cx)} (* i {f(0 if vertical else g)})) (+ {f(cy)} (* i {f(g if vertical else 0)}))) {f(d / 2)}) (setq i (1+ i)))',
    )
    span = g * (n - 1)
    c.box = (
        cx - d / 2,
        cy - d / 2,
        cx + d / 2 + (0 if vertical else span),
        cy + d / 2 + (span if vertical else 0),
    )
    return True


def polygon(c: Ctx, t: str) -> bool:
    m = re.search(r"(\d{1,2}|[삼사오육칠팔구십])\s*각형", t)
    assert m
    sides = int(m.group(1)) if m.group(1).isdigit() else KOR_SIDES[m.group(1)]
    r = num(rf"반지름\s*({N})", t) or ((diameter(t) or 0) / 2 or None)
    if not 3 <= sides <= 64 or not ok(r):
        raise Fail("다각형은 3~64각형과 반지름을 적어 주세요 (예: 육각형 반지름 30)")
    assert r is not None
    cx, cy = c.at(t, (0.0, 0.0))
    c.emit(f"{sides}각형 반지름 {f(r)}", f'(command "POL" {sides} {p(cx, cy)} {p(cx + r, cy)})')
    c.box = (cx - r, cy - r, cx + r, cy + r)
    return True


def line(c: Ctx, t: str) -> bool:
    pts = [(float(a), float(b)) for a, b in PAIR.findall(t)]
    if len(pts) < 2:
        length = num(rf"길이\s*({N})", t)
        if not ok(length):
            raise Fail("선은 '(0,0)에서 (100,0)까지'나 '길이 100 각도 30'으로 적어 주세요")
        assert length is not None
        ang = math.radians(num(rf"각도\s*({N})", t) or 0.0)
        x, y = pts[0] if pts else (0.0, 0.0)
        pts = [(x, y), (x + length * math.cos(ang), y + length * math.sin(ang))]
    c.emit(f"선 {len(pts) - 1}개 구간", f'(command "L" {" ".join(p(x, y) for x, y in pts)} "")')
    return True


def slot(c: Ctx, t: str) -> bool:
    length, w = num(rf"길이\s*({N})", t), num(rf"폭\s*({N})", t)
    if not ok(length, w) or (length or 0) <= (w or 0):
        raise Fail("장공은 길이와 폭을 적어 주세요 (길이 > 폭, 예: 장공 길이 40 폭 12)")
    assert length is not None and w is not None
    cx, cy = c.at(t, (0.0, 0.0))
    r, a = w / 2, (length - w) / 2  # centre distance from the middle
    c.emit(
        f"장공 {f(length)}×{f(w)}",
        f'(command "L" {p(cx - a, cy - r)} {p(cx + a, cy - r)} "")',
        f'(command "L" {p(cx + a, cy + r)} {p(cx - a, cy + r)} "")',
        f'(command "A" "C" {p(cx + a, cy)} {p(cx + a, cy - r)} {p(cx + a, cy + r)})',
        f'(command "A" "C" {p(cx - a, cy)} {p(cx - a, cy + r)} {p(cx - a, cy - r)})',
    )
    c.box = (cx - length / 2, cy - r, cx + length / 2, cy + r)
    return True


def flange(c: Ctx, t: str) -> bool:
    od = num(rf"(?:외경|바깥\s*지름|지름|[Φφ])\s*({N})", t)
    bore = num(rf"(?:내경|안\s*지름|보어)\s*({N})", t)
    m = COUNT.search(t)
    n = int(m.group(1)) if m else 4
    hole = num(rf"(?:볼트\s*구멍|구멍)\s*(?:지름\s*)?({N})", t)
    pcd = num(rf"PCD\s*({N})", t)
    if not ok(od, hole) or (bore is not None and not ok(bore)):
        raise Fail(
            "플랜지는 외경·구멍 지름을 적어 주세요 (예: 플랜지 외경 150 내경 50 구멍 14 8개 PCD 110)"
        )
    assert od is not None and hole is not None
    pcd = pcd or (od + (bore or 0)) / 2
    cx, cy = c.at(t, (0.0, 0.0))
    if not 2 <= n <= MAX_COUNT or pcd + hole >= od or (bore and bore + hole >= pcd):
        raise Fail("플랜지 치수가 맞지 않습니다 (내경 < PCD - 구멍, PCD + 구멍 < 외경)")
    lines = [f'(command "C" {p(cx, cy)} {f(od / 2)})']
    if bore:
        lines.append(f'(command "C" {p(cx, cy)} {f(bore / 2)})')
    lines.append(
        f'(setq i 0) (repeat {n} (command "C" (polar {p(cx, cy)} (* i (/ (* 2 pi) {n})) {f(pcd / 2)}) {f(hole / 2)}) (setq i (1+ i)))'
    )
    c.emit(
        f"플랜지 Φ{f(od)}"
        + (f" 내경 {f(bore)}" if bore else "")
        + f" 구멍 Φ{f(hole)}×{n} PCD {f(pcd)}",
        *lines,
    )
    c.box = (cx - od / 2, cy - od / 2, cx + od / 2, cy + od / 2)
    return True


def bracket(c: Ctx, t: str) -> bool:
    m = re.search(rf"({N})\s*[xX×*]\s*({N})", t)
    th = num(rf"두께\s*({N})", t)
    if not m or not ok(th):
        raise Fail("L 브래킷은 크기와 두께를 적어 주세요 (예: L 브래킷 80x60 두께 6)")
    w, h, th = float(m.group(1)), float(m.group(2)), float(th or 0)
    if th >= min(w, h):
        raise Fail("두께가 브래킷 크기보다 큽니다")
    x, y = c.at(t, (0.0, 0.0))
    pts = [(x, y), (x + w, y), (x + w, y + th), (x + th, y + th), (x + th, y + h), (x, y + h)]
    c.emit(
        f"L 브래킷 {f(w)}×{f(h)} 두께 {f(th)}",
        f'(command "PL" {" ".join(p(a, b) for a, b in pts)} "C")',
    )
    c.box = (x, y, x + w, y + h)
    return True


def text(c: Ctx, t: str) -> bool:
    m = re.search(
        r"'(.+)'|\"(.+)\"|‘(.+)’|“(.+)”|「(.+)」", t
    )  # the same kind of quote on both ends
    said = next((g for g in m.groups() if g), "") if m else ""
    if not said or len(said) > 200:
        raise Fail("문자는 따옴표로 적어 주세요 (예: 문자 '조립도' 높이 5)")
    h = num(rf"높이\s*({N})", t) or 3.5
    x, y = c.at(t, (0.0, 0.0))
    value = said.replace("\\", "\\\\").replace('"', '\\"')
    c.emit(f"문자 '{said}' 높이 {f(h)}", f'(command "T" {p(x, y)} {f(h)} "{value}")')
    return True


def dims(c: Ctx, t: str) -> bool:
    if not c.box:
        raise Fail("치수를 넣을 도형을 먼저 그려 주세요 (예: 100x50 사각형과 치수)")
    x0, y0, x1, y1 = c.box
    o = num(rf"(?:간격|떨어져)\s*({N})", t) or 10.0
    out = []
    if "세로" not in t:
        out.append(f'(command "DLI" {p(x0, y0)} {p(x1, y0)} {p(x0, y0 - o)})')
    if "가로" not in t:
        out.append(f'(command "DLI" {p(x1, y0)} {p(x1, y1)} {p(x1 + o, y0)})')
    c.emit(
        "치수 " + ("가로·세로" if len(out) == 2 else "가로" if "세로" not in t else "세로"), *out
    )
    return True


DIRS = {"오른쪽": (1, 0), "왼쪽": (-1, 0), "위": (0, 1), "아래": (0, -1)}


def edit(c: Ctx, t: str) -> bool:
    """Selected objects: (ssget "I") is the user's current selection."""
    base = PAIR.search(t)
    bx, by = (float(base.group(1)), float(base.group(2))) if base else (0.0, 0.0)
    sel = '(setq ss (ssget "I"))'
    if re.search(r"회전", t):
        ang = num(rf"({N})\s*도", t)
        if ang is None:
            raise Fail("회전 각도를 적어 주세요 (예: 선택한 것 45도 회전)")
        c.emit(
            f"선택 객체 {f(ang)}° 회전 (기준 {f(bx)},{f(by)})",
            sel,
            f'(command "ROTATE" ss "" {p(bx, by)} {f(ang)})',
        )
        return True
    if re.search(r"배|축척|확대|축소", t):
        k = num(rf"({N})\s*배", t) or num(rf"축척\s*({N})", t)
        if k is None or not 0 < k <= 1000:
            raise Fail("배율을 적어 주세요 (예: 선택한 것 2배 확대)")
        c.emit(
            f"선택 객체 {f(k)}배 (기준 {f(bx)},{f(by)})",
            sel,
            f'(command "SCALE" ss "" {p(bx, by)} {f(k)})',
        )
        return True
    if "대칭" in t:
        q = (bx, by + 1) if re.search(r"좌우|세로축|세로 축", t) else (bx + 1, by)
        c.emit("선택 객체 대칭 복사", sel, f'(command "MIRROR" ss "" {p(bx, by)} {p(*q)})')
        return True
    vec = next(((dx, dy) for w, (dx, dy) in DIRS.items() if w in t), None)
    rest = re.findall(N, COUNT.sub("", PAIR.sub("", t)))  # the distance: the number left over
    d = float(rest[0]) if rest else None
    if d is None or vec is None or not ok(d):
        raise Fail("방향과 거리를 적어 주세요 (예: 선택한 것 오른쪽으로 50 이동)")
    dx, dy = vec[0] * d, vec[1] * d
    m = COUNT.search(t)
    if "배열" in t or ("복사" in t and m):
        n = int(m.group(1)) if m else 2
        if not 2 <= n <= 500:
            raise Fail("배열 개수는 2~500개입니다(원본 포함)")
        c.emit(
            f"선택 객체 {n}개 배열 간격 {f(d)}",
            sel,
            f'(command "ARRAY" ss "" {n} {p(0, 0)} {p(dx, dy)})',
        )
        return True
    verb = "COPY" if "복사" in t else "MOVE"
    end = ' ""' if verb == "COPY" else ""
    c.emit(
        f"선택 객체 {'복사' if verb == 'COPY' else '이동'} ({f(dx)},{f(dy)})",
        sel,
        f'(command "{verb}" ss "" {p(0, 0)} {p(dx, dy)}{end})',
    )
    return True


def layer(c: Ctx, t: str) -> None:
    m = re.search(
        r"([A-Za-z][A-Za-z0-9_\-]{0,30}|외형선|중심선|숨은선|가상선|치수|문자|해치)\s*레이어", t
    )
    if m:
        name = LAYERS.get(m.group(1), m.group(1).upper())
        c.emit(f"레이어 {name}", f'(setvar "CLAYER" "{name}")')


def clause(c: Ctx, t: str) -> None:
    layer(c, t)
    if re.search(r"선택(?:한|된)", t):
        edit(c, t)
    elif "플랜지" in t:
        flange(c, t)
    elif "장공" in t or "슬롯" in t:
        slot(c, t)
    elif "브래킷" in t or "ㄱ자" in t:
        bracket(c, t)
    elif re.search(r"사각형|네모|판(?!금)|직사각", t):
        rect(c, t)
    elif re.search(r"각형", t):
        polygon(c, t)
    elif re.search(r"원|구멍|홀", t):
        circles(c, t)
    elif re.search(r"문자|글자|텍스트", t):
        text(c, t)
    elif "치수" in t:
        dims(c, t)
    elif re.search(r"선\b|직선|라인", t) or re.search(r"선(?:을|을\s|\s|$)", t):
        line(c, t)
    elif not re.search(r"레이어", t):
        raise Fail(f"'{t}'을(를) 이해하지 못했습니다")


def parse(text: str) -> dict[str, Any] | None:
    """Sentence -> {lisp, summary} | {error} (rules recognised it but it is wrong) | None."""
    t = " ".join(text.split())[:MAX_TEXT]  # one line: the sentence is echoed in a LISP comment
    if not t:
        return None
    c = Ctx()
    parts = [x for x in SPLIT.split(t) if x and x.strip()]
    try:
        for part in parts:
            clause(c, part.strip())
    except Fail as e:
        if not c.lines and str(e).endswith("이해하지 못했습니다"):
            return None  # nothing the rules know: LLM or help
        return {"error": str(e)}
    if not c.lines:
        return None
    return {"lisp": "\n".join([f"; {t}", *c.lines]) + "\n", "summary": " → ".join(c.said)}


# --- 3D (Track 11 L4): primitives with positions, stacked with '위에' -----------------------------

PAIR3 = re.compile(rf"\(\s*({N})\s*,\s*({N})\s*,\s*({N})\s*\)")
DIMS3 = re.compile(rf"({N})\s*[xX×*]\s*({N})\s*[xX×*]\s*({N})")


def solid(t: str, top: float | None) -> tuple[str, str, float]:
    """One 3D clause -> (said, LISP line, its top z)."""
    m = PAIR3.search(t)
    x, y, z = (float(m.group(1)), float(m.group(2)), float(m.group(3))) if m else (0.0, 0.0, 0.0)
    if not m and top is not None and re.search(r"위에|위로", t):
        z = top
    at = f"(list {f(x)} {f(y)} {f(z)})"
    if re.search(r"상자|박스|직육면체|블록", t):
        dims = DIMS3.search(t)
        if not dims:
            raise Fail("상자 크기를 '100x50x20'(가로x세로x높이)처럼 적어 주세요")
        a, b, h = (float(g) for g in dims.groups())
        if not ok(a, b, h):
            raise Fail("크기는 0보다 크고 100 m 이하여야 합니다")
        return (
            f"상자 {f(a)}×{f(b)}×{f(h)} ({f(x)},{f(y)},{f(z)})",
            f'(command "BOX" {f(a)} {f(b)} {f(h)} {at})',
            z + h,
        )
    if re.search(r"원뿔|콘", t):
        r1 = num(rf"(?:밑|아래)\D{{0,4}}반지름\s*({N})", t) or num(rf"반지름\s*({N})", t)
        r2 = num(rf"(?:윗|위)\D{{0,4}}반지름\s*({N})", t) or 0.0
        ch = num(rf"높이\s*({N})", t)
        if not ok(r1, ch):
            raise Fail("원뿔은 반지름과 높이를 적어 주세요 (예: 반지름 20 높이 40 원뿔)")
        assert r1 is not None and ch is not None
        return (
            f"원뿔 R{f(r1)}" + (f"/R{f(r2)}" if r2 else "") + f" 높이 {f(ch)}",
            f'(command "CONE" {f(r1)} {f(r2)} {f(ch)} {at})',
            z + ch,
        )
    if re.search(r"원통|원기둥|실린더|봉", t):
        d, ch = diameter(t), num(rf"(?:높이|길이)\s*({N})", t)
        if not ok(d, ch):
            raise Fail("원통은 지름(반지름)과 높이를 적어 주세요 (예: 지름 30 높이 50 원통)")
        assert d is not None and ch is not None
        return (
            f"원통 Φ{f(d)} 높이 {f(ch)} ({f(x)},{f(y)},{f(z)})",
            f'(command "CYLINDER" {f(d / 2)} {f(ch)} {at})',
            z + ch,
        )
    if re.search(r"(?<![원])구(?!멍)|공\b|구체", t):
        d = diameter(t)
        if not ok(d):
            raise Fail("구는 지름(반지름)을 적어 주세요 (예: 반지름 20 구)")
        assert d is not None
        return f"구 Φ{f(d)}", f'(command "SPHERE" {f(d / 2)} {at})', z + d / 2
    raise Fail(f"'{t}'을(를) 이해하지 못했습니다")


def parse3d(text: str) -> dict[str, Any] | None:
    """3D sentence -> {lisp, summary} | {error} | None (not primitives: the 3D proposals)."""
    t = " ".join(text.split())[:MAX_TEXT]
    if not t or not re.search(
        r"상자|박스|직육면체|블록|원통|원기둥|실린더|원뿔|(?<![원])구(?!멍)|구체", t
    ):
        return None
    lines, said, top = [f"; {t}"], [], None
    try:
        for part in (x.strip() for x in SPLIT.split(t) if x and x.strip()):
            s, line, top = solid(part, top)
            said.append(s)
            lines.append(line)
    except Fail as e:
        return {"error": str(e)}
    return {"lisp": "\n".join(lines) + "\n", "summary": " → ".join(said)}
