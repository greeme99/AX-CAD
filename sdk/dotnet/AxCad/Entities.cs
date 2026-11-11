// W8 (plan §22.4): entities shaped like Autodesk.AutoCAD.DatabaseServices. Each keeps the edit
// API's geometry (mm, degrees inside; the properties take radians like AutoCAD). An entity made with
// `new` is free to set up; once appended it changes only when opened ForWrite in a transaction.
using System.Collections;
using System.Text.Json.Nodes;
using static AxCad.ModelSpaceChecks;

namespace AxCad;

public abstract class Entity : DBObject
{
    protected Entity(JsonObject geom) : base(new JsonObject { ["layer"] = "0", ["geom"] = geom }) { }
    internal JsonObject G => (JsonObject)S["geom"]!;
    internal string GeomType => G["type"]!.GetValue<string>();

    internal static Entity FromPayload(JsonObject e)
    {
        var g = (JsonObject)e["geom"]!;
        Entity x = g["type"]!.GetValue<string>() switch
        {
            "LINE" => new Line(),
            "CIRCLE" => new Circle(),
            "ARC" => new Arc(),
            "LWPOLYLINE" => new Polyline(),
            "POINT" => new DBPoint(),
            "TEXT" => new DBText(),
            "MTEXT" => new MText(),
            "ELLIPSE" => new Ellipse(),
            "SPLINE" => new Spline(),
            "HATCH" => new Hatch(),
            "LEADER" => new Leader(),
            "MULTILEADER" => new MLeader(),
            "INSERT" => new BlockReference(),
            "DIM_ALIGNED" => new AlignedDimension(),
            "DIM_LINEAR" => new RotatedDimension(),
            "DIM_RADIUS" => new RadialDimension(),
            "DIM_DIAMETER" => new DiametricDimension(),
            "DIM_ANGULAR" => new LineAngularDimension2(),
            var t => throw new AxCadException(0, "BAD_GEOMETRY", $"unknown entity type {t}"),
        };
        x.S["geom"] = g.DeepClone();
        x.S["layer"] = e["layer"]!.GetValue<string>();
        foreach (var k in new[] { "aci", "linetype", "lwt", "tc", "tr", "inv", "lts", "xd", "att" })
            if (e[k] is { } v) x.S[k] = v.DeepClone();
        _ = x.ExtentPoints().ToList(); // W8 review M3: a geometry missing its fields fails here (BAD_RENDER), not later
        return x;
    }
    internal void LoadExtensionDictionary(Database db, JsonObject tree)
    {
        var d = db.Register(new DBDictionary { ExtensionOf = this }, null);
        d.OwnerId = ObjectId;
        d.LoadTree(db, tree);
        S["xdict"] = d.Index;
    }

    // --- common properties -------------------------------------------------------------------
    public string Layer
    {
        get => S["layer"]!.GetValue<string>();
        set
        {
            AssertWriteEnabled();
            S["layer"] = Db is null ? value : LayerOf(Db, value).Name;
        }
    }
    public ObjectId LayerId
    {
        get => Db is not null && Db.Layers.Has(Layer) ? Db.Layers[Layer] : ObjectId.Null;
        set => Layer = ((LayerTableRecord)value.Target).Name;
    }
    static LayerTableRecord LayerOf(Database db, string name)
    {
        if (!db.Layers.Has(name)) throw new AxCadException(0, "NO_LAYER", $"no layer {name} in the drawing");
        var l = (LayerTableRecord)db.Layers[name].Target;
        if (l.IsLocked) throw new AxCadException(0, "LAYER_LOCKED", $"layer {l.Name} is locked");
        return l;
    }
    internal void CheckLayer(Database db) => S["layer"] = LayerOf(db, Layer).Name;
    internal override void CheckWritable()
    {
        base.CheckWritable();
        if (Db is not null && OwnerId.Target is BlockTableRecord { IsLayout: true }) LayerOf(Db, Layer); // AutoCAD's eOnLockedLayer
    }
    /// <summary>ACI colour 0..256 (256 = BYLAYER, 0 = BYBLOCK).</summary>
    public int ColorIndex
    {
        get => S["aci"]?.GetValue<int>() ?? 256;
        set
        {
            AssertWriteEnabled();
            S["aci"] = value is >= 0 and <= 256 ? value : throw new AxCadException(0, "BAD_COLOR", "colour index 0..256");
        }
    }
    public string Linetype
    {
        get => S["linetype"]?.GetValue<string>() ?? "BYLAYER";
        set
        {
            AssertWriteEnabled();
            S["linetype"] = value;
        }
    }
    public double LinetypeScale
    {
        get => S["lts"]?.GetValue<double>() ?? 1;
        set
        {
            AssertWriteEnabled();
            S["lts"] = Positive(value, "LinetypeScale");
        }
    }
    /// <summary>1/100 mm; -1 BYLAYER, -2 BYBLOCK, -3 default.</summary>
    public int LineWeight
    {
        get => S["lwt"]?.GetValue<int>() ?? -1;
        set
        {
            AssertWriteEnabled();
            S["lwt"] = LineWeight(value, layer: false);
        }
    }
    /// <summary>Transparency in percent 0..90; null = BYLAYER.</summary>
    public int? Transparency
    {
        get => S["tr"]?.GetValue<int>();
        set
        {
            AssertWriteEnabled();
            S["tr"] = value is null or (>= 0 and <= 90) ? value : throw new AxCadException(0, "BAD_VALUE", "transparency 0..90 %");
        }
    }
    public bool Visible
    {
        get => S["inv"]?.GetValue<bool>() != true;
        set
        {
            AssertWriteEnabled();
            S["inv"] = !value;
        }
    }

    // --- geometry ----------------------------------------------------------------------------
    protected Point3d P(string key) => P(G[key]!);
    protected static Point3d P(JsonNode n) => new(n[0]!.GetValue<double>(), n[1]!.GetValue<double>());
    protected double D(string key) => G[key]!.GetValue<double>();
    protected static JsonArray J(Point3d p) => new(Finite(p.X), Finite(p.Y));
    protected void SetG(string key, JsonNode? v)
    {
        AssertWriteEnabled();
        G[key] = v;
    }
    protected void SetP(string key, Point3d p) => SetG(key, J(p));
    protected static double Ang(double deg) => Rad(deg);

    /// <summary>The points the entity's box is taken from.</summary>
    internal abstract IEnumerable<Point3d> ExtentPoints();
    public Extents3d GeometricExtents => Extents3d.Of(ExtentPoints());
    /// <summary>Moves, turns, scales (uniformly) or mirrors the entity in the XY plane.</summary>
    public void TransformBy(Matrix3d m)
    {
        AssertWriteEnabled();
        var (k, a, flip) = m.As2D();
        Map(m, k, a, flip);
    }
    internal abstract void Map(Matrix3d m, double k, double angDeg, bool flip);
    /// <summary>Angle (degrees) after the transform: turned, or reflected then turned.</summary>
    protected static double MapAngle(double x, double a, bool flip) => Norm(flip ? a - x : a + x);
    public Entity GetTransformedCopy(Matrix3d m)
    {
        var c = (Entity)Clone();
        c.TransformBy(m);
        return c;
    }
    /// <summary>A copy not in any database (AutoCAD's Clone): append it to keep it.</summary>
    public object Clone()
    {
        var c = (Entity)MemberwiseClone();
        c.S = (JsonObject)S.DeepClone();
        c.S.Remove("erased");
        c.S.Remove("xdict");
        c.S.Remove("arefs");
        c.Detach();
        c.OwnerId = ObjectId.Null;
        return c;
    }

