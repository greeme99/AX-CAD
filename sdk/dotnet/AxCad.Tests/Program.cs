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

// --- a drawing as objects -------------------------------------------------------------------
fake.On("GET", "/api/revisions/R1/render", 200, """
{"success":true,"data":{
  "current_layer":"0",
  "layers":[{"name":"0","locked":false},{"name":"CUT","locked":false},{"name":"LOCK","locked":true}],
  "entities":[
    {"handle":"A1","type":"LINE","layer":"0","geom":{"type":"LINE","start":[0,0],"end":[3,4]},"aci":256,"linetype":"BYLAYER"},
    {"handle":"A2","type":"CIRCLE","layer":"CUT","geom":{"type":"CIRCLE","center":[5,5],"radius":2}},
    {"handle":"A3","type":"POINT","layer":"LOCK","geom":{"type":"POINT","point":[1,1]}},
    {"handle":"D1","type":"DIMENSION","layer":"0","paths":[]}
  ]}}
""");
var dwg = await cad.OpenDrawingAsync("R1");
Check(dwg.ModelSpace.Count == 3, "editable entities only (a server-drawn one stays out)");
var line = (Line)dwg.ModelSpace[0];
Check(Math.Abs(line.Length - 5) < 1e-12 && line.ObjectName == "AcDbLine", "Line.Length, ObjectName");
ThrowsNow(() => line.Color = 1, "NO_TRANSACTION", "a change outside a transaction is refused");
ThrowsNow(() => dwg.ModelSpace.AddLine(Point3d.Origin, new(1, 1)), "NO_TRANSACTION", "AddLine outside a transaction is refused");

// abort: nothing stays
Transaction? ended;
using (var tr = dwg.StartTransaction())
{
    ended = tr;
    dwg.ModelSpace.AddCircle(new(0, 0), 9);
    line.Color = 3;
    dwg.Layers.Add("TEMP");
    ThrowsNow(() => dwg.StartTransaction(), "TRANSACTION_ACTIVE", "one transaction at a time");
} // disposed without Commit
Check(ended.Status == TransactionStatus.RolledBack, "status RolledBack after Dispose");
ThrowsNow(() => ended.Commit(), "TRANSACTION_ENDED", "no Commit after the end");
Check(dwg.ModelSpace.Count == 3 && line.Color == 256 && !dwg.Layers.Has("TEMP") && ((JsonArray)dwg.Changes()["created"]!).Count == 0, "dispose without Commit rolls everything back");

// commit: the edit request the 2D screen would send
using (var tr = dwg.StartTransaction())
{
    dwg.ActiveLayer = dwg.Layers.Add("PART");
    var c = dwg.ModelSpace.AddCircle(new(10, 10), 4);
    c.Color = 1;
    dwg.ModelSpace.AddLightWeightPolyline([0, 0, 10, 0, 10, 10], closed: true);
    line.Move(new(0, 0), new(10, 0));
    var circle = (Circle)dwg.ModelSpace[1];
    circle.Radius *= 2;
    var mirrored = (Line)line.Mirror(new(0, 0), new(0, 1));
    Check(Math.Abs(mirrored.StartPoint.X + 10) < 1e-9 && Math.Abs(mirrored.EndPoint.X + 13) < 1e-9, "Mirror makes a reflected copy");
    mirrored.Erase();
    ThrowsNow(() => ((DBPoint)dwg.ModelSpace[2]).Position = new(2, 2), "LAYER_LOCKED", "locked layer protected");
    ThrowsNow(() => dwg.ModelSpace.AddCircle(new(0, 0), 0), "BAD_GEOMETRY", "radius 0 refused");
    ThrowsNow(() => dwg.Layers.Add("bad*name"), "BAD_LAYER", "layer name rules");
    tr.Commit();
    Check(tr.Status == TransactionStatus.Committed, "status Committed");
}
var body = dwg.Changes();
var created = (JsonArray)body["created"]!;
var modified = (JsonArray)body["modified"]!;
Check(created.Count == 2 && created[0]!["layer"]!.GetValue<string>() == "PART" && created[0]!["color"]!.GetValue<int>() == 1, "created on the active layer with its colour (the erased copy left out)");
Check(modified.Count == 2 && modified[0]!["handle"]!.GetValue<string>() == "A1" && modified[0]!["color"] is null, "modified: handle, geometry; an unchanged colour is not sent");
Check(((JsonArray)body["layers"]!)[0]!["create"]!.GetValue<bool>(), "the new layer is created on save");
fake.On("POST", "/api/revisions/R1/edits", 200, """{"success":true,"data":{"revision_id":"R2","revision_no":"B"}}""");
var saved = await dwg.SaveAsync();
Check(saved.GetProperty("revision_id").GetString() == "R2" && fake.LastBody!["deleted"]!.AsArray().Count == 0, "SaveAsync posts the edits and returns the new revision");
await Throws(() => dwg.SaveAsync(), "DRAWING_SAVED", "the same edits are not sent twice");
ThrowsNow(() => dwg.StartTransaction(), "DRAWING_SAVED", "a saved drawing is read only");

// geometry: arcs flip when mirrored, polylines with bulges
dwg = await cad.OpenDrawingAsync("R1");
await Throws(() => dwg.SaveAsync(), "NO_CHANGES", "nothing to save in a fresh drawing");
using (var tr = dwg.StartTransaction())
{
    var arc = dwg.ModelSpace.AddArc(new(0, 0), 5, 0, Math.PI / 2);
    var m = (Arc)arc.Mirror(new(0, 0), new(0, 1)); // reflected across the y axis: 90..180 degrees
    Check(Math.Abs(m.StartAngle - Math.PI / 2) < 1e-9 && Math.Abs(m.EndAngle - Math.PI) < 1e-9, "a mirrored arc keeps its ends in order");
    var half = dwg.ModelSpace.AddLightWeightPolyline([0, 0, 10, 0], closed: true);
    half.SetBulgeAt(0, 1.0); // a half circle on the first segment
    Check(Math.Abs(half.Area - Math.PI * 25 / 2) < 1e-9, "polyline area includes the bulge");
    Check(Math.Abs(half.Length - (10 + Math.PI * 5)) < 1e-9, "polyline length includes the arc");
    arc.Rotate(Point3d.Origin, Math.PI);
    Check(Math.Abs(arc.StartAngle - Math.PI) < 1e-9, "Rotate turns the arc's angles");
    tr.Abort();
}
await Throws(() => dwg.SaveAsync(), "NO_CHANGES", "nothing left after the abort");

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
