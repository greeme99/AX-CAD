// AX-CAD .NET SDK tests (2D automation A3, TC-192): a fake API (no network), run with
//   dotnet run --project sdk/dotnet/AxCad.Tests
// Exits 1 when a check fails.
using System.Net;
using System.Text;
using System.Text.Json;
using System.Text.Json.Nodes;
using AxCad;

var failures = 0;
void Check(bool ok, string what)
{
    Console.WriteLine($"{(ok ? "ok  " : "FAIL")} {what}");
    if (!ok) failures++;
}
async Task Throws(Func<Task> f, string code, string what)
{
    try
    {
        await f();
        Check(false, $"{what} (no error)");
    }
    catch (AxCadException e)
    {
        Check(e.Code == code, $"{what} -> {e.Code}");
    }
}
void ThrowsNow(Action f, string code, string what) => Throws(() => { f(); return Task.CompletedTask; }, code, what).GetAwaiter().GetResult();

// --- connection rules (the same as the Python SDK) -------------------------------------------
ThrowsNow(() => new AxCadClient("http://cad.example", token: "t"), "BAD_URL", "plain http with a token only for this PC");
ThrowsNow(() => new AxCadClient("https://cad.example", localToken: "x"), "BAD_URL", "the install token never leaves this PC");
ThrowsNow(() => new AxCadClient("ftp://x"), "BAD_URL", "only http(s)");
using (new AxCadClient("https://cad.example", token: "t")) Check(true, "https with a token");
await Throws(() => AxCadClient.LoginTokenAsync("http://cad.example", "a", "b"), "BAD_URL", "login over plain http refused");

// --- envelopes, errors, files, headers --------------------------------------------------------
var fake = new FakeApi();
using var cad = new AxCadClient("http://127.0.0.1:18000", localToken: "LT", handler: fake);
fake.On("GET", "/api/x", 200, """{"success":true,"data":{"n":1},"error":null}""");
Check((await cad.GetAsync("/api/x")).GetProperty("n").GetInt32() == 1, "success envelope -> data");
Check(fake.Last!.Headers.GetValues("X-AXCAD-Local").Single() == "LT", "install token header sent");
fake.On("POST", "/api/projects", 422, """{"success":false,"data":null,"error":{"code":"DUPLICATE_KEY","message":"exists"}}""");
await Throws(() => cad.CreateProjectAsync("P", "n"), "DUPLICATE_KEY", "API error code kept");
Check(fake.LastBody!["needs_quote"]!.GetValue<bool>(), "new project sends needs_quote");
fake.On("GET", "/api/quotes/7/report", 200, "PK\u0003\u0004", "application/octet-stream");
Check((await cad.QuoteReportAsync(7)).Length == 4, "files come back as bytes");
fake.On("GET", "/api/redirect", 302, "", "text/html");
await Throws(() => cad.GetAsync("/api/redirect"), "HTTP_ERROR", "a redirect is not followed");
var dxf = Path.Combine(Path.GetTempPath(), "pl\"ate.dxf");
await File.WriteAllTextAsync(dxf, "0\nEOF\n");
fake.On("POST", "/api/documents/5/dxf", 200, """{"success":true,"data":{"revision_id":"r1"}}""");
await cad.UploadDxfAsync(5, dxf);
Check(fake.LastRaw!.Contains("filename=plate.dxf") || fake.LastRaw.Contains("filename=\"plate.dxf\""), "upload file name cleaned of quotes");