    // --- the edit request --------------------------------------------------------------------
    internal virtual JsonObject Geom() => (JsonObject)G.DeepClone();
    static readonly (string Key, string Api, Func<JsonNode, JsonNode> F)[] Props =
    [
        ("aci", "color", v => v.DeepClone()), ("linetype", "linetype", v => v.DeepClone()), ("lwt", "lineweight", v => v.DeepClone()),
        ("tc", "true_color", v => v.DeepClone()), ("tr", "transparency", v => v.DeepClone()), ("lts", "ltscale", v => v.DeepClone()),
        ("inv", "visible", v => !v.GetValue<bool>()), ("xd", "xdata", v => v.DeepClone()), ("att", "attributes", v => v.DeepClone()),
    ];
    internal JsonObject Edit(bool withHandle, bool onlyChanged)
    {
        var o = new JsonObject();
        if (withHandle) o["handle"] = FileHandle;
        o["layer"] = Layer;
        o["geom"] = Geom();
        var b = onlyChanged ? Base : [];
        foreach (var (k, api, f) in Props)
        {
            if (S[k] is not { } v) continue;
            if (onlyChanged && v.ToJsonString() == b[k]?.ToJsonString()) continue;
            if (k == "att" && GeomType != "INSERT") continue;
            o[api] = f(v);
        }
        return o;
    }
    internal override void Emit(JsonObject body)
    {
        if (OwnerId.IsNull || OwnerId.Target is not BlockTableRecord { IsLayout: true }) return; // block contents go with their block
        if (IsNewObject)
        {
            if (!IsErased) ((JsonArray)body["created"]!).Add(Edit(withHandle: false, onlyChanged: false));
        }
        else if (IsErased) ((JsonArray)body["deleted"]!).Add(FileHandle);
        else if (IsModified) ((JsonArray)body["modified"]!).Add(Edit(withHandle: true, onlyChanged: true));
    }
}

/// <summary>A curve: what has a start and an end.</summary>
public abstract class Curve : Entity
{
    protected Curve(JsonObject g) : base(g) { }
    public abstract Point3d StartPoint { get; set; }
    public abstract Point3d EndPoint { get; set; }
    public virtual bool Closed => false;
    public virtual double Area => 0;
}

public sealed class Line : Curve
{
    internal Line() : base(new JsonObject { ["type"] = "LINE", ["start"] = J(Point3d.Origin), ["end"] = J(Point3d.Origin) }) { }
    public Line(Point3d start, Point3d end) : this() => (StartPoint, EndPoint) = (start, end);
    public override string DxfName => "LINE";
    public override Point3d StartPoint { get => P("start"); set => SetP("start", value); }
    public override Point3d EndPoint { get => P("end"); set => SetP("end", value); }
    public double Length => StartPoint.DistanceTo(EndPoint);
    public double Angle => (Math.Atan2(EndPoint.Y - StartPoint.Y, EndPoint.X - StartPoint.X) + 2 * Math.PI) % (2 * Math.PI);
    public Vector3d Delta => EndPoint - StartPoint;
    internal override IEnumerable<Point3d> ExtentPoints() => [StartPoint, EndPoint];
    internal override void Map(Matrix3d m, double k, double a, bool flip) => (StartPoint, EndPoint) = (m * StartPoint, m * EndPoint);
}

public sealed class Circle : Curve
{
    internal Circle() : base(new JsonObject { ["type"] = "CIRCLE", ["center"] = J(Point3d.Origin), ["radius"] = 1.0 }) { }
    public Circle(Point3d center, Vector3d normal, double radius) : this()
    {
        Planar(normal);
        (Center, Radius) = (center, radius);
    }
    internal static void Planar(Vector3d n)
    {
        if (n.X != 0 || n.Y != 0 || n.Z <= 0) throw new AxCadException(0, "NOT_SUPPORTED", "AX-CAD 2D entities lie in the XY plane (normal 0,0,1)");
    }
    public override string DxfName => "CIRCLE";
    public Point3d Center { get => P("center"); set => SetP("center", value); }
    public double Radius { get => D("radius"); set => SetG("radius", Positive(value, "radius")); }
    public double Diameter { get => 2 * Radius; set => Radius = value / 2; }
    public double Circumference => 2 * Math.PI * Radius;
    public override double Area => Math.PI * Radius * Radius;
    public override bool Closed => true;
    public Vector3d Normal => Vector3d.ZAxis;
    public override Point3d StartPoint { get => Center + new Vector3d(Radius, 0); set => throw new AxCadException(0, "NOT_SUPPORTED", "a circle's start is set by its center and radius"); }
    public override Point3d EndPoint { get => StartPoint; set => throw new AxCadException(0, "NOT_SUPPORTED", "a circle's end is set by its center and radius"); }
    internal override IEnumerable<Point3d> ExtentPoints() => [Center - new Vector3d(Radius, Radius), Center + new Vector3d(Radius, Radius)];
    internal override void Map(Matrix3d m, double k, double a, bool flip)
    {
        var (c, r) = (m * Center, Positive(Radius * k, "radius")); // checked first: nothing half-moved
        (Center, Radius) = (c, r);
    }
}

public sealed class Arc : Curve
{
    internal Arc() : base(new JsonObject { ["type"] = "ARC", ["center"] = J(Point3d.Origin), ["radius"] = 1.0, ["start_angle"] = 0.0, ["end_angle"] = 90.0 }) { }
    /// <param name="startAngle">radians, counter-clockwise from the X axis</param>
    public Arc(Point3d center, double radius, double startAngle, double endAngle) : this() => (Center, Radius, StartAngle, EndAngle) = (center, radius, startAngle, endAngle);
    public Arc(Point3d center, Vector3d normal, double radius, double startAngle, double endAngle) : this(center, radius, startAngle, endAngle) => Circle.Planar(normal);
    public override string DxfName => "ARC";
    public Point3d Center { get => P("center"); set => SetP("center", value); }
    public double Radius { get => D("radius"); set => SetG("radius", Positive(value, "radius")); }
    public double StartAngle { get => Ang(D("start_angle")); set => SetG("start_angle", Norm(Deg(value))); }
    public double EndAngle { get => Ang(D("end_angle")); set => SetG("end_angle", Norm(Deg(value))); }
    public double TotalAngle => Ang(Norm(D("end_angle") - D("start_angle")) is var s && s == 0 ? 360 : s);
    public double Length => Radius * TotalAngle;
    public override double Area => Radius * Radius * (TotalAngle - Math.Sin(TotalAngle)) / 2;
    Point3d At(double a) => Center + new Vector3d(Radius * Math.Cos(a), Radius * Math.Sin(a));
    public override Point3d StartPoint { get => At(StartAngle); set => throw new AxCadException(0, "NOT_SUPPORTED", "set StartAngle"); }
    public override Point3d EndPoint { get => At(EndAngle); set => throw new AxCadException(0, "NOT_SUPPORTED", "set EndAngle"); }
    internal override IEnumerable<Point3d> ExtentPoints()
    {
        yield return StartPoint;
        yield return EndPoint;
        for (var q = 0; q < 4; q++) // the quadrant points the arc passes
            if (Norm(q * 90 - D("start_angle")) <= Norm(D("end_angle") - D("start_angle"))) yield return At(q * Math.PI / 2);
    }
    internal override void Map(Matrix3d m, double k, double a, bool flip)
    {
        var (s, e) = (D("start_angle"), D("end_angle"));
        (Center, Radius) = (m * Center, Radius * k);
        SetG("start_angle", MapAngle(flip ? e : s, a, flip)); // a reflection turns the arc: its ends swap
        SetG("end_angle", MapAngle(flip ? s : e, a, flip));
    }
}

