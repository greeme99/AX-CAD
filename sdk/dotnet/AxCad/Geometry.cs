// W8 (plan §22.4): geometry types shaped like Autodesk.AutoCAD.Geometry. AX-CAD drawings are 2D:
// Z is carried for source compatibility and must stay 0 where an entity is written.
namespace AxCad;

/// <summary>Comparison tolerance (AutoCAD's Tolerance.Global: 1e-10 vector / 1e-10 point).</summary>
public readonly record struct Tolerance(double EqualVector, double EqualPoint)
{
    public static Tolerance Global { get; } = new(1e-10, 1e-10);
}

public readonly record struct Point2d(double X, double Y)
{
    public static Point2d Origin => new(0, 0);
    public double GetDistanceTo(Point2d o) => Math.Sqrt((X - o.X) * (X - o.X) + (Y - o.Y) * (Y - o.Y));
    public Vector2d GetVectorTo(Point2d o) => new(o.X - X, o.Y - Y);
    public bool IsEqualTo(Point2d o) => IsEqualTo(o, Tolerance.Global);
    public bool IsEqualTo(Point2d o, Tolerance t) => GetDistanceTo(o) <= t.EqualPoint;
    public Point3d Convert3d() => new(X, Y, 0);
    public static Point2d operator +(Point2d p, Vector2d v) => new(p.X + v.X, p.Y + v.Y);
    public static Point2d operator -(Point2d p, Vector2d v) => new(p.X - v.X, p.Y - v.Y);
    public static Vector2d operator -(Point2d a, Point2d b) => new(a.X - b.X, a.Y - b.Y);
}

public readonly record struct Vector2d(double X, double Y)
{
    public static Vector2d XAxis => new(1, 0);
    public static Vector2d YAxis => new(0, 1);
    public double Length => Math.Sqrt(X * X + Y * Y);
    /// <summary>Angle from the X axis, 0..2π.</summary>
    public double Angle => (Math.Atan2(Y, X) + 2 * Math.PI) % (2 * Math.PI);
    public Vector2d GetNormal() => Length > 0 ? new(X / Length, Y / Length) : throw new AxCadException(0, "BAD_GEOMETRY", "zero vector");
    public double DotProduct(Vector2d o) => X * o.X + Y * o.Y;
    public Vector2d GetPerpendicularVector() => new(-Y, X);
    public static Vector2d operator *(Vector2d v, double k) => new(v.X * k, v.Y * k);
    public static Vector2d operator +(Vector2d a, Vector2d b) => new(a.X + b.X, a.Y + b.Y);
    public static Vector2d operator -(Vector2d a, Vector2d b) => new(a.X - b.X, a.Y - b.Y);
}

public readonly record struct Point3d(double X, double Y, double Z = 0)
{
    public static Point3d Origin => new(0, 0, 0);
    public double DistanceTo(Point3d o) => Math.Sqrt((X - o.X) * (X - o.X) + (Y - o.Y) * (Y - o.Y) + (Z - o.Z) * (Z - o.Z));
    public Vector3d GetVectorTo(Point3d o) => new(o.X - X, o.Y - Y, o.Z - Z);
    public Point3d Add(Vector3d v) => this + v;
    public Point3d Subtract(Vector3d v) => this - v;
    public bool IsEqualTo(Point3d o) => IsEqualTo(o, Tolerance.Global);
    public bool IsEqualTo(Point3d o, Tolerance t) => DistanceTo(o) <= t.EqualPoint;
    public Point3d TransformBy(Matrix3d m) => m.Apply(this);
    public Point2d Convert2d() => new(X, Y);
    public static Point3d operator +(Point3d p, Vector3d v) => new(p.X + v.X, p.Y + v.Y, p.Z + v.Z);
    public static Point3d operator -(Point3d p, Vector3d v) => new(p.X - v.X, p.Y - v.Y, p.Z - v.Z);
    public static Vector3d operator -(Point3d a, Point3d b) => new(a.X - b.X, a.Y - b.Y, a.Z - b.Z);
    public static Point3d operator *(Matrix3d m, Point3d p) => m.Apply(p);
}