// --- W8: a drawing as an AutoCAD .NET-style Database --------------------------------------------
fake.On("GET", "/api/revisions/R1/render", 200, """
{"success":true,"data":{
  "current_layer":"0",
  "layers":[{"name":"0","locked":false},{"name":"CUT","locked":false,"aci":1},{"name":"LOCK","locked":true}],
  "tables":{"linetypes":[{"name":"DASHED","description":"__ __","pattern":[5,-2]}],"textstyles":[{"name":"Standard","font":"txt.shx"}],"dimstyles":[{"name":"ISO-25","dim":{"dimtxt":2.5}}],"regapps":["ACAD","MYAPP"],"layouts":["Layout1"]},
  "blocks":[{"name":"BOLT","paths":[],"attdefs":[{"tag":"SIZE","prompt":"Size","default":"M8"}]}],
  "object_ids":{"model_space":"1F","nod":"C","tables":{"layer":"2"},"records":{"layer":{"0":"10","CUT":"11"}},"blocks":{"BOLT":"30"}},
  "dicts":{"MYDICT":{"type":"DICTIONARY","handle":"A0","entries":{"REC":{"type":"XRECORD","handle":"A1","tags":[[1,"hello"],[40,2.5]]}}}},
  "entities":[
    {"handle":"A1","type":"LINE","layer":"0","geom":{"type":"LINE","start":[0,0],"end":[3,4]},"aci":256,"linetype":"BYLAYER"},
    {"handle":"A2","type":"CIRCLE","layer":"CUT","geom":{"type":"CIRCLE","center":[5,5],"radius":2},"xd":{"MYAPP":[[1000,"tag"]]}},
    {"handle":"A3","type":"POINT","layer":"LOCK","geom":{"type":"POINT","point":[1,1]}},
    {"handle":"A4","type":"INSERT","layer":"0","geom":{"type":"INSERT","name":"BOLT","insert":[50,0],"xscale":1,"yscale":1,"rotation":0},"att":{"SIZE":"M8"}},
    {"handle":"D1","type":"DIMENSION","layer":"0","paths":[]}
  ]}}
""");
await Throws(() => cad.OpenDatabaseAsync("../x"), "BAD_ID", "a revision id is not a path");
fake.On("GET", "/api/revisions/BAD/render", 200, """{"success":true,"data":{"layers":[{"name":"0"}],"entities":[{"handle":"Z","layer":"0","geom":{"type":"LINE"}}]}}""");
await Throws(() => cad.OpenDatabaseAsync("BAD"), "BAD_RENDER", "a payload not shaped as the server sends it -> one error type");
var db = await cad.OpenDatabaseAsync("R1");
ThrowsNow(() => db.TransactionManager.GetObject(db.BlockTableId, OpenMode.ForRead), "NO_TRANSACTION", "GetObject needs a transaction");
ObjectId lineId, circleId, insertId;

// read, and refusals (AutoCAD's eNotOpenForWrite / eOnLockedLayer)
using (var tr = db.TransactionManager.StartTransaction())
{
    var bt = (BlockTable)tr.GetObject(db.BlockTableId, OpenMode.ForRead);
    var ms = (BlockTableRecord)tr.GetObject(bt[BlockTableRecord.ModelSpace], OpenMode.ForRead);
    var ids = ms.ToList();
    Check(ids.Count == 4 && ms.Handle.ToString() == "1F", "model space: the editable entities (a server-drawn one stays out), its handle");
    (lineId, circleId, insertId) = (ids[0], ids[1], ids[3]);
    var line = (Line)tr.GetObject(lineId, OpenMode.ForRead);
    Check(Math.Abs(line.Length - 5) < 1e-12 && line.Handle.ToString() == "A1" && lineId.ObjectClassName == "LINE", "Line.Length, Handle, ObjectClassName");
    ThrowsNow(() => line.ColorIndex = 1, "NOT_OPEN_FOR_WRITE", "a change needs ForWrite");
    ThrowsNow(() => tr.GetObject(ids[2], OpenMode.ForWrite), "LAYER_LOCKED", "an entity on a locked layer is not opened for write");
    ThrowsNow(() => ms.AppendEntity(new Line(Point3d.Origin, new(1, 1))), "NOT_OPEN_FOR_WRITE", "AppendEntity needs the space ForWrite");
    ThrowsNow(() => db.TransactionManager.StartTransaction(), "TRANSACTION_ACTIVE", "one transaction at a time");
    var lt = (LayerTable)tr.GetObject(db.LayerTableId, OpenMode.ForRead);
    Check(lt.Has("cut") && ((LayerTableRecord)tr.GetObject(lt["CUT"], OpenMode.ForRead)).ColorIndex == 1 && lt["CUT"].Handle.ToString() == "11", "layer table: case-insensitive names, records, handles");
    var c = (Circle)tr.GetObject(circleId, OpenMode.ForRead);
    Check(c.GetXDataForApplication("MYAPP")!.AsArray()[1].Value as string == "tag", "XDATA as a ResultBuffer");
    var nod = (DBDictionary)tr.GetObject(db.NamedObjectsDictionaryId, OpenMode.ForRead);
    var sub = (DBDictionary)tr.GetObject(nod.GetAt("MYDICT"), OpenMode.ForRead);
    var rec = (Xrecord)tr.GetObject(sub.GetAt("REC"), OpenMode.ForRead);
    Check(rec.Data!.AsArray()[0].Value as string == "hello" && sub.Count == 1, "named object dictionary, XRecord data");
    var br = (BlockReference)tr.GetObject(insertId, OpenMode.ForRead);
    var att = (AttributeReference)tr.GetObject(br.AttributeCollection[0], OpenMode.ForRead);
    Check(br.Name == "BOLT" && att.Tag == "SIZE" && att.TextString == "M8", "block reference, its attributes");
    tr.Commit();
}