/// <summary>A lightweight polyline: AddVertexAt, bulges (tan of a quarter of the arc angle).</summary>
public sealed class Polyline : Curve
{
    public Polyline() : base(new JsonObject { ["type"] = "LWPOLYLINE", ["points"] = new JsonArray(), ["closed"] = false }) { }
    public Polyline(int vertices) : this() { }
    public override string DxfName => "LWPOLYLINE";
    JsonArray Pts => (JsonArray)G["points"]!;
    public int NumberOfVertices => Pts.Count;
    public void AddVertexAt(int index, Point2d pt, double bulge, double startWidth, double endWidth)
    {
        AssertWriteEnabled();
        if (startWidth != 0 || endWidth != 0) throw new AxCadException(0, "NOT_SUPPORTED", "AX-CAD polylines have no widths (use a lineweight)");
        if (index < 0 || index > Pts.Count) throw new ArgumentOutOfRangeException(nameof(index));
        Pts.Insert(index, new JsonArray(Finite(pt.X), Finite(pt.Y), Finite(bulge)));
    }
    public void RemoveVertexAt(int index)
    {
        AssertWriteEnabled();
        Pts.RemoveAt(index);
    }
    public Point2d GetPoint2dAt(int i) => new(Pts[i]![0]!.GetValue<double>(), Pts[i]![1]!.GetValue<double>());
    public Point3d GetPoint3dAt(int i) => GetPoint2dAt(i).Convert3d();
    public void SetPointAt(int i, Point2d p)
    {
        AssertWriteEnabled();
        Pts[i] = new JsonArray(Finite(p.X), Finite(p.Y), GetBulgeAt(i));
    }
    public double GetBulgeAt(int i) => Pts[i]![2]!.GetValue<double>();
    public void SetBulgeAt(int i, double bulge)
    {
        AssertWriteEnabled();
        Pts[i]![2] = Finite(bulge);
    }
    public override bool Closed => G["closed"]!.GetValue<bool>();
    public void SetClosed(bool closed) => SetG("closed", closed);
    /// <summary>AutoCAD's Closed setter (the property is read/write there).</summary>
    public bool IsClosed { get => Closed; set => SetClosed(value); }
    public override Point3d StartPoint { get => GetPoint3dAt(0); set => SetPointAt(0, value.Convert2d()); }
    public override Point3d EndPoint { get => GetPoint3dAt(NumberOfVertices - 1); set => SetPointAt(NumberOfVertices - 1, value.Convert2d()); }

    IEnumerable<(Point2d A, Point2d B, double Bulge)> Segments()
    {
        var n = NumberOfVertices;
        for (var i = 0; i < (Closed ? n : n - 1); i++) yield return (GetPoint2dAt(i), GetPoint2dAt((i + 1) % n), GetBulgeAt(i));
    }
    public double Length => Segments().Sum(s =>
    {
        var c = s.A.GetDistanceTo(s.B);
        if (s.Bulge == 0 || c == 0) return c;
        var theta = 4 * Math.Atan(Math.Abs(s.Bulge));
        return c / (2 * Math.Sin(theta / 2)) * theta;
    });
    public override double Area
    {
        get
        {
            if (!Closed) return 0;
            double a = 0;
            foreach (var (p, q, b) in Segments())
            {
                a += (p.X * q.Y - q.X * p.Y) / 2;
                if (b == 0) continue;
                var c = p.GetDistanceTo(q);
                var theta = 4 * Math.Atan(b);
                var r = c / (2 * Math.Sin(Math.Abs(theta) / 2));
                a += Math.Sign(b) * r * r * (Math.Abs(theta) - Math.Sin(Math.Abs(theta))) / 2;
            }
            return Math.Abs(a);
        }
    }
    internal override JsonObject Geom()
    {
        if (NumberOfVertices < 2) throw new AxCadException(0, "BAD_GEOMETRY", "a polyline needs two vertices");
        return base.Geom();
    }
    internal override IEnumerable<Point3d> ExtentPoints() => Enumerable.Range(0, NumberOfVertices).Select(GetPoint3dAt);
    internal override void Map(Matrix3d m, double k, double a, bool flip)
    {
        var next = new JsonArray();
        for (var i = 0; i < NumberOfVertices; i++)
        {
            var p = m * GetPoint3dAt(i);
            next.Add(new JsonArray(Finite(p.X), Finite(p.Y), flip ? -GetBulgeAt(i) : GetBulgeAt(i)));
        }
        SetG("points", next);
    }
}

public sealed class DBPoint : Entity
{
    internal DBPoint() : base(new JsonObject { ["type"] = "POINT", ["point"] = J(Point3d.Origin) }) { }
    public DBPoint(Point3d position) : this() => Position = position;
    public override string DxfName => "POINT";
    public Point3d Position { get => P("point"); set => SetP("point", value); }
    internal override IEnumerable<Point3d> ExtentPoints() => [Position];
    internal override void Map(Matrix3d m, double k, double a, bool flip) => Position = m * Position;
}

public sealed class DBText : Entity
{
    public DBText() : base(new JsonObject { ["type"] = "TEXT", ["insert"] = J(Point3d.Origin), ["height"] = 2.5, ["value"] = "", ["rotation"] = 0.0 }) { }
    public override string DxfName => "TEXT";
    public string TextString { get => G["value"]!.GetValue<string>(); set => SetG("value", Line1(value)); }
    public Point3d Position { get => P("insert"); set => SetP("insert", value); }
    public double Height { get => D("height"); set => SetG("height", Positive(value, "height")); }
    public double Rotation { get => Ang(D("rotation")); set => SetG("rotation", Norm(Deg(value))); }
    internal static string Line1(string s) => s.Length <= 2049 && !s.Any(c => c < ' ' || c is >= '\x7f' and <= '\x9f') ? s : throw new AxCadException(0, "BAD_TEXT", "one line of 2049 characters at most");
    internal override IEnumerable<Point3d> ExtentPoints() => [Position, Position + new Vector3d(TextString.Length * Height * 0.8, Height)];
    internal override void Map(Matrix3d m, double k, double a, bool flip)
    {
        (Position, Height) = (m * Position, Height * k);
        if (!flip) SetG("rotation", MapAngle(D("rotation"), a, false)); // mirrored text stays readable (MIRRTEXT 0)
    }
}

public enum AttachmentPoint { TopLeft = 1, TopCenter, TopRight, MiddleLeft, MiddleCenter, MiddleRight, BottomLeft, BottomCenter, BottomRight }

