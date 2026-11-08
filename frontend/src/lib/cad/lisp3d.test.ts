import { describe, expect, it } from "vitest";
import { runLisp } from "./lisp";
import { primitives } from "./lisp3d";

describe("AX-LISP on the 3D screen", () => {
  it("turns commands into primitive features with origin and axis", async () => {
    const out = await runLisp(`(command "BOX" 100 50 20)
(command "CYLINDER" 10 30 '(50 25 20))
(setq i 0) (repeat 3 (command "SPHERE" 5 (list (* i 30) 0 40) ) (setq i (1+ i)))
(command "CONE" 10 0 25 '(0 0 0) "X")`, { point3: true });
    const p = primitives(out.segments);
    expect(typeof p).not.toBe("string");
    const list = p as Exclude<typeof p, string>;
    expect(list.map((x) => x.kind)).toEqual(["BOX", "CYLINDER", "SPHERE", "SPHERE", "SPHERE", "CONE"]);
    expect(list[0]).toMatchObject({ length: 100, width: 50, height: 20, origin: [0, 0, 0], axis: "Z" });
    expect(list[1]).toMatchObject({ radius: 10, height: 30, origin: [50, 25, 20] });
    expect(list[4].origin).toEqual([60, 0, 40]);
    expect(list[5]).toMatchObject({ radius: 10, radius2: 0, height: 25, axis: "X" });
  });

  it("refuses unknown commands and bad values before anything is made", async () => {
    const bad = async (src: string) => primitives((await runLisp(src, { point3: true })).segments);
    expect(await bad('(command "BOX" 10 10 10) (command "LINE" \'(0 0) \'(1 1) "")')).toMatch("쓸 수 없는 명령: LINE");
    expect(await bad('(command "BOX" 10 -5 10)')).toMatch("width");
    expect(await bad('(command "SPHERE")')).toMatch("radius");
    expect(await bad('(repeat 51 (command "SPHERE" 1))')).toMatch("50개까지");
  });
});