// abort: nothing stays
Transaction? ended;
using (var tr = db.TransactionManager.StartTransaction())
{
    ended = tr;
    var ms = (BlockTableRecord)tr.GetObject(((BlockTable)tr.GetObject(db.BlockTableId, OpenMode.ForRead))[BlockTableRecord.ModelSpace], OpenMode.ForWrite);
    ms.AppendEntity(new Circle(Point3d.Origin, Vector3d.ZAxis, 9));
    ((Line)tr.GetObject(lineId, OpenMode.ForWrite)).ColorIndex = 3;
    var lt = (LayerTable)tr.GetObject(db.LayerTableId, OpenMode.ForWrite);
    lt.Add(new LayerTableRecord { Name = "TEMP" });
} // disposed without Commit
Check(ended.Status == TransactionStatus.RolledBack, "status RolledBack after Dispose");
ThrowsNow(() => ended.Commit(), "TRANSACTION_ENDED", "no Commit after the end");
Check(db.Changes().ToJsonString() == """{"created":[],"modified":[],"deleted":[]}""", "dispose without Commit rolls everything back");

// commit: the edit request the 2D screen would send
using (var tr = db.TransactionManager.StartTransaction())
{
    var bt = (BlockTable)tr.GetObject(db.BlockTableId, OpenMode.ForWrite);
    var ms = (BlockTableRecord)tr.GetObject(bt[BlockTableRecord.ModelSpace], OpenMode.ForWrite);
    var lt = (LayerTable)tr.GetObject(db.LayerTableId, OpenMode.ForWrite);
    var part = lt.Add(new LayerTableRecord { Name = "PART", ColorIndex = 5 });
    db.Clayer = part;
    var c = new Circle(new(10, 10), Vector3d.ZAxis, 4) { ColorIndex = 1, Layer = "PART" };
    ms.AppendEntity(c);
    tr.AddNewlyCreatedDBObject(c, true);
    var pl = new Polyline();
    foreach (var (x, y) in new[] { (0.0, 0.0), (10.0, 0.0), (10.0, 10.0) }) pl.AddVertexAt(pl.NumberOfVertices, new(x, y), 0, 0, 0);
    pl.IsClosed = true;
    pl.Layer = "PART";
    ms.AppendEntity(pl);
    var line = (Line)tr.GetObject(lineId, OpenMode.ForWrite);
    line.TransformBy(Matrix3d.Displacement(new Vector3d(10, 0)));
    var circle = (Circle)tr.GetObject(circleId, OpenMode.ForWrite);
    circle.Radius *= 2;
    var mirrored = line.GetTransformedCopy(Matrix3d.Mirroring(new Point3d(0, 0), new Point3d(0, 1)));
    var ml = (Line)mirrored;
    Check(Math.Abs(ml.StartPoint.X + 10) < 1e-9 && Math.Abs(ml.EndPoint.X + 13) < 1e-9, "Matrix3d.Mirroring: a reflected copy");
    ThrowsNow(() => line.TransformBy(new Matrix3d([2, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1])), "NOT_SUPPORTED", "a non-uniform scale is refused");
    ThrowsNow(() => new Circle(Point3d.Origin, Vector3d.ZAxis, 0), "BAD_GEOMETRY", "radius 0 refused");
    ThrowsNow(() => c.LineWeight = 17, "BAD_VALUE", "lineweights are AutoCAD's");
    ThrowsNow(() => new SelectionFilter([new TypedValue(62, "red")]), "BAD_FILTER", "a colour filter takes a number");
    ThrowsNow(() => lt.Add(new LayerTableRecord { Name = "bad*name" }), "BAD_NAME", "layer name rules");
    // a block with an attribute definition, inserted with its attribute
    var blk = new BlockTableRecord { Name = "FLANGE", Origin = new(100, 100) };
    var blkId = bt.Add(blk);
    blk.AppendEntity(new Circle(Point3d.Origin, Vector3d.ZAxis, 20));
    var ad = new AttributeDefinition(new(0, -25), "DN50", "size", "Size?", ObjectId.Null) { Height = 3 };
    blk.AppendEntity(ad);
    var br = new BlockReference(new(200, 0), blkId);
    ms.AppendEntity(br);
    var ar = new AttributeReference();
    ar.SetAttributeFromBlock(ad, br.BlockTransform);
    br.AttributeCollection.AppendAttribute(ar);
    ThrowsNow(() => blk.AppendEntity(new BlockReference(Point3d.Origin, blkId)), "SELF_REFERENCE", "a block cannot contain itself");
    // the drawing's block reference: an attribute value, and the block renamed
    var bolt = (BlockReference)tr.GetObject(insertId, OpenMode.ForRead);
    ((AttributeReference)tr.GetObject(bolt.AttributeCollection[0], OpenMode.ForWrite)).TextString = "M10";
    ((BlockTableRecord)tr.GetObject(bt["BOLT"], OpenMode.ForWrite)).Name = "BOLT2";
    // XDATA, a dictionary entry, a text style
    var ra = (RegAppTable)tr.GetObject(db.RegAppTableId, OpenMode.ForWrite);
    ra.Add(new RegAppTableRecord { Name = "MINE" });
    circle.XData = new ResultBuffer(new TypedValue(1001, "MINE"), new TypedValue(1070, 7));
    ThrowsNow(() => circle.XData = new ResultBuffer(new TypedValue(1001, "NOPE")), "NO_REGAPP", "XDATA needs a registered application");
    var nod = (DBDictionary)tr.GetObject(db.NamedObjectsDictionaryId, OpenMode.ForWrite);
    var sub = (DBDictionary)tr.GetObject(nod.GetAt("MYDICT"), OpenMode.ForWrite);
    sub.SetAt("NEW", new Xrecord { Data = new ResultBuffer(new TypedValue(1, "a"), new TypedValue(70, 3)) });
    var ts = (TextStyleTable)tr.GetObject(db.TextStyleTableId, OpenMode.ForWrite);
    ts.Add(new TextStyleTableRecord { Name = "KS", FileName = @"C:\fonts\malgun.ttf", TextSize = 3.5 });
    // dimensions
    var dim = new RotatedDimension(0, new(0, 0), new(50, 0), new(0, 10), "", ObjectId.Null) { Dimasz = 3 };
    ms.AppendEntity(dim);
    Check(Math.Abs(dim.Measurement - 50) < 1e-12, "RotatedDimension.Measurement");
    tr.Commit();
    Check(tr.Status == TransactionStatus.Committed, "status Committed");
}
var body = db.Changes();
var created = (JsonArray)body["created"]!;
var modified = (JsonArray)body["modified"]!;
Check(created.Count == 4 && created[0]!["layer"]!.GetValue<string>() == "PART" && created[0]!["color"]!.GetValue<int>() == 1 && created[1]!["geom"]!["closed"]!.GetValue<bool>(), "created: layer, colour, a closed polyline (the free copy is not in the drawing)");
Check(created[2]!["geom"]!["name"]!.GetValue<string>() == "FLANGE" && created[2]!["attributes"]!["SIZE"]!.GetValue<string>() == "DN50", "a new block reference with its attribute");
Check(created[3]!["geom"]!["overrides"]!["dimasz"]!.GetValue<double>() == 3, "a dimension's own variables");
Check(modified.Count == 3 && modified[0]!["handle"]!.GetValue<string>() == "A1" && modified[0]!["color"] is null, "modified: handle, geometry; an unchanged colour is not sent");
Check(modified[1]!["xdata"]!["MINE"]![0]![1]!.GetValue<long>() == 7, "XDATA sent by application");
Check(modified[2]!["handle"]!.GetValue<string>() == "A4" && modified[2]!["attributes"]!["SIZE"]!.GetValue<string>() == "M10" && modified[2]!["geom"]!["name"]!.GetValue<string>() == "BOLT", "an attribute value; a renamed block keeps its drawing name (the server renames references)");
var layers = (JsonArray)body["layers"]!;
Check(layers[0]!["name"]!.GetValue<string>() == "PART" && layers[0]!["create"]!.GetValue<bool>() && layers[0]!["color"]!.GetValue<int>() == 5, "the new layer is created with its colour");
var blocks = (JsonArray)body["blocks"]!;
Check(blocks.Count == 2 && blocks[0]!["op"]!.GetValue<string>() == "rename" && blocks[1]!["op"]!.GetValue<string>() == "create" && blocks[1]!["attdefs"]![0]!["insert"]![1]!.GetValue<double>() == 75, "blocks: rename, create with attdefs (base added: the server subtracts it)");
var tables = (JsonArray)body["tables"]!;
Check(tables.Any(t => t!["kind"]!.GetValue<string>() == "regapp") && tables.Any(t => t!["kind"]!.GetValue<string>() == "textstyle" && t["font"]!.GetValue<string>() == "malgun.ttf"), "table records: a font file name only");
var dicts = (JsonArray)body["dictionaries"]!;
Check(dicts.Count == 1 && dicts[0]!["path"]!.ToJsonString() == """["MYDICT","NEW"]""" && dicts[0]!["handle"] is null, "an XRecord set in the named object dictionary");