public sealed class MText : Entity
{
    public MText() : base(new JsonObject { ["type"] = "MTEXT", ["insert"] = J(Point3d.Origin), ["height"] = 2.5, ["width"] = 0.0, ["value"] = "", ["rotation"] = 0.0, ["attachment"] = 1 }) { }
    public override string DxfName => "MTEXT";
    /// <summary>The text with MTEXT codes (\P = new paragraph).</summary>
    public string Contents { get => G["value"]!.GetValue<string>(); set => SetG("value", value.Length <= 65536 ? value : throw new AxCadException(0, "BAD_TEXT", "64K characters at most")); }
    public string Text => Contents.Replace("\\P", "\n");
    public Point3d Location { get => P("insert"); set => SetP("insert", value); }
    public double TextHeight { get => D("height"); set => SetG("height", Positive(value, "TextHeight")); }
    /// <summary>0 = no wrapping.</summary>
    public double Width { get => D("width"); set => SetG("width", value >= 0 ? Finite(value) : throw new AxCadException(0, "BAD_GEOMETRY", "width >= 0")); }
    public double Rotation { get => Ang(D("rotation")); set => SetG("rotation", Norm(Deg(value))); }
    public AttachmentPoint Attachment { get => (AttachmentPoint)G["attachment"]!.GetValue<int>(); set => SetG("attachment", (int)value is >= 1 and <= 9 ? (int)value : throw new AxCadException(0, "BAD_VALUE", "attachment 1..9")); }
    internal override IEnumerable<Point3d> ExtentPoints() => [Location, Location + new Vector3d(Math.Max(Width, TextHeight), -TextHeight)];
    internal override void Map(Matrix3d m, double k, double a, bool flip)
    {
        (Location, TextHeight, Width) = (m * Location, TextHeight * k, Width * k);
        if (!flip) SetG("rotation", MapAngle(D("rotation"), a, false));
    }
}

public sealed class Ellipse : Curve
{
    internal Ellipse() : base(new JsonObject { ["type"] = "ELLIPSE", ["center"] = J(Point3d.Origin), ["major"] = new JsonArray(1.0, 0.0), ["ratio"] = 1.0, ["start_param"] = 0.0, ["end_param"] = 2 * Math.PI }) { }
    /// <param name="startAngle">parameter (radians) on the ellipse; 0 and 2π for a whole one</param>
    public Ellipse(Point3d center, Vector3d unitNormal, Vector3d majorAxis, double radiusRatio, double startAngle, double endAngle) : this()
    {
        Circle.Planar(unitNormal);
        if (majorAxis.IsZeroLength() || radiusRatio is <= 0 or > 1) throw new AxCadException(0, "BAD_GEOMETRY", "a major axis and a ratio in (0, 1]");
        G["center"] = J(center);
        G["major"] = new JsonArray(Finite(majorAxis.X), Finite(majorAxis.Y));
        G["ratio"] = radiusRatio;
        G["start_param"] = Finite(startAngle);
        G["end_param"] = Finite(endAngle);
    }
    public override string DxfName => "ELLIPSE";
    public Point3d Center { get => P("center"); set => SetP("center", value); }
    public Vector3d MajorAxis => new(G["major"]![0]!.GetValue<double>(), G["major"]![1]!.GetValue<double>());
    public Vector3d MinorAxis => new(-MajorAxis.Y * RadiusRatio, MajorAxis.X * RadiusRatio);
    public double RadiusRatio { get => D("ratio"); set => SetG("ratio", value is > 0 and <= 1 ? value : throw new AxCadException(0, "BAD_GEOMETRY", "ratio in (0, 1]")); }
    public double StartParam { get => D("start_param"); set => SetG("start_param", Finite(value)); }
    public double EndParam { get => D("end_param"); set => SetG("end_param", Finite(value)); }
    Point3d At(double t) => Center + MajorAxis * Math.Cos(t) + MinorAxis * Math.Sin(t);
    public override Point3d StartPoint { get => At(StartParam); set => throw new AxCadException(0, "NOT_SUPPORTED", "set StartParam"); }
    public override Point3d EndPoint { get => At(EndParam); set => throw new AxCadException(0, "NOT_SUPPORTED", "set EndParam"); }
    public override bool Closed => Math.Abs(Math.Abs(EndParam - StartParam) - 2 * Math.PI) < 1e-9;
    public override double Area => Closed ? Math.PI * MajorAxis.Length * MinorAxis.Length : 0;
    internal override IEnumerable<Point3d> ExtentPoints() => Enumerable.Range(0, 73).Select(i => At(StartParam + (EndParam - StartParam) * i / 72));
    internal override void Map(Matrix3d m, double k, double a, bool flip)
    {
        var (s, e) = (StartParam, EndParam);
        Center = m * Center;
        var mj = m.Apply(MajorAxis);
        SetG("major", new JsonArray(Finite(mj.X), Finite(mj.Y)));
        if (flip) (StartParam, EndParam) = (-e, -s); // the direction turns: the ends swap
    }
}

public sealed class Spline : Curve
{
    internal Spline() : base(new JsonObject { ["type"] = "SPLINE", ["fit"] = new JsonArray() }) { }
    /// <summary>A curve through the fit points (the server builds the exact CAD spline).</summary>
    public Spline(IEnumerable<Point3d> fitPoints, int order = 4, double fitTolerance = 0) : this()
    {
        var fit = new JsonArray(fitPoints.Select(p => (JsonNode)J(p)).ToArray());
        if (fit.Count < 2 || fit.Count > 10_000) throw new AxCadException(0, "BAD_GEOMETRY", "2..10,000 fit points");
        G["fit"] = fit;
    }
    public override string DxfName => "SPLINE";
    JsonArray Fit => (JsonArray)G["fit"]!;
    public int NumFitPoints => Fit.Count;
    public Point3d GetFitPointAt(int i) => P(Fit[i]!);
    public void SetFitPointAt(int i, Point3d p)
    {
        AssertWriteEnabled();
        Fit[i] = J(p);
    }
    public override Point3d StartPoint { get => GetFitPointAt(0); set => SetFitPointAt(0, value); }
    public override Point3d EndPoint { get => GetFitPointAt(NumFitPoints - 1); set => SetFitPointAt(NumFitPoints - 1, value); }
    internal override IEnumerable<Point3d> ExtentPoints() => Enumerable.Range(0, NumFitPoints).Select(GetFitPointAt);
    internal override void Map(Matrix3d m, double k, double a, bool flip) => SetG("fit", new JsonArray(Enumerable.Range(0, NumFitPoints).Select(i => (JsonNode)J(m * GetFitPointAt(i))).ToArray()));
}

public enum HatchPatternType { UserDefined = 0, PreDefined = 1, CustomDefined = 2 }
[Flags] public enum HatchLoopTypes { Default = 0, External = 1, Polyline = 2, Derived = 4, Textbox = 8, Outermost = 16 }

