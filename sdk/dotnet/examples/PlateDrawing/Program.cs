// A 200 x 100 plate with four holes (Ø10) on a blank A3 drawing, saved as the next revision.
//   dotnet run --project sdk/dotnet/examples/PlateDrawing -- <project id>
// Needs the AX-CAD install on this PC running (Client.Local reads the install token).
using AxCad;

if (args.Length < 1 || !long.TryParse(args[0], out var projectId))
{
    Console.Error.WriteLine("usage: PlateDrawing <project id>");
    return 2;
}

using var cad = AxCadClient.Local();
var doc = await cad.CreateDocumentAsync(projectId, $"PLATE-{DateTime.Now:yyMMddHHmmss}", "PART", "SDK 예제 판재");
var blank = await cad.BlankDrawingAsync(doc.GetProperty("document_id").GetInt64());
var db = await cad.OpenDatabaseAsync(blank.GetProperty("revision_id").GetString()!);

// AutoCAD .NET style: nothing reaches the drawing unless Commit runs
using (var tr = db.TransactionManager.StartTransaction())
{
    var bt = (BlockTable)tr.GetObject(db.BlockTableId, OpenMode.ForRead);
    var ms = (BlockTableRecord)tr.GetObject(bt[BlockTableRecord.ModelSpace], OpenMode.ForWrite);
    var lt = (LayerTable)tr.GetObject(db.LayerTableId, OpenMode.ForWrite);
    if (!lt.Has("CUT")) lt.Add(new LayerTableRecord { Name = "CUT", ColorIndex = 1 });

    var outline = new Polyline { Layer = "CUT" };
    foreach (var (x, y) in new[] { (0.0, 0.0), (200.0, 0.0), (200.0, 100.0), (0.0, 100.0) })
        outline.AddVertexAt(outline.NumberOfVertices, new Point2d(x, y), 0, 0, 0);
    outline.IsClosed = true;
    ms.AppendEntity(outline);
    tr.AddNewlyCreatedDBObject(outline, true);

    foreach (var (x, y) in new[] { (15.0, 15.0), (185.0, 15.0), (185.0, 85.0), (15.0, 85.0) })
    {
        var hole = new Circle(new Point3d(x, y, 0), Vector3d.ZAxis, 5) { Layer = "CUT" };
        ms.AppendEntity(hole);
        tr.AddNewlyCreatedDBObject(hole, true);
    }
    tr.Commit();
}

var saved = await db.SaveAsync();
Console.WriteLine($"revision {saved.GetProperty("revision_no").GetString()}: {saved.GetProperty("revision_id").GetString()}");
return 0;