// selection
var sel = db.Editor.SelectAll(new SelectionFilter([new TypedValue(0, "CIRCLE"), new TypedValue(8, "PART,CUT")]));
Check(sel.Status == PromptStatus.OK && sel.Value!.Count == 2, "SelectAll with a type and a layer list");
Check(db.Editor.SelectWindow(new(-1, -1), new(14, 25)).Value!.Count == 5, "SelectWindow: wholly inside (locked layers too: selecting is reading)");
Check(db.Editor.SelectAll(new SelectionFilter([new TypedValue(0, "SPLINE")])).Status == PromptStatus.Error, "nothing selected: Status Error, Value null");

fake.On("POST", "/api/revisions/R1/edits", 200, """{"success":true,"data":{"revision_id":"R2","revision_no":"B"}}""");
var saved = await db.SaveAsync();
Check(saved.GetProperty("revision_id").GetString() == "R2" && fake.LastBody!["created"]!.AsArray().Count == 4, "SaveAsync posts the edits and returns the new revision");
await Throws(() => db.SaveAsync(), "DRAWING_SAVED", "the same edits are not sent twice");
ThrowsNow(() => db.TransactionManager.StartTransaction(), "DRAWING_SAVED", "a saved database is read only");

// geometry: arcs flip when mirrored, polylines with bulges, matrices
db = await cad.OpenDatabaseAsync("R1");
await Throws(() => db.SaveAsync(), "NO_CHANGES", "nothing to save in a fresh drawing");
using (var tr = db.TransactionManager.StartTransaction())
{
    var arc = new Arc(Point3d.Origin, 5, 0, Math.PI / 2);
    var m = (Arc)arc.GetTransformedCopy(Matrix3d.Mirroring(new Point3d(0, 0), new Point3d(0, 1))); // across the y axis: 90..180 degrees
    Check(Math.Abs(m.StartAngle - Math.PI / 2) < 1e-9 && Math.Abs(m.EndAngle - Math.PI) < 1e-9, "a mirrored arc keeps its ends in order");
    var half = new Polyline();
    half.AddVertexAt(0, new(0, 0), 1.0, 0, 0); // a half circle on the first segment
    half.AddVertexAt(1, new(10, 0), 0, 0, 0);
    half.IsClosed = true;
    Check(Math.Abs(half.Area - Math.PI * 25 / 2) < 1e-9, "polyline area includes the bulge");
    Check(Math.Abs(half.Length - (10 + Math.PI * 5)) < 1e-9, "polyline length includes the arc");
    arc.TransformBy(Matrix3d.Rotation(Math.PI, Vector3d.ZAxis, Point3d.Origin));
    Check(Math.Abs(arc.StartAngle - Math.PI) < 1e-9, "Rotation turns the arc's angles");
    var r = Matrix3d.Rotation(0.3, Vector3d.ZAxis, new Point3d(4, 5)) * Matrix3d.Scaling(2, new Point3d(1, 1));
    var p = new Point3d(7, -2);
    Check(p.TransformBy(r).TransformBy(r.Inverse()).IsEqualTo(p, new Tolerance(1e-9, 1e-9)), "Matrix3d inverse");
    Check(Math.Abs(new Vector3d(1, 0).GetAngleTo(new Vector3d(0, -1), Vector3d.ZAxis) - 1.5 * Math.PI) < 1e-12, "Vector3d.GetAngleTo about an axis");
    tr.Abort();
}
await Throws(() => db.SaveAsync(), "NO_CHANGES", "nothing left after the abort");