/// <summary>A hatch with one closed boundary (AppendLoop with a closed polyline or a circle).</summary>
public sealed class Hatch : Entity
{
    public Hatch() : base(new JsonObject { ["type"] = "HATCH", ["boundary"] = new JsonArray(), ["pattern"] = "SOLID", ["scale"] = 1.0, ["angle"] = 0.0 }) { }
    public override string DxfName => "HATCH";
    public string PatternName => G["pattern"]!.GetValue<string>();
    public void SetHatchPattern(HatchPatternType type, string name)
    {
        AssertWriteEnabled();
        if (type != HatchPatternType.PreDefined || name.Length is 0 or > 64 || name.Any(c => !char.IsLetterOrDigit(c) && c != '_' && c != '-')) throw new AxCadException(0, "BAD_PATTERN", "a predefined pattern name (SOLID, ANSI31 ...)");
        G["pattern"] = name.ToUpperInvariant();
    }
    public double PatternScale { get => D("scale"); set => SetG("scale", Positive(value, "PatternScale")); }
    public double PatternAngle { get => Ang(D("angle")); set => SetG("angle", Norm(Deg(value))); }
    public int NumberOfLoops => ((JsonArray)G["boundary"]!).Count > 0 ? 1 : 0;
    public void AppendLoop(HatchLoopTypes type, ObjectIdCollection ids)
    {
        AssertWriteEnabled();
        if (NumberOfLoops > 0) throw new AxCadException(0, "NOT_SUPPORTED", "AX-CAD hatches have one boundary");
        if (ids.Count != 1) throw new AxCadException(0, "NOT_SUPPORTED", "the boundary is one closed polyline or circle");
        G["boundary"] = ids[0].Target switch
        {
            Polyline { Closed: true } pl => new JsonArray(Enumerable.Range(0, pl.NumberOfVertices).Select(i => (JsonNode)new JsonArray(pl.GetPoint2dAt(i).X, pl.GetPoint2dAt(i).Y, pl.GetBulgeAt(i))).ToArray()),
            Circle c => new JsonArray(new JsonArray(c.Center.X - c.Radius, c.Center.Y, 1.0), new JsonArray(c.Center.X + c.Radius, c.Center.Y, 1.0)),
            _ => throw new AxCadException(0, "NOT_SUPPORTED", "the boundary is one closed polyline or circle"),
        };
    }
    public void EvaluateHatch(bool underEstimateNumLines) { } // the server draws the pattern
    internal override JsonObject Geom() => NumberOfLoops > 0 ? base.Geom() : throw new AxCadException(0, "BAD_GEOMETRY", "a hatch needs a boundary (AppendLoop)");
    internal override IEnumerable<Point3d> ExtentPoints() => ((JsonArray)G["boundary"]!).Select(p => P(p!));
    internal override void Map(Matrix3d m, double k, double a, bool flip)
    {
        SetG("boundary", new JsonArray(((JsonArray)G["boundary"]!).Select(p =>
        {
            var q = m * P(p!);
            return (JsonNode)new JsonArray(Finite(q.X), Finite(q.Y), flip ? -p![2]!.GetValue<double>() : p![2]!.GetValue<double>());
        }).ToArray()));
        SetG("scale", Positive(D("scale") * k, "PatternScale"));
        SetG("angle", MapAngle(D("angle"), a, flip));
    }
}

/// <summary>A leader: the arrow at vertex 0.</summary>
public sealed class Leader : Curve
{
    public Leader() : base(new JsonObject { ["type"] = "LEADER", ["vertices"] = new JsonArray() }) { }
    public override string DxfName => "LEADER";
    JsonArray V => (JsonArray)G["vertices"]!;
    public int NumVertices => V.Count;
    public bool AppendVertex(Point3d p)
    {
        AssertWriteEnabled();
        V.Add(J(p));
        return true;
    }
    public Point3d VertexAt(int i) => P(V[i]!);
    public void SetVertexAt(int i, Point3d p)
    {
        AssertWriteEnabled();
        V[i] = J(p);
    }
    public override Point3d StartPoint { get => VertexAt(0); set => SetVertexAt(0, value); }
    public override Point3d EndPoint { get => VertexAt(NumVertices - 1); set => SetVertexAt(NumVertices - 1, value); }
    internal override JsonObject Geom() => NumVertices >= 2 ? base.Geom() : throw new AxCadException(0, "BAD_GEOMETRY", "a leader needs two vertices");
    internal override IEnumerable<Point3d> ExtentPoints() => V.Select(p => P(p!));
    internal override void Map(Matrix3d m, double k, double a, bool flip) => SetG("vertices", new JsonArray(V.Select(p => (JsonNode)J(m * P(p!))).ToArray()));
}

/// <summary>A multileader with text: AddLeaderLine(arrow point), TextLocation (the landing) and MText.</summary>
public sealed class MLeader : Entity
{
    public MLeader() : base(new JsonObject { ["type"] = "MULTILEADER", ["vertices"] = new JsonArray(), ["value"] = "", ["height"] = 2.5 }) { }
    public override string DxfName => "MULTILEADER";
    JsonArray V => (JsonArray)G["vertices"]!;
    /// <summary>Adds the arrow point (AX-CAD multileaders have one leader line); returns its index 0.</summary>
    public int AddLeaderLine(Point3d arrow)
    {
        AssertWriteEnabled();
        if (V.Count > 0 && G["arrow_set"] is not null) throw new AxCadException(0, "NOT_SUPPORTED", "AX-CAD multileaders have one leader line");
        V.Insert(0, J(arrow));
        G["arrow_set"] = true;
        return 0;
    }
    public Point3d TextLocation
    {
        get => P(V[V.Count - 1]!);
        set
        {
            AssertWriteEnabled();
            if (V.Count >= 2 || (V.Count == 1 && G["arrow_set"] is null)) V[V.Count - 1] = J(value);
            else V.Add(J(value));
        }
    }
    /// <summary>The note: its Contents and TextHeight are taken.</summary>
    public MText MText
    {
        get
        {
            var t = new MText { Contents = G["value"]!.GetValue<string>(), TextHeight = D("height") };
            if (V.Count > 0) t.Location = TextLocation;
            return t;
        }
        set
        {
            AssertWriteEnabled();
            G["value"] = value.Contents;
            G["height"] = value.TextHeight;
        }
    }
    internal override JsonObject Geom()
    {
        var g = base.Geom();
        g.Remove("arrow_set");
        if (((JsonArray)g["vertices"]!).Count < 2) throw new AxCadException(0, "BAD_GEOMETRY", "a multileader needs an arrow point and a text location");
        return g;
    }
    internal override IEnumerable<Point3d> ExtentPoints() => V.Select(p => P(p!));
    internal override void Map(Matrix3d m, double k, double a, bool flip)
    {
        SetG("vertices", new JsonArray(V.Select(p => (JsonNode)J(m * P(p!))).ToArray()));
        SetG("height", D("height") * k);
    }
}

// --- blocks and attributes -------------------------------------------------------------------

