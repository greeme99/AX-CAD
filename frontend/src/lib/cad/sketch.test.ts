import { describe, expect, it } from "vitest";
import { addConstraint, build, constraintLabel, facePlane, makeConstraint, pickRef, PLANES, pointOf, preview, refAt, removeEntities, toLocal, toWorld, type SkEntity } from "./sketch";

const line = (id: number, start: [number, number], end: [number, number]): SkEntity => ({ id, type: "LINE", start, end });

describe("plane maths", () => {
  it("maps sketch coordinates onto the FreeCAD base planes and back", () => {
    expect(toWorld(PLANES.XY, [3, 4])).toEqual([3, 4, 0]);
    expect(toWorld(PLANES.XZ, [3, 4])).toEqual([3, 0, 4]); // seen from the front: up is +Z
    expect(toWorld(PLANES.YZ, [3, 4])).toEqual([0, 3, 4]);
    const pl = {
      origin: [10, 0, 5] as [number, number, number],
      normal: [0, 0, 1] as [number, number, number],
      xdir: [0, 1, 0] as [number, number, number],
    };
    expect(toLocal(pl, toWorld(pl, [2, -7]))).toEqual([2, -7]);
  });

  it("puts a face plane's x axis along the projected global X (Y for faces facing X)", () => {
    expect(facePlane([1, 2, 3], [0, 0, 2])).toEqual({
      origin: [1, 2, 3],
      normal: [0, 0, 1],
      xdir: [1, 0, 0],
    });
    expect(facePlane([0, 0, 0], [-1, 0, 0]).xdir).toEqual([0, 1, 0]);
  });
});

describe("drawing tools", () => {
  it("rectangle: four lines closed by COINCIDENT with H/V, snapped corner to the origin", () => {
    const r = build("RECT", [{ p: [0, 0], at: { e: -1, p: "start" } }, { p: [40, 25] }], [], false)!;
    expect(r.entities.map((e) => e.id)).toEqual([1, 2, 3, 4]);
    expect(r.constraints.filter((c) => c.type === "COINCIDENT")).toHaveLength(5);
    expect(r.constraints.filter((c) => c.type === "HORIZONTAL" || c.type === "VERTICAL")).toHaveLength(4);
    expect(r.constraints.at(-1)).toEqual({
      type: "COINCIDENT",
      refs: [
        { e: 1, p: "start" },
        { e: -1, p: "start" },
      ],
    });
    expect(build("RECT", [{ p: [0, 0] }, { p: [40, 0] }], [], false)).toBeNull();
  });

  it("line: snapped ends and near-level lines get auto constraints", () => {
    const ents = [line(1, [0, 0], [10, 0])];
    const r = build(
      "LINE",
      [
        { p: [10, 0], at: { e: 1, p: "end" } },
        { p: [10.1, 8], on: { e: 1, p: "edge" } },
      ],
      ents,
      true,
    )!;
    expect(r.entities[0]).toMatchObject({ id: 2, construction: true });
    expect(r.constraints).toEqual([
      {
        type: "COINCIDENT",
        refs: [
          { e: 2, p: "start" },
          { e: 1, p: "end" },
        ],
      },
      {
        type: "POINT_ON_OBJECT",
        refs: [
          { e: 2, p: "end" },
          { e: 1, p: "edge" },
        ],
      },
      { type: "VERTICAL", refs: [{ e: 2, p: "edge" }] },
    ]);
  });

  it("arc from centre, start and end; circle; point", () => {
    const a = build("ARC", [{ p: [0, 0] }, { p: [5, 0] }, { p: [0, 9] }], [], false)!.entities[0];
    expect(a).toMatchObject({
      type: "ARC",
      radius: 5,
      start_angle: 0,
      end_angle: 90,
    });
    expect(build("CIRCLE", [{ p: [1, 1] }, { p: [4, 5] }], [], false)!.entities[0]).toMatchObject({ radius: 5 });
    expect(build("POINT", [{ p: [2, 2], at: { e: 3, p: "center" } }], [], false)!.constraints).toHaveLength(1);
    expect(preview("RECT", [{ p: [0, 0] }], [2, 1])).toHaveLength(4);
    expect(preview("ARC", [{ p: [0, 0] }, { p: [1, 0] }], [0, 1])[0]).toMatchObject({ type: "ARC", end_angle: 90 });
  });
});

