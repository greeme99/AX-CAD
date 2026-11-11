' ActiveX reference: Blocks, attributes, selection sets, XData, dictionaries
Sub Main()
    Dim b As Object, a As Object, ref As Object, ss As Object, x As Variant, t As Variant, v As Variant
    Set b = ThisDrawing.Blocks.Add(Array(0, 0, 0), "CONF_BLK")
    b.AddCircle Array(0, 0, 0), 5
    Set a = b.AddAttribute(2.5, 0, "No?", Array(0, -8, 0), "NO", "1")
    Debug.Print b.Count, a.TagString, a.TextString
    ThisDrawing.RegisteredApplications.Add "CONF_APP"
    Set ref = ThisDrawing.ModelSpace.AddPoint(Array(3, 3, 0))
    ref.SetXData Array(1001, 1000), Array("CONF_APP", "x")
    ref.GetXData "CONF_APP", t, v
    Debug.Print t(1), v(1)
    ThisDrawing.ModelSpace.AddLine Array(0, 0, 0), Array(10, 0, 0)
    Set ss = ThisDrawing.SelectionSets.Add("CONF")
    ss.Select 5, , , Array(0), Array("LINE")
    Debug.Print ss.Count
    Set x = ThisDrawing.Dictionaries.Add("CONF_D").AddXRecord("R")
    x.SetXRecordData Array(1), Array("hello")
    x.GetXRecordData t, v
    Debug.Print v(0)
End Sub
