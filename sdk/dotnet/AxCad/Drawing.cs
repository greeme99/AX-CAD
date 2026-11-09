// AX-CAD .NET SDK: a 2D drawing revision as AutoCAD-style objects (ModelSpace.AddLine,
// Entity.Move, Layers.Add ...). Open with AxCadClient.OpenDrawingAsync, change it inside a
// Transaction (Commit keeps the changes; Dispose without Commit rolls them back, as in AutoCAD
// .NET), then SaveAsync: the changes go to POST /api/revisions/{id}/edits like the 2D screen's save
// and make a new revision. Coordinates: WCS, double, the drawing unit (mm); angles in radians.
using System.Collections;
using System.Text.Json;
using System.Text.Json.Nodes;

namespace AxCad;

public readonly record struct Point3d(double X, double Y, double Z = 0)
{
    public static Point3d Origin => new(0, 0);
    public double DistanceTo(Point3d o) => Math.Sqrt((X - o.X) * (X - o.X) + (Y - o.Y) * (Y - o.Y));
    internal JsonArray Json() => [X, Y];
    internal static Point3d From(JsonNode? n) => n is JsonArray a && a.Count >= 2 ? new(a[0]!.GetValue<double>(), a[1]!.GetValue<double>()) : throw new AxCadException(0, "BAD_GEOMETRY", "point expected");
}

public sealed class Drawing
{
    readonly AxCadClient _client;
    readonly List<Entity> _all = [];
    readonly HashSet<string> _layers;
    readonly HashSet<string> _locked;
    readonly List<string> _newLayers = [];
    Transaction? _tr;

    public string RevisionId { get; }
    public ModelSpace ModelSpace { get; }
    public LayerTable Layers { get; }
    /// <summary>The layer new entities go on.</summary>
    public string ActiveLayer
    {
        get => _active;
        set => _active = HasLayer(value) ? Layer(value) : throw new AxCadException(0, "NO_LAYER", $"no layer {value} in the drawing");
    }
    string _active;

    internal Drawing(AxCadClient client, string revisionId, JsonElement render)
    {
        _client = client;
        RevisionId = revisionId;
        var layers = render.GetProperty("layers").EnumerateArray().ToList();
        _layers = new(layers.Select(l => l.GetProperty("name").GetString()!), StringComparer.OrdinalIgnoreCase);
        _locked = new(layers.Where(l => l.TryGetProperty("locked", out var k) && k.ValueKind == JsonValueKind.True).Select(l => l.GetProperty("name").GetString()!), StringComparer.OrdinalIgnoreCase);
        // only editable entities (with a geometry) are objects here; the rest stays as it is
        foreach (var e in render.GetProperty("entities").EnumerateArray())
        {
            if (!e.TryGetProperty("geom", out var g) || g.ValueKind != JsonValueKind.Object) continue;
            int? aci = e.TryGetProperty("aci", out var a) && a.ValueKind == JsonValueKind.Number ? a.GetInt32() : null;
            string? lt = e.TryGetProperty("linetype", out var l) && l.ValueKind == JsonValueKind.String ? l.GetString() : null;
            _all.Add(Entity.Create(this, e.GetProperty("handle").GetString()!, e.GetProperty("layer").GetString()!, JsonNode.Parse(g.GetRawText())!.AsObject(), aci, lt, isNew: false));
        }
        var current = render.TryGetProperty("current_layer", out var cl) && cl.ValueKind == JsonValueKind.String ? cl.GetString() : null;
        _active = current is not null && HasLayer(current) && !_locked.Contains(current) ? Layer(current) : _layers.FirstOrDefault(n => !_locked.Contains(n)) ?? "0";
        ModelSpace = new ModelSpace(this);
        Layers = new LayerTable(this);
    }