describe("constraints and picking", () => {
  const ents: SkEntity[] = [
    line(1, [0, 0], [40, 0]),
    line(2, [40, 0], [40, 25]),
    {
      id: 3,
      type: "ARC",
      center: [40, 30],
      radius: 5,
      start_angle: 270,
      end_angle: 90,
    },
  ];

  it("makes valued constraints from the current geometry, points first", () => {
    expect(makeConstraint("DISTANCE", [{ e: 1, p: "edge" }], ents)).toEqual({
      type: "DISTANCE",
      refs: [{ e: 1, p: "edge" }],
      value: 40,
    });
    expect(
      makeConstraint(
        "DISTANCE",
        [
          { e: 1, p: "edge" },
          { e: 2, p: "end" },
        ],
        ents,
      ),
    ).toMatchObject({
      refs: [
        { e: 2, p: "end" },
        { e: 1, p: "edge" },
      ],
      value: 25,
    });
    expect(
      makeConstraint(
        "ANGLE",
        [
          { e: 1, p: "edge" },
          { e: 2, p: "edge" },
        ],
        ents,
      ),
    ).toMatchObject({ value: 90 });
    expect(makeConstraint("DIAMETER", [{ e: 3, p: "edge" }], ents)).toMatchObject({ value: 10 });
    expect(makeConstraint("RADIUS", [{ e: 1, p: "edge" }], ents)).toMatch(/반지름/);
    expect(
      makeConstraint(
        "TANGENT",
        [
          { e: 3, p: "center" },
          { e: 2, p: "end" },
        ],
        ents,
      ),
    ).toMatch(/끝점/);
  });

  it("endpoint tangency replaces the coincident it implies", () => {
    const co = {
      type: "COINCIDENT" as const,
      refs: [
        { e: 2, p: "end" as const },
        { e: 3, p: "start" as const },
      ],
    };
    const t = makeConstraint(
      "TANGENT",
      [
        { e: 3, p: "start" },
        { e: 2, p: "end" },
      ],
      ents,
    );
    expect(typeof t).toBe("object");
    expect(addConstraint([co], t as Exclude<typeof t, string>)).toEqual([t]);
  });

  it("picks points before curves before axes, and maps snaps to references", () => {
    expect(pickRef(ents, [40.2, 0.1], 0.5)).toEqual({ e: 1, p: "end" });
    expect(pickRef(ents, [20, 0.3], 0.5)).toEqual({ e: 1, p: "edge" });
    expect(pickRef(ents, [-10, 0.3], 0.5)).toEqual({ e: -1, p: "edge" });
    expect(pickRef(ents, [0.1, 0], 0.5)).toEqual({ e: -1, p: "start" });
    expect(refAt(ents, { x: 40, y: 25, kind: "END", handle: "2" })).toEqual({
      at: { e: 2, p: "end" },
    });
    expect(refAt(ents, { x: 45, y: 30, kind: "NEA", handle: "3" })).toEqual({
      on: { e: 3, p: "edge" },
    });
    expect(pointOf(ents, { e: 3, p: "end" })![1]).toBeCloseTo(35);
  });

  it("removing an entity drops its constraints; labels read in Korean", () => {
    const p = removeEntities(
      {
        entities: ents,
        constraints: [
          { type: "HORIZONTAL", refs: [{ e: 1, p: "edge" }] },
          { type: "FIXED", refs: [{ e: 2, p: "end" }] },
        ],
      },
      new Set([1]),
    );
    expect(p.entities.map((e) => e.id)).toEqual([2, 3]);
    expect(p.constraints).toHaveLength(1);
    expect(
      constraintLabel({
        type: "DISTANCE",
        refs: [
          { e: 2, p: "start" },
          { e: -1, p: "start" },
        ],
        value: 12.34567,
      }),
    ).toBe("거리 #2.시작, 원점 = 12.346");
  });
});
