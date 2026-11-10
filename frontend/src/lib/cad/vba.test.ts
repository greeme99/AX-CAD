// 2D automation A2: VBA macros on the shared object model (plan.md §20).
import { describe, expect, it } from "vitest";
import { DrawingModel, type Ent } from "./objmodel";
import { macros, parse, runVba, VbaError, type VbaHost } from "./vba";

const ents: Ent[] = [
  { handle: "A1", layer: "0", geom: { type: "LINE", start: [0, 0], end: [10, 0] } },
  { handle: "A2", layer: "CUT", geom: { type: "CIRCLE", center: [5, 5], radius: 2 } },
];
const host = (extra: VbaHost = {}): VbaHost & { model: DrawingModel } => ({ model: new DrawingModel(ents, [{ name: "0" }, { name: "CUT" }, { name: "LOCK", locked: true }], "0"), ...extra });
const run = (src: string, h: VbaHost = host(), macro?: string) => runVba(src, h, macro);

const HELPERS = `
Function Pt(x As Double, y As Double) As Variant
    Dim p(0 To 2) As Double
    p(0) = x: p(1) = y: p(2) = 0
    Pt = p
End Function
`;

describe("VBA macros", () => {
  it("draws with ThisDrawing.ModelSpace like AutoCAD VBA", async () => {
    const out = await run(`
Option Explicit
Sub Main()
    Dim p1(0 To 2) As Double, p2(0 To 2) As Double
    p2(0) = 3: p2(1) = 4
    Dim ln As AcadLine
    Set ln = ThisDrawing.ModelSpace.AddLine(p1, p2)
    ln.Color = acRed
    Debug.Print ln.Length, ln.ObjectName
    Dim i As Integer
    For i = 0 To 3
        ThisDrawing.ModelSpace.AddCircle Pt(10 + i * 20, 10), 4
    Next i
    MsgBox "원 " & CStr(i) & "개"
End Sub
${HELPERS}`);
    expect(out.log).toEqual(["5 AcDbLine", "원 4개"]);
    expect(out.changes!.created.map((e) => e.geom.type)).toEqual(["LINE", "CIRCLE", "CIRCLE", "CIRCLE", "CIRCLE"]);
    expect(out.changes!.created[0].aci).toBe(1);
    expect(out.changes!.created[4].geom).toEqual({ type: "CIRCLE", center: [70, 10], radius: 4 });
  });

  it("For Each, If/ElseIf, Select Case, Do loops and string functions", async () => {
    const out = await run(`
Sub Main()
    Dim ent As Object, n As Integer, kinds As String
    For Each ent In ThisDrawing.ModelSpace
        Select Case ent.ObjectName
            Case "AcDbLine": kinds = kinds & "L"
            Case "AcDbCircle", "AcDbArc": kinds = kinds & "C"
            Case Else: kinds = kinds & "?"
        End Select
        n = n + 1
    Next
    Debug.Print kinds, n
    Dim k As Integer
    k = 0
    Do While k < 10
        k = k + 3
    Loop
    If k > 20 Then
        Debug.Print "big"
    ElseIf k = 12 Then
        Debug.Print "twelve"
    Else
        Debug.Print "other"
    End If
    Do
        k = k - 1
    Loop Until k <= 5
    Debug.Print k, UCase(Left("abcdef", 3)), Mid("abcdef", 2, 3), InStr("abcdef", "cd"), Replace("a-b-c", "-", "+")
    Debug.Print Format(3.14159, "0.00"), Len("가나다"), 7 \\ 2, 7 Mod 3, 2 ^ 10, Round(2.567, 1)
    Debug.Print Join(Split("a,b,c", ","), "|"), UBound(Array(1, 2, 3)), IIf(1 > 2, "y", "n")
End Sub`);
    expect(out.log).toEqual(["LC 2", "twelve", "5 ABC bcd 3 a+b+c", "3.14 3 3 1 1024 2.6", "a|b|c 2 n"]);
  });

  it("With blocks, layers, the active layer and property changes", async () => {
    const src = `
${HELPERS}
Sub Main()
    Dim lay As AcadLayer
    Set lay = ThisDrawing.Layers.Add("PART")
    ThisDrawing.ActiveLayer = lay
    With ThisDrawing.ModelSpace.AddText("A-1", Pt(0, 0), 3.5)
        .Color = acGreen
        Debug.Print .TextString, .Layer, .Height
    End With
    Dim c As Object
    Set c = ThisDrawing.HandleToObject("FFFF")
End Sub`;
    const out = await run(src).catch((e: VbaError) => e);
    // W4: HandleToObject of a handle the drawing has not: the error names the method, with the line
    expect(out).toBeInstanceOf(VbaError);
    expect((out as VbaError).message).toMatch(/HandleToObject/);
    expect((out as VbaError).line).toBe(src.split("\n").findIndex((l) => l.includes("HandleToObject")) + 1);
    const ok = await run(`
${HELPERS}
Sub Main()
    ThisDrawing.ActiveLayer = ThisDrawing.Layers.Add("PART")
    With ThisDrawing.ModelSpace.AddText("A-1", Pt(0, 0), 3.5)
        .Color = acGreen
        Debug.Print .TextString, .Layer, .Height
    End With
    Dim e As Object
    For Each e In ThisDrawing.ModelSpace
        If e.ObjectName = "AcDbCircle" Then e.Radius = e.Radius * 2: e.Move Pt(0, 0), Pt(10, 0)
    Next
End Sub`);
    expect(ok.log).toEqual(["A-1 PART 3.5"]);
    expect(ok.changes!.layers).toEqual(["PART"]);
    expect(ok.changes!.updated).toEqual([{ ...ents[1], geom: { type: "CIRCLE", center: [15, 5], radius: 4 } }]);
  });

  it("ByRef, ByVal, Optional, Exit and On Error Resume Next", async () => {
    const out = await run(`
Sub Inc(ByRef n As Integer, Optional ByVal stepBy As Integer = 1)
    n = n + stepBy
End Sub
Function Twice(ByVal v As Integer) As Integer
    v = v * 2
    Twice = v
End Function
Sub Main()
    Dim x As Integer, y As Integer
    x = 1
    Inc x
    Inc x, 10
    y = 5
    Debug.Print x, Twice(y), y
    Dim i As Integer
    For i = 1 To 100
        If i = 4 Then Exit For
    Next
    Debug.Print i
    On Error Resume Next
    ThisDrawing.ModelSpace.AddCircle Array(0, 0, 0), -1
    Debug.Print Err.Number <> 0, Err.Description
    Err.Clear
    Debug.Print Err.Number
End Sub`);
    expect(out.log[0]).toBe("12 10 5");
    expect(out.log[1]).toBe("4");
    expect(out.log[2]).toMatch(/^True .*반지름/);
    expect(out.log[3]).toBe("0");
  });

  it("Utility.GetPoint / GetReal ask the screen; cancel stops the macro", async () => {
    const answers: unknown[] = [[10, 20], 7.5];
    const h = host({ input: async () => answers.shift() as never });
    const out = await run(`
Sub Main()
    Dim p As Variant, r As Double
    p = ThisDrawing.Utility.GetPoint(, "중심점: ")
    r = ThisDrawing.Utility.GetReal("반지름: ")
    ThisDrawing.ModelSpace.AddCircle p, r
End Sub`, h);
    expect(out.changes!.created[0].geom).toEqual({ type: "CIRCLE", center: [10, 20], radius: 7.5 });
    await expect(run(`Sub Main()\n p = ThisDrawing.Utility.GetPoint(, "점")\nEnd Sub`, host({ input: async () => null }))).rejects.toThrow(/취소/);
    const picked = await run(`Sub Main()\n Dim e As Object, p As Variant\n ThisDrawing.Utility.GetEntity e, p, "고르세요"\n e.Color = 3\n Debug.Print e.ObjectName, p(0)\nEnd Sub`, host({ entsel: async () => ({ handle: "A2", at: [4, 5] }) }));
    expect(picked.log).toEqual(["AcDbCircle 4"]);
    expect(picked.changes!.updated[0]).toMatchObject({ handle: "A2", aci: 3 });
  });

  it("errors name the line; limits stop endless loops; locked layers are protected", async () => {
    await expect(run(`Sub Main()\n  Dim a\n  a = b + 1\nEnd Sub`)).rejects.toMatchObject({ line: 3, message: expect.stringMatching(/정의되지 않은 이름: b/) });
    await expect(run(`Sub Main()\n  Do\n  Loop\nEnd Sub`)).rejects.toThrow(/실행 단계/);
    await expect(run(`Sub Main()\n  Set x = New Collection\nEnd Sub`)).rejects.toThrow(/New로 객체를 만들 수 없습니다/);
    await expect(run(`Sub Main()\n  ThisDrawing.ActiveLayer = "LOCK"\n  ThisDrawing.ModelSpace.AddPoint Array(0, 0, 0)\nEnd Sub`)).rejects.toThrow(/잠긴 레이어/);
    await expect(run(`Sub Main()\n  If True Then\n    x = 1\nEnd Sub`)).rejects.toThrow(VbaError);
    await expect(run(`Sub Main()\n  Main\nEnd Sub`)).rejects.toThrow(/너무 깊습니다/);
    await expect(run(`Sub Main()\n  x = "a\nEnd Sub`)).rejects.toThrow(/닫히지 않은 문자열/);
  });

  it("lists the macros a user can run and picks one", async () => {
    const src = `Sub DrawA()\n Debug.Print "A"\nEnd Sub\nPrivate Sub Hidden()\nEnd Sub\nSub WithArg(x)\nEnd Sub\nSub DrawB()\n Debug.Print "B"\nEnd Sub`;
    expect(macros(parse(src))).toEqual(["DrawA", "DrawB"]);
    await expect(run(src)).rejects.toThrow(/실행할 매크로를 고르세요: DrawA, DrawB/);
    expect((await run(src, host(), "drawb")).log).toEqual(["B"]);
    await expect(run(src, host(), "WithArg")).rejects.toThrow(/인수가 있는 Sub/);
  });

  it("typed Dim starts at 0 / \"\" / False; Const lists", async () => {
    const out = await run(`Sub Main()\n Dim n As Integer, s As String, b As Boolean, v\n Const K1 = 2, K2 = 3\n Debug.Print "n=" & n, "s=" & s, b, IsEmpty(v), K1 * K2\nEnd Sub`);
    expect(out.log).toEqual(["n=0 s= False True 6"]);
  });

  it("line continuation, comments and Rem", async () => {
    const out = await run(`Sub Main() ' 시작
    Rem 주석
    Debug.Print "a" & _
        "b"
End Sub`);
    expect(out.log).toEqual(["ab"]);
  });

  it("limits: empty loops count, big strings are refused before they are made, Like has no regex", async () => {
    await expect(run(`Sub Main()\n Dim i As Double\n For i = 1 To 1E+12\n Next\nEnd Sub`)).rejects.toThrow(/실행 단계/);
    await expect(run(`Sub Main()\n x = Space(1000000000)\nEnd Sub`)).rejects.toThrow(/64 KB/);
    const t0 = Date.now();
    const out = await run(`Sub Main()\n Debug.Print String(60000, "a") Like "*a*a*a*a*a*a*a*b", "AB12" Like "AB##", "x" Like "?"\nEnd Sub`);
    expect(out.log).toEqual(["False True True"]);
    expect(Date.now() - t0).toBeLessThan(2000);
    await expect(run(`Sub Main()\n x = constructor\nEnd Sub`)).rejects.toThrow(/정의되지 않은 이름: constructor/);
  });
});
