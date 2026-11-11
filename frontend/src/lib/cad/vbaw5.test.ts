// W5 (plan §22.4): the VBA language — GoTo and error handlers, Type, Enum, multi-dimensional arrays,
// Collection and Scripting.Dictionary, class modules (.cls) with properties.
import { describe, expect, it } from "vitest";
import { runVba, VbaError } from "./vba";

const run = async (src: string, classes: string[] = []) => (await runVba(src, {}, undefined, classes)).log;

describe("VBA language (W5)", () => {
  it("GoTo, On Error GoTo label, Resume Next / Resume label, Err.Number", async () => {
    expect(
      await run(`Sub Main()
    Dim i As Integer
    On Error GoTo Fail
    i = 1
Again:
    i = i + 1
    If i < 4 Then GoTo Again
    Debug.Print "i", i
    Err.Raise 513, , "custom"
    Debug.Print "after raise"
    Debug.Print 1 / 0
    Debug.Print "after div"
    Exit Sub
Fail:
    Debug.Print "error", Err.Number, Err.Description
    Resume Next
End Sub`),
    ).toEqual(["i 4", "error 513 custom", "after raise", "error 1004 계산 결과가 숫자가 아닙니다 (0으로 나누기 등)", "after div"]);
    await expect(run(`Sub Main()\n    GoTo Nowhere\nEnd Sub`)).rejects.toThrow(/레이블이 없습니다/);
    await expect(run(`Sub Main()\nTop:\n    GoTo Top\nEnd Sub`)).rejects.toThrow(/실행 단계/); // an endless GoTo loop stops
  });
  it("Type and Enum", async () => {
    expect(
      await run(`Type Pt
    X As Double
    Y As Double
    Tags(1) As String
End Type
Enum Shape
    Round = 1
    Square
    Hex = 6
End Enum
Sub Main()
    Dim a As Pt, b As Pt
    a.X = 3: a.Y = 4: a.Tags(1) = "t"
    b = a
    b.X = 10
    Debug.Print a.X, b.X, a.Tags(1), Square, Shape.Hex, TypeName(a)
End Sub`),
    ).toEqual(["3 10 t 2 6 Pt"]);
  });
  it("multi-dimensional arrays, ReDim Preserve, UBound(a, 2)", async () => {
    expect(
      await run(`Sub Main()
    Dim m(1 To 2, 0 To 2) As Integer, i As Integer, j As Integer
    For i = 1 To 2: For j = 0 To 2: m(i, j) = i * 10 + j: Next: Next
    Debug.Print m(2, 1), UBound(m, 1), UBound(m, 2), LBound(m)
    Dim v() As Integer
    ReDim v(2): v(0) = 7: v(2) = 9
    ReDim Preserve v(4)
    Debug.Print v(0), v(2), UBound(v)
End Sub`),
    ).toEqual(["21 2 2 1", "7 9 4"]);
  });
  it("Collection and Scripting.Dictionary", async () => {
    expect(
      await run(`Sub Main()
    Dim c As New Collection, x As Variant
    c.Add "a": c.Add "b", "kb": c.Add "z", , 1
    Debug.Print c.Count, c(1), c("KB"), c.Item(3)
    c.Remove 1
    For Each x In c: Debug.Print x: Next
    Dim d As Object
    Set d = CreateObject("Scripting.Dictionary")
    d.Add "M8", 1.25
    d("M10") = 1.5
    d.Item("M8") = 1.0
    Debug.Print d.Count, d("M10"), d.Exists("M8"), d.Exists("M12"), Join(d.Keys, ",")
    For Each x In d: Debug.Print x, d(x): Next
End Sub`),
    ).toEqual(["3 z b b", "a", "b", "2 1.5 True False M8,M10", "M8 1", "M10 1.5"]);
    await expect(run(`Sub Main()\n    Set x = CreateObject("WScript.Shell")\nEnd Sub`)).rejects.toThrow(/COM/);
  });
  it("class modules: fields, Property Get/Let, methods, Me, Class_Initialize, Is", async () => {
    const cls = `VERSION 1.0 CLASS
BEGIN
  MultiUse = -1  'True
END
Attribute VB_Name = "Bolt"
Private mSize As Double
Public Count As Integer
Private Sub Class_Initialize()
    mSize = 8
End Sub
Public Property Get Size() As Double
    Size = mSize
End Property
Public Property Let Size(v As Double)
    If v <= 0 Then Err.Raise 5, , "size > 0"
    mSize = v
End Property
Public Function Label() As String
    Label = "M" & Me.Size & " x" & Count
End Function
Public Sub Grow()
    Size = Size + 2
    Count = Count + 1
End Sub`;
    expect(
      await run(
        `Sub Main()
    Dim b As Bolt, c As Object
    Set b = New Bolt
    b.Count = 3
    b.Grow
    Debug.Print b.Size, b.Label(), TypeName(b)
    Set c = b
    Debug.Print c Is b, (New Bolt) Is b
    On Error Resume Next
    b.Size = -1
    Debug.Print Err.Number, b.Size
End Sub`,
        [cls],
      ),
    ).toEqual(["10 M10 x4 Bolt", "True False", "5 10"]);
    await expect(run(`Sub Main()\n    Set b = New Nut\nEnd Sub`)).rejects.toThrow(VbaError);
  });
});

describe("W5 security review", () => {
  it("limits are not caught by On Error; a Type holding itself and a run's data budget stop", async () => {
    await expect(run(`Sub Main()\n    On Error Resume Next\n    Do\n    Loop\nEnd Sub`)).rejects.toThrow(/실행 단계/);
    await expect(run(`Type A\n    Inner As A\nEnd Type\nSub Main()\n    Dim x As A\nEnd Sub`)).rejects.toThrow(/중첩|너무 깊/);
    await expect(
      run(`Sub Main()\n    Dim c As New Collection, i As Long, a() As Long\n    For i = 1 To 1000\n        ReDim a(19999)\n        c.Add a\n    Next\nEnd Sub`),
    ).rejects.toThrow(/데이터가 너무 큽니다/);
  });
  it("a broken or nameless class module is skipped with a note; arguments errors do not leak depth", async () => {
    const out = await runVba(`Sub Main()\n    Debug.Print "ok"\nEnd Sub`, {}, undefined, ["Sub Broken(", 'Attribute VB_Name = "Fine"\nPublic X As Long']);
    expect(out.log.some((l) => /건너뜀/.test(l))).toBe(true);
    expect(out.log.at(-1)).toBe("ok");
    expect(
      await run(`Sub F(a)\nEnd Sub\nSub Main()\n    Dim i As Integer\n    On Error Resume Next\n    For i = 1 To 300\n        F 1, 2\n    Next\n    On Error GoTo 0\n    F 1\n    Debug.Print "depth ok"\nEnd Sub`),
    ).toEqual(["depth ok"]);
  });
  it("Dictionary keys stay case-insensitive in text mode after a remove (index rebuilt)", async () => {
    expect(
      await run(`Sub Main()\n    Dim d As Object\n    Set d = CreateObject("Scripting.Dictionary")\n    d.CompareMode = 1\n    d.Add "A", 1: d.Add "B", 2: d.Add "C", 3\n    d.Remove "a"\n    Debug.Print d.Exists("c"), d("b"), d.Count\nEnd Sub`),
    ).toEqual(["True 2 2"]);
  });
});
