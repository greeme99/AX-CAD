' ActiveX/VBA reference (ModelSpace.Add*, Layers, entity properties) — examples rewritten for AX-CAD
Sub Main()
    Dim ln As Object, c As Object, lay As Object
    Set lay = ThisDrawing.Layers.Add("CONF")
    lay.Color = 3
    ThisDrawing.ActiveLayer = lay
    Set ln = ThisDrawing.ModelSpace.AddLine(Array(0, 0, 0), Array(30, 40, 0))
    Set c = ThisDrawing.ModelSpace.AddCircle(Array(10, 10, 0), 5)
    Debug.Print ln.Length, ln.Layer, ThisDrawing.ModelSpace.Count
    c.Radius = 2
    Debug.Print Round(c.Area, 4), Round(c.Circumference, 4)
    ln.Move Array(0, 0, 0), Array(1, 1, 0)
    Debug.Print ln.StartPoint(0), ln.EndPoint(1)
    Debug.Print TypeName(ln), ln.ObjectName
End Sub