    internal IEnumerable<Entity> Live => _all.Where(e => !e.IsErased);
    internal bool HasLayer(string name) => _layers.Contains(name);
    internal string Layer(string name) => _layers.First(n => n.Equals(name, StringComparison.OrdinalIgnoreCase));
    /// <summary>Starts the transaction every change goes in (one at a time).</summary>
    public Transaction StartTransaction()
    {
        if (_tr is not null) throw new AxCadException(0, "TRANSACTION_ACTIVE", "commit or abort the open transaction first");
        Unsaved();
        return _tr = new Transaction(this);
    }
    /// <summary>True once SaveAsync made a new revision: this object then stays as it was (read only).</summary>
    public bool IsSaved { get; private set; }
    void Unsaved()
    {
        if (IsSaved) throw new AxCadException(0, "DRAWING_SAVED", "saved as a new revision: open that revision to go on editing");
    }
    public bool InTransaction => _tr is not null;

    internal (List<(Entity E, Entity.State S)> Ents, int Count, List<string> NewLayers, string Active) Snapshot() =>
        (_all.Select(e => (e, e.Save())).ToList(), _all.Count, [.. _newLayers], _active);
    internal void Restore((List<(Entity E, Entity.State S)> Ents, int Count, List<string> NewLayers, string Active) s)
    {
        _all.RemoveRange(s.Count, _all.Count - s.Count); // entities made in the transaction go
        foreach (var (e, st) in s.Ents) e.Load(st);
        foreach (var n in _newLayers.Except(s.NewLayers).ToList()) _layers.Remove(n);
        _newLayers.Clear();
        _newLayers.AddRange(s.NewLayers);
        _active = s.Active;
    }
    internal void End(Transaction t)
    {
        if (_tr == t) _tr = null;
    }

    internal void Writable(string layer)
    {
        if (_tr is null) throw new AxCadException(0, "NO_TRANSACTION", "changes go inside a transaction: using var tr = drawing.StartTransaction(); ... tr.Commit();");
        if (!HasLayer(layer)) throw new AxCadException(0, "NO_LAYER", $"no layer {layer} in the drawing");
        if (_locked.Contains(layer)) throw new AxCadException(0, "LAYER_LOCKED", $"layer {layer} is locked");
    }
    internal T Add<T>(T e) where T : Entity
    {
        if (_all.Count(x => x.IsNew) >= 10_000) throw new AxCadException(0, "TOO_MANY", "10,000 new entities per save");
        _all.Add(e);
        return e;
    }
    internal string AddLayer(string name)
    {
        if (_tr is null) throw new AxCadException(0, "NO_TRANSACTION", "changes go inside a transaction");
        if (HasLayer(name)) return Layer(name);
        if (name.Length is 0 or > 255 || name.Any(c => c < 0x20 || c == 0x7f || "<>/\\\":;?*|=`".Contains(c)))
            throw new AxCadException(0, "BAD_LAYER", $"not a layer name: {name}");
        _layers.Add(name);
        _newLayers.Add(name);
        return name;
    }
    internal IReadOnlyCollection<string> LayerNames => _layers;

    /// <summary>The edit request the 2D screen would send (created, modified, deleted, new layers).</summary>
    public JsonObject Changes()
    {
        var created = new JsonArray();
        var modified = new JsonArray();
        var deleted = new JsonArray();
        foreach (var e in _all)
        {
            if (e.IsNew && !e.IsErased) created.Add(e.Edit(withHandle: false, onlyChanged: false));
            else if (!e.IsNew && e.IsErased) deleted.Add(e.Handle);
            else if (!e.IsNew && e.IsModified) modified.Add(e.Edit(withHandle: true, onlyChanged: true));
        }
        var body = new JsonObject { ["created"] = created, ["modified"] = modified, ["deleted"] = deleted };
        if (_newLayers.Count > 0) body["layers"] = new JsonArray(_newLayers.Select(n => (JsonNode)new JsonObject { ["name"] = n, ["create"] = true }).ToArray());
        return body;
    }