public sealed class BlockReference : Entity
{
    internal BlockReference() : base(new JsonObject { ["type"] = "INSERT", ["name"] = "", ["insert"] = J(Point3d.Origin), ["xscale"] = 1.0, ["yscale"] = 1.0, ["rotation"] = 0.0 }) { }
    public BlockReference(Point3d position, ObjectId blockTableRecord) : this()
    {
        if (blockTableRecord.Target is not BlockTableRecord b || b.IsLayout || b.IsErased) throw new AxCadException(0, "WRONG_TYPE", "a block of the drawing");
        S["btr"] = b.Index;
        _btr = b;
        G["name"] = b.Name;
        G["insert"] = J(position);
    }
    public override string DxfName => "INSERT";
    BlockTableRecord? _btr; // the block given to the constructor (before the reference is in a database)
    BlockTableRecord? Record
    {
        get
        {
            if (Db is null) return _btr;
            if (S["btr"] is JsonValue v && Db is not null) return (BlockTableRecord)Db.Object(v.GetValue<int>());
            if (Db is null) return null;
            var bt = (BlockTable)Db.BlockTableId.Target;
            var name = G["name"]!.GetValue<string>();
            return bt.Has(name) ? (BlockTableRecord)bt[name].Target : null;
        }
    }
    public ObjectId BlockTableRecord => Record?.ObjectId ?? ObjectId.Null;
    public string Name => Record?.Name ?? G["name"]!.GetValue<string>();
    public Point3d Position { get => P("insert"); set => SetP("insert", value); }
    public double Rotation { get => Ang(D("rotation")); set => SetG("rotation", Norm(Deg(value))); }
    public Scale3d ScaleFactors
    {
        get => new(D("xscale"), D("yscale"), 1);
        set
        {
            if (value.X == 0 || value.Y == 0) throw new AxCadException(0, "BAD_GEOMETRY", "scale factors are not 0");
            SetG("xscale", Finite(value.X));
            SetG("yscale", Finite(value.Y));
        }
    }
    public Matrix3d BlockTransform => Matrix3d.Displacement(Position - Point3d.Origin) * Matrix3d.Rotation(Rotation, Vector3d.ZAxis, Point3d.Origin) * new Matrix3d([D("xscale"), 0, 0, 0, 0, D("yscale"), 0, 0, 0, 0, 1, 0, 0, 0, 0, 1]);
    public AttributeCollection AttributeCollection => new(this);
    internal JsonObject Att => S["att"] as JsonObject ?? (JsonObject)(S["att"] = new JsonObject());

    internal void LoadAttributes(Database db)
    {
        if (S["att"] is not JsonObject att) return;
        S["arefs"] = new JsonArray(att.Select(kv =>
        {
            var a = db.Register(new AttributeReference(kv.Key), null);
            a.OwnerId = ObjectId;
            return (JsonNode)a.Index;
        }).ToArray());
    }
    internal IEnumerable<AttributeReference> Refs() => (S["arefs"] as JsonArray ?? []).Select(i => (AttributeReference)Db!.Object(i!.GetValue<int>()));
    internal void AddRef(AttributeReference a)
    {
        AssertWriteEnabled();
        if (Db is null) throw new AxCadException(0, "NOT_IN_DB", "append the block reference first");
        if (a.Db is not null) throw new AxCadException(0, "ALREADY_IN_DB", "the attribute is already in a database");
        if (Att.Any(kv => kv.Key.Equals(a.Tag, StringComparison.OrdinalIgnoreCase))) throw new AxCadException(0, "DUPLICATE_TAG", $"the reference has {a.Tag} already");
        if (Att.Count >= 500) throw new AxCadException(0, "TOO_MANY", "500 attributes per reference");
        Db.Add(a, this);
        Att[a.Tag] = a.PendingText;
        (S["arefs"] as JsonArray ?? (JsonArray)(S["arefs"] = new JsonArray())).Add(a.Index);
    }
    internal override JsonObject Geom()
    {
        var g = base.Geom();
        // a drawing block renamed but not saved keeps its drawing name (the server renames references after the entity edits)
        if (Record is { } r) g["name"] = r.IsNewObject ? r.Name : r.SavedName;
        return g;
    }
    internal override IEnumerable<Point3d> ExtentPoints() => [Position]; // the block's contents are drawn by the server
    internal override void Map(Matrix3d m, double k, double a, bool flip)
    {
        Position = m * Position;
        SetG("xscale", Finite(D("xscale") * k));
        SetG("yscale", Finite(D("yscale") * k * (flip ? -1 : 1)));
        SetG("rotation", MapAngle(D("rotation"), a, flip));
    }
}

public sealed class AttributeCollection : IEnumerable<ObjectId>
{
    readonly BlockReference _r;
    internal AttributeCollection(BlockReference r) => _r = r;
    public int Count => _r.Refs().Count();
    public ObjectId this[int i] => _r.Refs().ElementAt(i).ObjectId;
    /// <summary>Adds an attribute (made with SetAttributeFromBlock) to a reference opened ForWrite.</summary>
    public ObjectId AppendAttribute(AttributeReference a)
    {
        _r.AddRef(a);
        return a.ObjectId;
    }
    public IEnumerator<ObjectId> GetEnumerator() => _r.Refs().Select(a => a.ObjectId).ToList().GetEnumerator();
    IEnumerator IEnumerable.GetEnumerator() => GetEnumerator();
}

/// <summary>An attribute definition in a block made here: tag, prompt, default value, position.</summary>
public sealed class AttributeDefinition : Entity
{
    public AttributeDefinition() : base(new JsonObject { ["type"] = "ATTDEF", ["insert"] = J(Point3d.Origin), ["height"] = 2.5, ["rotation"] = 0.0, ["tag"] = "", ["prompt"] = "", ["default"] = "" }) { }
    public AttributeDefinition(Point3d position, string text, string tag, string prompt, ObjectId style) : this() => (Position, TextString, Tag, Prompt) = (position, text, tag, prompt);
    public override string DxfName => "ATTDEF";
    public string Tag
    {
        get => G["tag"]!.GetValue<string>();
        set => SetG("tag", value.Length is > 0 and <= 255 && !value.Any(c => char.IsWhiteSpace(c) || char.IsControl(c)) ? value.ToUpperInvariant() : throw new AxCadException(0, "BAD_TAG", "a tag is 1..255 characters, no spaces"));
    }
    public string Prompt { get => G["prompt"]!.GetValue<string>(); set => SetG("prompt", DBText.Line1(value)); }
    /// <summary>The default value.</summary>
    public string TextString { get => G["default"]!.GetValue<string>(); set => SetG("default", DBText.Line1(value)); }
    public Point3d Position { get => P("insert"); set => SetP("insert", value); }
    public double Height { get => D("height"); set => SetG("height", Positive(value, "height")); }
    public double Rotation { get => Ang(D("rotation")); set => SetG("rotation", Norm(Deg(value))); }
    public bool Invisible { get => G["invisible"]?.GetValue<bool>() == true; set => SetG("invisible", value); }
    public bool Constant { get => G["constant"]?.GetValue<bool>() == true; set => SetG("constant", value); }
    public bool Verifiable { get => G["verify"]?.GetValue<bool>() == true; set => SetG("verify", value); }
    public bool Preset { get => G["preset"]?.GetValue<bool>() == true; set => SetG("preset", value); }
    /// <summary>The edit API's AttDef: block coordinates, with the base added (the server takes it from the base point).</summary>
    internal JsonObject Def(Point3d origin)
    {
        if (Tag.Length == 0) throw new AxCadException(0, "BAD_TAG", "an attribute definition needs a tag");
        var d = new JsonObject { ["tag"] = Tag, ["prompt"] = Prompt, ["default"] = TextString, ["insert"] = new JsonArray(Position.X + origin.X, Position.Y + origin.Y), ["height"] = Height, ["rotation"] = D("rotation"), ["layer"] = Layer };
        foreach (var k in new[] { "invisible", "constant", "verify", "preset" }) if (G[k]?.GetValue<bool>() == true) d[k] = true;
        return d;
    }
    internal override IEnumerable<Point3d> ExtentPoints() => [Position];
    internal override void Map(Matrix3d m, double k, double a, bool flip)
    {
        (Position, Height) = (m * Position, Height * k);
        if (!flip) SetG("rotation", MapAngle(D("rotation"), a, false));
    }
}