Console.WriteLine(failures == 0 ? "all checks passed" : $"{failures} check(s) failed");
return failures == 0 ? 0 : 1;

/// <summary>A fake AX-CAD API: answers by method and path, remembers the last request.</summary>
sealed class FakeApi : HttpMessageHandler
{
    readonly Dictionary<string, (int Status, string Body, string Type)> _routes = [];
    public HttpRequestMessage? Last { get; private set; }
    public JsonNode? LastBody { get; private set; }
    public string? LastRaw { get; private set; }

    public void On(string method, string path, int status, string body, string type = "application/json") => _routes[$"{method} {path}"] = (status, body, type);

    protected override async Task<HttpResponseMessage> SendAsync(HttpRequestMessage request, CancellationToken ct)
    {
        Last = request;
        LastRaw = request.Content is null ? null : await request.Content.ReadAsStringAsync(ct);
        LastBody = request.Content?.Headers.ContentType?.MediaType == "application/json" && LastRaw is not null ? JsonNode.Parse(LastRaw) : null;
        var key = $"{request.Method} {request.RequestUri!.AbsolutePath}";
        if (!_routes.TryGetValue(key, out var r)) return new HttpResponseMessage(HttpStatusCode.NotFound) { Content = new StringContent("""{"success":false,"error":{"code":"NO_ROUTE","message":"test"}}""", Encoding.UTF8, "application/json") };
        return new HttpResponseMessage((HttpStatusCode)r.Status) { Content = new StringContent(r.Body, Encoding.UTF8, r.Type) };
    }
}