    /// <summary>Saves the changes as the next revision; returns it (revision_id, revision_no, ...).
    /// Open that revision to go on editing.</summary>
    public async Task<JsonElement> SaveAsync(CancellationToken ct = default)
    {
        if (_tr is not null) throw new AxCadException(0, "TRANSACTION_ACTIVE", "commit or abort the transaction before saving");
        Unsaved();
        var body = Changes();
        if (((JsonArray)body["created"]!).Count + ((JsonArray)body["modified"]!).Count + ((JsonArray)body["deleted"]!).Count == 0 && body["layers"] is null)
            throw new AxCadException(0, "NO_CHANGES", "nothing to save");
        var saved = await _client.PostAsync($"/api/revisions/{Uri.EscapeDataString(RevisionId)}/edits", body, ct);
        IsSaved = true; // the same edits are never sent twice
        return saved;
    }
}

public enum TransactionStatus { Active, Committed, RolledBack }

/// <summary>AutoCAD-style transaction: Commit keeps the changes made since StartTransaction; Abort,
/// or Dispose without Commit, puts the drawing back as it was (nothing half-applied).</summary>
public sealed class Transaction : IDisposable
{
    readonly Drawing _d;
    readonly (List<(Entity E, Entity.State S)> Ents, int Count, List<string> NewLayers, string Active) _snap;

    internal Transaction(Drawing d)
    {
        _d = d;
        _snap = d.Snapshot();
    }

    /// <summary>Active → Committed, or Active → RolledBack (Abort / Dispose without Commit).</summary>
    public TransactionStatus Status { get; private set; } = TransactionStatus.Active;

    public void Commit()
    {
        if (Status != TransactionStatus.Active) throw new AxCadException(0, "TRANSACTION_ENDED", "the transaction has ended");
        Status = TransactionStatus.Committed;
        _d.End(this);
    }

    public void Abort()
    {
        if (Status != TransactionStatus.Active) return;
        _d.Restore(_snap);
        Status = TransactionStatus.RolledBack;
        _d.End(this);
    }

    public void Dispose() => Abort();
}

public sealed class ModelSpace : IEnumerable<Entity>
{
    readonly Drawing _d;
    internal ModelSpace(Drawing d) => _d = d;

    public int Count => _d.Live.Count();
    public Entity this[int index] => _d.Live.ElementAt(index);
    public IEnumerator<Entity> GetEnumerator() => _d.Live.ToList().GetEnumerator();
    IEnumerator IEnumerable.GetEnumerator() => GetEnumerator();

    JsonObject G(string type) => new() { ["type"] = type };
    T New<T>(JsonObject g) where T : Entity
    {
        _d.Writable(_d.ActiveLayer);
        return _d.Add((T)Entity.Create(_d, $"new-{Guid.NewGuid():N}", _d.ActiveLayer, g, null, null, isNew: true));
    }

    public Line AddLine(Point3d start, Point3d end) => New<Line>(Set(G("LINE"), ("start", start.Json()), ("end", end.Json())));
    public Circle AddCircle(Point3d center, double radius) => New<Circle>(Set(G("CIRCLE"), ("center", center.Json()), ("radius", Positive(radius, "radius"))));
    public Arc AddArc(Point3d center, double radius, double startAngle, double endAngle) =>
        New<Arc>(Set(G("ARC"), ("center", center.Json()), ("radius", Positive(radius, "radius")), ("start_angle", Deg(startAngle)), ("end_angle", Deg(endAngle))));
    /// <summary>Vertices as x0, y0, x1, y1, ... (AutoCAD's AddLightWeightPolyline).</summary>
    public Polyline AddLightWeightPolyline(double[] xy, bool closed = false)
    {
        if (xy.Length < 4 || xy.Length % 2 != 0) throw new AxCadException(0, "BAD_GEOMETRY", "x, y pairs: at least two vertices");
        var pts = new JsonArray();
        for (var i = 0; i < xy.Length; i += 2) pts.Add(new JsonArray(Finite(xy[i]), Finite(xy[i + 1]), 0.0));
        return New<Polyline>(Set(G("LWPOLYLINE"), ("points", pts), ("closed", closed)));
    }
    public DBText AddText(string text, Point3d insertion, double height) =>
        New<DBText>(Set(G("TEXT"), ("insert", insertion.Json()), ("height", Positive(height, "height")), ("value", text), ("rotation", 0.0)));
    public DBPoint AddPoint(Point3d point) => New<DBPoint>(Set(G("POINT"), ("point", point.Json())));

