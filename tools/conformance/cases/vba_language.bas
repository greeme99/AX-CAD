' VBA language reference: Select Case, loops, strings, arrays, error handling
Type Part
    Name As String
    Qty As Integer
End Type
Enum Finish
    Raw = 0
    Painted
End Enum
Function Total(parts() As Part) As Integer
    Dim i As Integer
    For i = LBound(parts) To UBound(parts)
        Total = Total + parts(i).Qty
    Next
End Function
Sub Main()
    Dim p(1) As Part, d As Object, k As Variant
    p(0).Name = "bolt": p(0).Qty = 4
    p(1).Name = "nut": p(1).Qty = 6
    Debug.Print Total(p), Painted
    Select Case Total(p)
        Case 1 To 5: Debug.Print "few"
        Case Is > 5: Debug.Print "many"
    End Select
    Set d = CreateObject("Scripting.Dictionary")
    d("M8") = 2: d("M10") = 3
    For Each k In d.Keys
        Debug.Print k & "=" & d(k)
    Next
    Debug.Print UCase(Left("flange", 3)), InStr("a,b", ","), Replace("1-2", "-", "+")
    On Error GoTo Oops
    Err.Raise 1000, , "planned"
    Exit Sub
Oops:
    Debug.Print Err.Number, Err.Description
End Sub