public readonly record struct Vector3d(double X, double Y, double Z = 0)
{
    public static Vector3d XAxis => new(1, 0, 0);
    public static Vector3d YAxis => new(0, 1, 0);
    public static Vector3d ZAxis => new(0, 0, 1);
    public double Length => Math.Sqrt(X * X + Y * Y + Z * Z);
    public bool IsZeroLength() => Length <= Tolerance.Global.EqualVector;
    public Vector3d GetNormal() => Length > 0 ? this / Length : throw new AxCadException(0, "BAD_GEOMETRY", "zero vector");
    public double DotProduct(Vector3d o) => X * o.X + Y * o.Y + Z * o.Z;
    public Vector3d CrossProduct(Vector3d o) => new(Y * o.Z - Z * o.Y, Z * o.X - X * o.Z, X * o.Y - Y * o.X);
    /// <summary>Unsigned angle to another vector, 0..π.</summary>
    public double GetAngleTo(Vector3d o) => Math.Acos(Math.Clamp(DotProduct(o) / (Length * o.Length), -1, 1));
    /// <summary>Angle to another vector measured counter-clockwise about the reference axis, 0..2π.</summary>
    public double GetAngleTo(Vector3d o, Vector3d reference)
    {
        var a = GetAngleTo(o);
        return CrossProduct(o).DotProduct(reference) < 0 ? 2 * Math.PI - a : a;
    }
    public Vector3d Negate() => new(-X, -Y, -Z);
    public Vector3d TransformBy(Matrix3d m) => m.Apply(this);
    public bool IsParallelTo(Vector3d o) => CrossProduct(o).Length <= Tolerance.Global.EqualVector * Math.Max(1, Length * o.Length);
    public bool IsPerpendicularTo(Vector3d o) => Math.Abs(DotProduct(o)) <= Tolerance.Global.EqualVector * Math.Max(1, Length * o.Length);
    public static Vector3d operator *(Vector3d v, double k) => new(v.X * k, v.Y * k, v.Z * k);
    public static Vector3d operator *(double k, Vector3d v) => v * k;
    public static Vector3d operator /(Vector3d v, double k) => new(v.X / k, v.Y / k, v.Z / k);
    public static Vector3d operator +(Vector3d a, Vector3d b) => new(a.X + b.X, a.Y + b.Y, a.Z + b.Z);
    public static Vector3d operator -(Vector3d a, Vector3d b) => new(a.X - b.X, a.Y - b.Y, a.Z - b.Z);
    public static Vector3d operator -(Vector3d v) => v.Negate();
}

public readonly record struct Scale3d(double X, double Y, double Z)
{
    public Scale3d(double factor) : this(factor, factor, factor) { }
}