    static JsonObject Set(JsonObject g, params (string Key, JsonNode? Value)[] items)
    {
        foreach (var (k, v) in items) g[k] = v;
        return g;
    }
    internal static double Finite(double v) => double.IsFinite(v) && Math.Abs(v) <= 1e9 ? v : throw new AxCadException(0, "BAD_GEOMETRY", "value out of range");
    internal static double Positive(double v, string what) => Finite(v) > 0 ? v : throw new AxCadException(0, "BAD_GEOMETRY", $"{what} must be > 0");
    internal static double Deg(double rad) => Finite(rad) * 180 / Math.PI;
}

public sealed class LayerTable : IEnumerable<string>
{
    readonly Drawing _d;
    internal LayerTable(Drawing d) => _d = d;
    public int Count => _d.LayerNames.Count;
    public bool Has(string name) => _d.HasLayer(name);
    /// <summary>Adds a layer (made when the drawing is saved) and returns its name.</summary>
    public string Add(string name) => _d.AddLayer(name);
    public IEnumerator<string> GetEnumerator() => _d.LayerNames.ToList().GetEnumerator();
    IEnumerator IEnumerable.GetEnumerator() => GetEnumerator();
}

/// <summary>An editable entity. Geometry changes and Move/Rotate/Scale/Mirror work on lines, circles,
/// arcs, polylines, text and points; other kinds (dimensions, hatches, blocks ...) keep their shape
/// but can change layer and colour or be erased.</summary>
public class Entity
{
    readonly Drawing _d;
    internal JsonObject G;
    readonly string _baseJson;
    string _layer;
    int? _aci;
    string? _linetype;
    readonly string _baseLayer;
    readonly int? _baseAci;
    readonly string? _baseLinetype;

    internal Entity(Drawing d, string handle, string layer, JsonObject geom, int? aci, string? linetype, bool isNew)
    {
        _d = d;
        Handle = handle;
        G = geom;
        _baseJson = geom.ToJsonString();
        _layer = _baseLayer = layer;
        _aci = _baseAci = aci;
        _linetype = _baseLinetype = linetype;
        IsNew = isNew;
    }

    internal static Entity Create(Drawing d, string handle, string layer, JsonObject g, int? aci, string? lt, bool isNew) =>
        (g["type"]?.GetValue<string>()) switch
        {
            "LINE" => new Line(d, handle, layer, g, aci, lt, isNew),
            "CIRCLE" => new Circle(d, handle, layer, g, aci, lt, isNew),
            "ARC" => new Arc(d, handle, layer, g, aci, lt, isNew),
            "LWPOLYLINE" => new Polyline(d, handle, layer, g, aci, lt, isNew),
            "TEXT" => new DBText(d, handle, layer, g, aci, lt, isNew),
            "POINT" => new DBPoint(d, handle, layer, g, aci, lt, isNew),
            _ => new Entity(d, handle, layer, g, aci, lt, isNew),
        };

    public string Handle { get; }
    public string Type => G["type"]!.GetValue<string>();
    public string ObjectName => Type switch
    {
        "LINE" => "AcDbLine", "CIRCLE" => "AcDbCircle", "ARC" => "AcDbArc", "LWPOLYLINE" => "AcDbPolyline", "TEXT" => "AcDbText",
        "POINT" => "AcDbPoint", "ELLIPSE" => "AcDbEllipse", "SPLINE" => "AcDbSpline", "INSERT" => "AcDbBlockReference", "HATCH" => "AcDbHatch",
        _ => "AcDb" + Type,
    };
    public bool IsNew { get; }
    public bool IsErased { get; private set; }
    internal bool IsModified => G.ToJsonString() != _baseJson || _layer != _baseLayer || _aci != _baseAci || _linetype != _baseLinetype;