/// <summary>An attribute of a block reference: its value is the drawing's (position and size are
/// the block definition's, drawn by the server).</summary>
public sealed class AttributeReference : Entity
{
    string _tag;
    internal string PendingText = "";
    public AttributeReference() : base(new JsonObject { ["type"] = "ATTRIB" }) => _tag = "";
    internal AttributeReference(string tag) : this() => _tag = tag;
    public override string DxfName => "ATTRIB";
    BlockReference? Owner => !OwnerId.IsNull ? OwnerId.Target as BlockReference : null;
    public string Tag
    {
        get => _tag;
        set
        {
            if (Db is not null) throw new AxCadException(0, "NOT_SUPPORTED", "a reference's tags are its block's");
            _tag = value.Length is > 0 and <= 255 && !value.Any(c => char.IsWhiteSpace(c) || char.IsControl(c)) ? value.ToUpperInvariant() : throw new AxCadException(0, "BAD_TAG", "a tag is 1..255 characters, no spaces");
        }
    }
    public string TextString
    {
        get => Owner?.Att[_tag]?.GetValue<string>() ?? PendingText;
        set
        {
            AssertWriteEnabled();
            var v = DBText.Line1(value);
            if (Owner is { } r) r.Att[_tag] = v; // the reference's value (saved as its `attributes`)
            else PendingText = v;
        }
    }
    /// <summary>Takes the definition's tag and default value (AutoCAD also places it by the transform; AX-CAD's server does).</summary>
    public void SetAttributeFromBlock(AttributeDefinition definition, Matrix3d blockTransform)
    {
        if (Db is not null) throw new AxCadException(0, "ALREADY_IN_DB", "set it up before AppendAttribute");
        Tag = definition.Tag;
        PendingText = definition.TextString;
    }
    internal override void CheckWritable()
    {
        base.CheckWritable();
        if (Owner is { } r && r.Db is not null && r.OwnerId.Target is BlockTableRecord { IsLayout: true } && r.Db.Layers.Has(r.Layer) && ((LayerTableRecord)r.Db.Layers[r.Layer].Target).IsLocked)
            throw new AxCadException(0, "LAYER_LOCKED", $"layer {r.Layer} is locked");
    }
    internal override IEnumerable<Point3d> ExtentPoints() => Owner is { } r ? [r.Position] : [];
    internal override void Map(Matrix3d m, double k, double a, bool flip) => throw new AxCadException(0, "NOT_SUPPORTED", "attributes move with their block reference");
    internal override void Emit(JsonObject body) { } // saved through the reference's attributes
    public override void Erase(bool erasing) => throw new AxCadException(0, "NOT_SUPPORTED", "a block's attributes are not erased one by one (redefine the block)");
}

// --- dimensions ------------------------------------------------------------------------------

/// <summary>A dimension: its own dimension variables (overrides) and text (DimensionText; "" = the measurement).</summary>
public abstract class Dimension : Entity
{
    protected Dimension(JsonObject g) : base(g) { }
    public override string DxfName => "DIMENSION";
    JsonObject Over => G["overrides"] as JsonObject ?? (JsonObject)(G["overrides"] = new JsonObject());
    public string DimensionText
    {
        get => G["text"]?.GetValue<string>() ?? "";
        set
        {
            var t = DBText.Line1(value);
            if (t.Contains("%<")) throw new AxCadException(0, "BAD_TEXT", "field codes (%<...>%) are not taken");
            SetG("text", t is "" or "<>" ? null : t);
        }
    }
    public abstract double Measurement { get; }
    double Num(string k, double d) => Over[k]?.GetValue<double>() ?? d;
    void SetNum(string k, double v, double lo = 0, double hi = 1e6)
    {
        AssertWriteEnabled();
        Over[k] = double.IsFinite(v) && v >= lo && v <= hi ? v : throw new AxCadException(0, "BAD_DIMVAR", $"{k} {lo}..{hi}");
    }
    public double Dimasz { get => Num("dimasz", 2.5); set => SetNum("dimasz", value); }
    public double Dimtxt { get => Num("dimtxt", 2.5); set => SetNum("dimtxt", value); }
    public double Dimexe { get => Num("dimexe", 1.25); set => SetNum("dimexe", value); }
    public double Dimexo { get => Num("dimexo", 0.625); set => SetNum("dimexo", value); }
    public double Dimgap { get => Num("dimgap", 0.625); set => SetNum("dimgap", value, -1e6); }
    public double Dimscale { get => Num("dimscale", 1); set => SetNum("dimscale", value); }
    public double Dimlfac { get => Num("dimlfac", 1); set => SetNum("dimlfac", value, -1e6); }
    public int Dimdec { get => (int)Num("dimdec", 2); set => SetNum("dimdec", value, 0, 8); }
    public int Dimclrd { get => (int)Num("dimclrd", 0); set => SetNum("dimclrd", value, 0, 256); }
    public int Dimclre { get => (int)Num("dimclre", 0); set => SetNum("dimclre", value, 0, 256); }
    public int Dimclrt { get => (int)Num("dimclrt", 0); set => SetNum("dimclrt", value, 0, 256); }
    public int Dimtad { get => (int)Num("dimtad", 0); set => SetNum("dimtad", value, 0, 4); }
    public string Dimpost
    {
        get => Over["dimpost"]?.GetValue<string>() ?? "";
        set
        {
            AssertWriteEnabled();
            var t = DBText.Line1(value);
            Over["dimpost"] = t.Length <= 255 && !t.Contains("%<") ? t : throw new AxCadException(0, "BAD_DIMVAR", "dimpost: 255 characters, no field codes");
        }
    }
    internal override JsonObject Geom()
    {
        var g = base.Geom();
        if (g["overrides"] is JsonObject o && o.Count == 0) g.Remove("overrides");
        return g;
    }
    protected void MapPoints(Matrix3d m, params string[] keys)
    {
        foreach (var k in keys) SetP(k, m * P(k));
    }
}

public sealed class AlignedDimension : Dimension
{
    internal AlignedDimension() : base(new JsonObject { ["type"] = "DIM_ALIGNED", ["p1"] = J(Point3d.Origin), ["p2"] = J(new Point3d(1, 0)), ["distance"] = 1.0 }) { }
    public AlignedDimension(Point3d xLine1Point, Point3d xLine2Point, Point3d dimLinePoint, string dimText, ObjectId dimensionStyle) : this()
    {
        (XLine1Point, XLine2Point) = (xLine1Point, xLine2Point);
        DimLinePoint = dimLinePoint;
        DimensionText = dimText;
    }
    public Point3d XLine1Point { get => P("p1"); set => SetP("p1", value); }
    public Point3d XLine2Point { get => P("p2"); set => SetP("p2", value); }
    /// <summary>A point the dimension line passes through.</summary>
    public Point3d DimLinePoint
    {
        get
        {
            var (a, b) = (XLine1Point, XLine2Point);
            var n = new Vector3d(-(b.Y - a.Y), b.X - a.X).GetNormal();
            return a + n * D("distance");
        }
        set
        {
            var (a, b) = (XLine1Point, XLine2Point);
            var len = a.DistanceTo(b);
            if (len == 0) throw new AxCadException(0, "BAD_GEOMETRY", "the two points are the same");
            var d = ((b.X - a.X) * (value.Y - a.Y) - (b.Y - a.Y) * (value.X - a.X)) / len;
            SetG("distance", Finite(d == 0 ? 1 : d));
        }
    }
    public override double Measurement => XLine1Point.DistanceTo(XLine2Point);
    internal override IEnumerable<Point3d> ExtentPoints() => [XLine1Point, XLine2Point, DimLinePoint];
    internal override void Map(Matrix3d m, double k, double a, bool flip)
    {
        MapPoints(m, "p1", "p2");
        SetG("distance", D("distance") * k * (flip ? -1 : 1));
    }
}

