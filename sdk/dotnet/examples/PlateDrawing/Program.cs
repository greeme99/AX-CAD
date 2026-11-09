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
var dwg = await cad.OpenDrawingAsync(blank.GetProperty("revision_id").GetString()!);

using (var tr = dwg.StartTransaction()) // nothing reaches the drawing unless Commit runs
{
    dwg.ActiveLayer = dwg.Layers.Add("CUT");
    dwg.ModelSpace.AddLightWeightPolyline([0, 0, 200, 0, 200, 100, 0, 100], closed: true);
    foreach (var (x, y) in new[] { (15.0, 15.0), (185.0, 15.0), (185.0, 85.0), (15.0, 85.0) })
        dwg.ModelSpace.AddCircle(new(x, y), 5);
    tr.Commit();
}

var saved = await dwg.SaveAsync();
Console.WriteLine($"revision {saved.GetProperty("revision_no").GetString()}: {saved.GetProperty("revision_id").GetString()}");
return 0;