    public string Layer
    {
        get => _layer;
        set
        {
            Check();
            _d.Writable(value);
            _layer = _d.Layer(value);
        }
    }
    /// <summary>ACI colour 0..256 (256 = BYLAYER).</summary>
    public int Color
    {
        get => _aci ?? 256;
        set
        {
            Check();
            _aci = value is >= 0 and <= 256 ? value : throw new AxCadException(0, "BAD_COLOR", "colour index 0..256");
        }
    }
    public string Linetype
    {
        get => _linetype ?? "BYLAYER";
        set
        {
            Check();
            _linetype = value;
        }
    }

    internal readonly record struct State(string Geom, string Layer, int? Aci, string? Linetype, bool Erased);
    internal State Save() => new(G.ToJsonString(), _layer, _aci, _linetype, IsErased);
    internal void Load(State s)
    {
        G = JsonNode.Parse(s.Geom)!.AsObject();
        (_layer, _aci, _linetype, IsErased) = (s.Layer, s.Aci, s.Linetype, s.Erased);
    }

    protected void Check()
    {
        if (IsErased) throw new AxCadException(0, "ERASED", $"entity {Handle} is erased");
        _d.Writable(_layer);
    }

    public void Erase()
    {
        Check();
        IsErased = true;
    }

    // --- transforms (a point map plus what it does to angles, sizes and bulges) ---------------
    protected virtual void Map(Func<Point3d, Point3d> f, Func<double, double> angleDeg, double scale, bool flip) =>
        throw new AxCadException(0, "NOT_SUPPORTED", $"{ObjectName}: move it in the 2D screen");

    public void TransformBy(Func<Point3d, Point3d> f, Func<double, double> angleDeg, double scale, bool flip)
    {
        Check();
        Map(f, angleDeg, scale, flip);
    }
    public void Move(Point3d from, Point3d to) => TransformBy(p => new(p.X + to.X - from.X, p.Y + to.Y - from.Y), a => a, 1, false);
    /// <param name="angle">radians, counter-clockwise</param>
    public void Rotate(Point3d basePoint, double angle)
    {
        var (c, s) = (Math.Cos(angle), Math.Sin(angle));
        TransformBy(p => new(basePoint.X + (p.X - basePoint.X) * c - (p.Y - basePoint.Y) * s, basePoint.Y + (p.X - basePoint.X) * s + (p.Y - basePoint.Y) * c), a => a + angle * 180 / Math.PI, 1, false);
    }
    public void Scale(Point3d basePoint, double factor)
    {
        ModelSpace.Positive(factor, "factor");
        TransformBy(p => new(basePoint.X + (p.X - basePoint.X) * factor, basePoint.Y + (p.Y - basePoint.Y) * factor), a => a, factor, false);
    }
    /// <summary>A mirrored copy across the line p1-p2 (AutoCAD keeps the original).</summary>
    public Entity Mirror(Point3d p1, Point3d p2)
    {
        var copy = Copy();
        var (ux, uy) = (p2.X - p1.X, p2.Y - p1.Y);
        var len = Math.Sqrt(ux * ux + uy * uy);
        if (len == 0) throw new AxCadException(0, "BAD_GEOMETRY", "mirror line needs two different points");
        (ux, uy) = (ux / len, uy / len);
        var phi = Math.Atan2(uy, ux) * 180 / Math.PI;
        copy.TransformBy(p =>
        {
            var (vx, vy) = (p.X - p1.X, p.Y - p1.Y);
            var d = 2 * (vx * ux + vy * uy);
            return new(p1.X + d * ux - vx, p1.Y + d * uy - vy);
        }, a => 2 * phi - a, 1, true);
        return copy;
    }
    public Entity Copy()
    {
        Check();
        return _d.Add(Create(_d, $"new-{Guid.NewGuid():N}", _layer, (JsonObject)G.DeepClone(), _aci, _linetype, isNew: true));
    }