public sealed class RotatedDimension : Dimension
{
    internal RotatedDimension() : base(new JsonObject { ["type"] = "DIM_LINEAR", ["p1"] = J(Point3d.Origin), ["p2"] = J(new Point3d(1, 0)), ["base"] = J(new Point3d(0, 1)), ["angle"] = 0.0 }) { }
    public RotatedDimension(double rotation, Point3d xLine1Point, Point3d xLine2Point, Point3d dimLinePoint, string dimText, ObjectId dimensionStyle) : this()
    {
        (Rotation, XLine1Point, XLine2Point, DimLinePoint) = (rotation, xLine1Point, xLine2Point, dimLinePoint);
        DimensionText = dimText;
    }
    public double Rotation { get => Ang(D("angle")); set => SetG("angle", Norm(Deg(value))); }
    public Point3d XLine1Point { get => P("p1"); set => SetP("p1", value); }
    public Point3d XLine2Point { get => P("p2"); set => SetP("p2", value); }
    public Point3d DimLinePoint { get => P("base"); set => SetP("base", value); }
    public override double Measurement => Math.Abs((XLine2Point - XLine1Point).DotProduct(new Vector3d(Math.Cos(Rotation), Math.Sin(Rotation))));
    internal override IEnumerable<Point3d> ExtentPoints() => [XLine1Point, XLine2Point, DimLinePoint];
    internal override void Map(Matrix3d m, double k, double a, bool flip)
    {
        MapPoints(m, "p1", "p2", "base");
        SetG("angle", MapAngle(D("angle"), a, flip));
    }
}

public sealed class RadialDimension : Dimension
{
    internal RadialDimension() : base(new JsonObject { ["type"] = "DIM_RADIUS", ["center"] = J(Point3d.Origin), ["radius"] = 1.0, ["angle"] = 0.0 }) { }
    public RadialDimension(Point3d center, Point3d chordPoint, double leaderLength, string dimText, ObjectId dimensionStyle) : this()
    {
        Center = center;
        ChordPoint = chordPoint;
        DimensionText = dimText;
    }
    public Point3d Center { get => P("center"); set => SetP("center", value); }
    public Point3d ChordPoint
    {
        get => Center + new Vector3d(Math.Cos(Ang(D("angle"))), Math.Sin(Ang(D("angle")))) * D("radius");
        set
        {
            var v = value - Center;
            SetG("radius", Positive(v.Length, "radius"));
            SetG("angle", Norm(Deg(Math.Atan2(v.Y, v.X))));
        }
    }
    public override double Measurement => D("radius");
    internal override IEnumerable<Point3d> ExtentPoints() => [Center, ChordPoint];
    internal override void Map(Matrix3d m, double k, double a, bool flip)
    {
        MapPoints(m, "center");
        SetG("radius", D("radius") * k);
        SetG("angle", MapAngle(D("angle"), a, flip));
    }
}

public sealed class DiametricDimension : Dimension
{
    internal DiametricDimension() : base(new JsonObject { ["type"] = "DIM_DIAMETER", ["center"] = J(Point3d.Origin), ["radius"] = 1.0, ["angle"] = 0.0 }) { }
    public DiametricDimension(Point3d chordPoint, Point3d farChordPoint, double leaderLength, string dimText, ObjectId dimensionStyle) : this()
    {
        var c = chordPoint + (farChordPoint - chordPoint) * 0.5;
        G["center"] = J(c);
        var v = chordPoint - c;
        G["radius"] = Positive(v.Length, "radius");
        G["angle"] = Norm(Deg(Math.Atan2(v.Y, v.X)));
        DimensionText = dimText;
    }
    Point3d Center => P("center");
    public Point3d ChordPoint => Center + new Vector3d(Math.Cos(Ang(D("angle"))), Math.Sin(Ang(D("angle")))) * D("radius");
    public Point3d FarChordPoint => Center - (ChordPoint - Center);
    public override double Measurement => 2 * D("radius");
    internal override IEnumerable<Point3d> ExtentPoints() => [ChordPoint, FarChordPoint];
    internal override void Map(Matrix3d m, double k, double a, bool flip)
    {
        MapPoints(m, "center");
        SetG("radius", D("radius") * k);
        SetG("angle", MapAngle(D("angle"), a, flip));
    }
}

/// <summary>An angle between two lines; the arc passes through ArcPoint.</summary>
public sealed class LineAngularDimension2 : Dimension
{
    internal LineAngularDimension2() : base(new JsonObject { ["type"] = "DIM_ANGULAR", ["center"] = J(Point3d.Origin), ["p1"] = J(new Point3d(1, 0)), ["p2"] = J(new Point3d(0, 1)), ["base"] = J(new Point3d(1, 1)) }) { }
    public LineAngularDimension2(Point3d xLine1Start, Point3d xLine1End, Point3d xLine2Start, Point3d xLine2End, Point3d arcPoint, string dimText, ObjectId dimensionStyle) : this()
    {
        var (d1, d2) = (xLine1End - xLine1Start, xLine2End - xLine2Start);
        var den = d1.X * d2.Y - d1.Y * d2.X;
        if (Math.Abs(den) < 1e-12) throw new AxCadException(0, "BAD_GEOMETRY", "the two lines are parallel");
        var t = ((xLine2Start.X - xLine1Start.X) * d2.Y - (xLine2Start.Y - xLine1Start.Y) * d2.X) / den;
        var c = xLine1Start + d1 * t;
        // the ends away from the vertex give the directions of the two legs
        Point3d Far(Point3d s, Point3d e) => s.DistanceTo(c) > e.DistanceTo(c) ? s : e;
        G["center"] = J(c);
        G["p1"] = J(Far(xLine1Start, xLine1End));
        G["p2"] = J(Far(xLine2Start, xLine2End));
        G["base"] = J(arcPoint);
        DimensionText = dimText;
    }
    public Point3d Center => P("center");
    public Point3d XLine1End => P("p1");
    public Point3d XLine2End => P("p2");
    public Point3d ArcPoint { get => P("base"); set => SetP("base", value); }
    public override double Measurement => (XLine1End - Center).GetAngleTo(XLine2End - Center);
    internal override IEnumerable<Point3d> ExtentPoints() => [Center, XLine1End, XLine2End, ArcPoint];
    internal override void Map(Matrix3d m, double k, double a, bool flip) => MapPoints(m, "center", "p1", "p2", "base");
}