/// <summary>A 4x4 transform (row-major, column vectors: p' = M p), like AutoCAD's Matrix3d.</summary>
public readonly struct Matrix3d : IEquatable<Matrix3d>
{
    readonly double[] _m; // 16, row-major; null = identity (default struct)

    public Matrix3d(double[] elements)
    {
        if (elements.Length != 16 || elements.Any(v => !double.IsFinite(v))) throw new AxCadException(0, "BAD_GEOMETRY", "a matrix is 16 finite numbers");
        _m = (double[])elements.Clone();
    }

    public double this[int row, int column] => _m is null ? (row == column ? 1 : 0) : _m[4 * row + column];
    public static Matrix3d Identity => new([1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1]);
    public static Matrix3d Displacement(Vector3d v) => new([1, 0, 0, v.X, 0, 1, 0, v.Y, 0, 0, 1, v.Z, 0, 0, 0, 1]);
    /// <summary>Rotation by angle (radians, counter-clockwise looking down the axis) about an axis through center.</summary>
    public static Matrix3d Rotation(double angle, Vector3d axis, Point3d center)
    {
        var u = axis.GetNormal();
        var (c, s, t) = (Math.Cos(angle), Math.Sin(angle), 1 - Math.Cos(angle));
        var r = new Matrix3d([
            t * u.X * u.X + c, t * u.X * u.Y - s * u.Z, t * u.X * u.Z + s * u.Y, 0,
            t * u.X * u.Y + s * u.Z, t * u.Y * u.Y + c, t * u.Y * u.Z - s * u.X, 0,
            t * u.X * u.Z - s * u.Y, t * u.Y * u.Z + s * u.X, t * u.Z * u.Z + c, 0,
            0, 0, 0, 1]);
        return Displacement(center - Point3d.Origin) * r * Displacement(Point3d.Origin - center);
    }
    public static Matrix3d Scaling(double factor, Point3d center) =>
        Displacement(center - Point3d.Origin) * new Matrix3d([factor, 0, 0, 0, 0, factor, 0, 0, 0, 0, factor, 0, 0, 0, 0, 1]) * Displacement(Point3d.Origin - center);
    /// <summary>Reflection through a point (AutoCAD's Mirroring(Point3d)).</summary>
    public static Matrix3d Mirroring(Point3d p) => Scaling(-1, p) is var m ? new([m[0, 0], 0, 0, m[0, 3], 0, m[1, 1], 0, m[1, 3], 0, 0, 1, 0, 0, 0, 0, 1]) : Identity;
    /// <summary>Reflection across the line through p1 and p2 in the XY plane (AutoCAD: Mirroring(Line3d)).</summary>
    public static Matrix3d Mirroring(Point3d p1, Point3d p2)
    {
        var d = (p2 - p1).GetNormal();
        var (a, b) = (d.X * d.X - d.Y * d.Y, 2 * d.X * d.Y);
        var m = new Matrix3d([a, b, 0, 0, b, -a, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1]);
        return Displacement(p1 - Point3d.Origin) * m * Displacement(Point3d.Origin - p1);
    }

    public static Matrix3d operator *(Matrix3d a, Matrix3d b)
    {
        var r = new double[16];
        for (var i = 0; i < 4; i++)
            for (var j = 0; j < 4; j++)
                for (var k = 0; k < 4; k++) r[4 * i + j] += a[i, k] * b[k, j];
        return new(r);
    }
    public Matrix3d PreMultiplyBy(Matrix3d left) => left * this;
    public Matrix3d PostMultiplyBy(Matrix3d right) => this * right;
    public double GetDeterminant() =>
        this[0, 0] * (this[1, 1] * this[2, 2] - this[1, 2] * this[2, 1]) - this[0, 1] * (this[1, 0] * this[2, 2] - this[1, 2] * this[2, 0]) + this[0, 2] * (this[1, 0] * this[2, 1] - this[1, 1] * this[2, 0]);
    /// <summary>Inverse of an affine matrix.</summary>
    public Matrix3d Inverse()
    {
        var det = GetDeterminant();
        if (Math.Abs(det) < 1e-12) throw new AxCadException(0, "BAD_GEOMETRY", "the matrix has no inverse");
        var r = new double[16];
        var self = this;
        double M(int i, int j) => self[i, j];
        r[0] = (M(1, 1) * M(2, 2) - M(1, 2) * M(2, 1)) / det; r[1] = (M(0, 2) * M(2, 1) - M(0, 1) * M(2, 2)) / det; r[2] = (M(0, 1) * M(1, 2) - M(0, 2) * M(1, 1)) / det;
        r[4] = (M(1, 2) * M(2, 0) - M(1, 0) * M(2, 2)) / det; r[5] = (M(0, 0) * M(2, 2) - M(0, 2) * M(2, 0)) / det; r[6] = (M(0, 2) * M(1, 0) - M(0, 0) * M(1, 2)) / det;
        r[8] = (M(1, 0) * M(2, 1) - M(1, 1) * M(2, 0)) / det; r[9] = (M(0, 1) * M(2, 0) - M(0, 0) * M(2, 1)) / det; r[10] = (M(0, 0) * M(1, 1) - M(0, 1) * M(1, 0)) / det;
        for (var i = 0; i < 3; i++) r[4 * i + 3] = -(r[4 * i] * M(0, 3) + r[4 * i + 1] * M(1, 3) + r[4 * i + 2] * M(2, 3));
        r[15] = 1;
        return new(r);
    }

    internal Point3d Apply(Point3d p) => new(
        this[0, 0] * p.X + this[0, 1] * p.Y + this[0, 2] * p.Z + this[0, 3],
        this[1, 0] * p.X + this[1, 1] * p.Y + this[1, 2] * p.Z + this[1, 3],
        this[2, 0] * p.X + this[2, 1] * p.Y + this[2, 2] * p.Z + this[2, 3]);
    internal Vector3d Apply(Vector3d v) => new(
        this[0, 0] * v.X + this[0, 1] * v.Y + this[0, 2] * v.Z,
        this[1, 0] * v.X + this[1, 1] * v.Y + this[1, 2] * v.Z,
        this[2, 0] * v.X + this[2, 1] * v.Y + this[2, 2] * v.Z);

    /// <summary>The 2D similarity the matrix is (uniform scale, rotation, reflection, move in XY), as
    /// AX-CAD can apply it to a 2D entity; anything else (shear, non-uniform scale, out of plane) is refused.</summary>
    internal (double Scale, double AngleDeg, bool Flip) As2D()
    {
        var (a, b, c, d) = (this[0, 0], this[0, 1], this[1, 0], this[1, 1]);
        var k = Math.Sqrt(a * a + c * c);
        var flip = a * d - b * c < 0;
        var planar = Math.Abs(this[0, 2]) + Math.Abs(this[1, 2]) + Math.Abs(this[2, 0]) + Math.Abs(this[2, 1]) + Math.Abs(this[2, 3]) < 1e-9 && Math.Abs(this[2, 2] - 1) < 1e-9 && Math.Abs(this[3, 0]) + Math.Abs(this[3, 1]) + Math.Abs(this[3, 2]) + Math.Abs(this[3, 3] - 1) < 1e-9;
        var similar = k > 1e-12 && Math.Abs(Math.Sqrt(b * b + d * d) - k) < 1e-9 * Math.Max(1, k) && Math.Abs(a * b + c * d) < 1e-9 * Math.Max(1, k * k);
        if (!planar || !similar) throw new AxCadException(0, "NOT_SUPPORTED", "AX-CAD 2D entities take moves, rotations, uniform scaling and mirroring in the XY plane only");
        return (k, Math.Atan2(c, a) * 180 / Math.PI, flip);
    }

    public bool Equals(Matrix3d o)
    {
        for (var i = 0; i < 4; i++)
            for (var j = 0; j < 4; j++)
                if (this[i, j] != o[i, j]) return false;
        return true;
    }
    public override bool Equals(object? obj) => obj is Matrix3d m && Equals(m);
    public override int GetHashCode() => HashCode.Combine(this[0, 0], this[0, 3], this[1, 1], this[1, 3]);
    public static bool operator ==(Matrix3d a, Matrix3d b) => a.Equals(b);
    public static bool operator !=(Matrix3d a, Matrix3d b) => !a.Equals(b);
}

/// <summary>An axis-aligned box (GeometricExtents).</summary>
public readonly record struct Extents3d(Point3d MinPoint, Point3d MaxPoint)
{
    public Extents3d AddPoint(Point3d p) => new(new(Math.Min(MinPoint.X, p.X), Math.Min(MinPoint.Y, p.Y), Math.Min(MinPoint.Z, p.Z)), new(Math.Max(MaxPoint.X, p.X), Math.Max(MaxPoint.Y, p.Y), Math.Max(MaxPoint.Z, p.Z)));
    public Extents3d AddExtents(Extents3d e) => AddPoint(e.MinPoint).AddPoint(e.MaxPoint);
    internal static Extents3d Of(IEnumerable<Point3d> pts)
    {
        Extents3d? e = null;
        foreach (var p in pts) e = e is { } x ? x.AddPoint(p) : new Extents3d(p, p);
        return e ?? throw new AxCadException(0, "NO_EXTENTS", "the object has no extents");
    }
}