    internal JsonObject Edit(bool withHandle, bool onlyChanged)
    {
        var o = new JsonObject();
        if (withHandle) o["handle"] = Handle;
        o["layer"] = _layer;
        o["geom"] = G.DeepClone();
        if (_aci is int c && (!onlyChanged || _aci != _baseAci)) o["color"] = c;
        if (_linetype is string lt && (!onlyChanged || _linetype != _baseLinetype)) o["linetype"] = lt;
        return o;
    }

    protected Point3d P(string key) => Point3d.From(G[key]);
    protected double D(string key) => G[key]!.GetValue<double>();
    protected void Set(string key, Point3d p)
    {
        Check();
        G[key] = new JsonArray(ModelSpace.Finite(p.X), ModelSpace.Finite(p.Y));
    }
    protected void Set(string key, double v)
    {
        Check();
        G[key] = ModelSpace.Finite(v);
    }
    protected static double Norm(double deg) => ((deg % 360) + 360) % 360;
}

public sealed class Line : Entity
{
    internal Line(Drawing d, string h, string l, JsonObject g, int? a, string? lt, bool n) : base(d, h, l, g, a, lt, n) { }
    public Point3d StartPoint { get => P("start"); set => Set("start", value); }
    public Point3d EndPoint { get => P("end"); set => Set("end", value); }
    public double Length => StartPoint.DistanceTo(EndPoint);
    public double Angle => (Math.Atan2(EndPoint.Y - StartPoint.Y, EndPoint.X - StartPoint.X) + 2 * Math.PI) % (2 * Math.PI);
    protected override void Map(Func<Point3d, Point3d> f, Func<double, double> a, double k, bool flip)
    {
        var (s, e) = (f(StartPoint), f(EndPoint));
        StartPoint = s;
        EndPoint = e;
    }
}

public sealed class Circle : Entity
{
    internal Circle(Drawing d, string h, string l, JsonObject g, int? a, string? lt, bool n) : base(d, h, l, g, a, lt, n) { }
    public Point3d Center { get => P("center"); set => Set("center", value); }
    public double Radius { get => D("radius"); set => Set("radius", ModelSpace.Positive(value, "radius")); }
    public double Diameter => 2 * Radius;
    public double Area => Math.PI * Radius * Radius;
    public double Circumference => 2 * Math.PI * Radius;
    protected override void Map(Func<Point3d, Point3d> f, Func<double, double> a, double k, bool flip)
    {
        Center = f(Center);
        Radius *= k;
    }
}

public sealed class Arc : Entity
{
    internal Arc(Drawing d, string h, string l, JsonObject g, int? a, string? lt, bool n) : base(d, h, l, g, a, lt, n) { }
    public Point3d Center { get => P("center"); set => Set("center", value); }
    public double Radius { get => D("radius"); set => Set("radius", ModelSpace.Positive(value, "radius")); }
    /// <summary>radians</summary>
    public double StartAngle { get => D("start_angle") * Math.PI / 180; set => Set("start_angle", ModelSpace.Deg(value)); }
    /// <summary>radians</summary>
    public double EndAngle { get => D("end_angle") * Math.PI / 180; set => Set("end_angle", ModelSpace.Deg(value)); }
    public double TotalAngle => (Norm(D("end_angle") - D("start_angle")) is var s && s == 0 ? 360 : Norm(D("end_angle") - D("start_angle"))) * Math.PI / 180;
    public double Length => Radius * TotalAngle;
    protected override void Map(Func<Point3d, Point3d> f, Func<double, double> a, double k, bool flip)
    {
        var (s, e) = (D("start_angle"), D("end_angle"));
        Center = f(Center);
        Radius *= k;
        // a reflection turns the arc's direction: its ends swap
        Set("start_angle", Norm(a(flip ? e : s)));
        Set("end_angle", Norm(a(flip ? s : e)));
    }
}

public sealed class Polyline : Entity
{
    internal Polyline(Drawing d, string h, string l, JsonObject g, int? a, string? lt, bool n) : base(d, h, l, g, a, lt, n) { }
    JsonArray Pts => G["points"]!.AsArray();
    public int NumberOfVertices => Pts.Count;
    public Point3d GetPointAt(int i) => new(Pts[i]![0]!.GetValue<double>(), Pts[i]![1]!.GetValue<double>());
    public double GetBulgeAt(int i) => Pts[i]![2]!.GetValue<double>();
    /// <summary>Bulge = tan(arc angle / 4) of the segment starting at vertex i; 0 is straight, 1 a half circle.</summary>
    public void SetBulgeAt(int i, double bulge)
    {
        Check();
        Pts[i]![2] = ModelSpace.Finite(bulge);
    }
    public bool Closed
    {
        get => G["closed"]!.GetValue<bool>();
        set
        {
            Check();
            G["closed"] = value;
        }
    }
    /// <summary>x0, y0, x1, y1, ...</summary>
    public double[] Coordinates => Enumerable.Range(0, NumberOfVertices).SelectMany(i => new[] { GetPointAt(i).X, GetPointAt(i).Y }).ToArray();

    IEnumerable<(Point3d A, Point3d B, double Bulge)> Segments()
    {
        var n = NumberOfVertices;
        for (var i = 0; i < (Closed ? n : n - 1); i++) yield return (GetPointAt(i), GetPointAt((i + 1) % n), GetBulgeAt(i));
    }
    /// <summary>Length with arc segments (bulges).</summary>
    public double Length => Segments().Sum(s =>
    {
        var c = s.A.DistanceTo(s.B);
        if (s.Bulge == 0 || c == 0) return c;
        var theta = 4 * Math.Atan(Math.Abs(s.Bulge));
        return c / (2 * Math.Sin(theta / 2)) * theta;
    });
    /// <summary>Area of a closed polyline, arc segments included.</summary>
    public double Area
    {
        get
        {
            if (!Closed) return 0;
            double a = 0;
            foreach (var (p, q, b) in Segments())
            {
                a += (p.X * q.Y - q.X * p.Y) / 2;
                if (b == 0) continue;
                var c = p.DistanceTo(q);
                var theta = 4 * Math.Atan(b);
                var r = c / (2 * Math.Sin(Math.Abs(theta) / 2));
                a += Math.Sign(b) * r * r * (Math.Abs(theta) - Math.Sin(Math.Abs(theta))) / 2;
            }
            return Math.Abs(a);
        }
    }
    protected override void Map(Func<Point3d, Point3d> f, Func<double, double> a, double k, bool flip)
    {
        var next = new JsonArray();
        for (var i = 0; i < NumberOfVertices; i++)
        {
            var p = f(GetPointAt(i));
            next.Add(new JsonArray(ModelSpace.Finite(p.X), ModelSpace.Finite(p.Y), flip ? -GetBulgeAt(i) : GetBulgeAt(i)));
        }
        Check();
        G["points"] = next;
    }
}

public sealed class DBText : Entity
{
    internal DBText(Drawing d, string h, string l, JsonObject g, int? a, string? lt, bool n) : base(d, h, l, g, a, lt, n) { }
    public string TextString
    {
        get => G["value"]!.GetValue<string>();
        set
        {
            Check();
            G["value"] = value;
        }
    }
    public Point3d Position { get => P("insert"); set => Set("insert", value); }
    public double Height { get => D("height"); set => Set("height", ModelSpace.Positive(value, "height")); }
    /// <summary>radians</summary>
    public double Rotation { get => D("rotation") * Math.PI / 180; set => Set("rotation", Norm(ModelSpace.Deg(value))); }
    protected override void Map(Func<Point3d, Point3d> f, Func<double, double> a, double k, bool flip)
    {
        Position = f(Position);
        Height *= k;
        if (!flip) Set("rotation", Norm(a(D("rotation")))); // mirrored text stays readable (MIRRTEXT 0)
    }
}

public sealed class DBPoint : Entity
{
    internal DBPoint(Drawing d, string h, string l, JsonObject g, int? a, string? lt, bool n) : base(d, h, l, g, a, lt, n) { }
    public Point3d Position { get => P("point"); set => Set("point", value); }
    protected override void Map(Func<Point3d, Point3d> f, Func<double, double> a, double k, bool flip) => Position = f(Position);
}
