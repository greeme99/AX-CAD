// W8 (plan §22.4): selections shaped like Autodesk.AutoCAD.EditorInput, over the model space of an
// open database (a script outside the app has no screen to pick on: Select* by filter and window).
using System.Text.Json.Nodes;

namespace AxCad;

public enum PromptStatus { OK = 5100, None = 5000, Error = -5001, Cancel = -5002 }

/// <summary>A DXF filter: (0 type) (8 layer) (62 colour) (6 linetype) (2 block name); text values take
/// * ? wildcards and comma lists, like AutoCAD's.</summary>
public sealed class SelectionFilter
{
    public SelectionFilter(TypedValue[] values)
    {
        foreach (var v in values)
        {
            if (v.TypeCode is not (0 or 2 or 6 or 8 or 62)) throw new AxCadException(0, "NOT_SUPPORTED", $"filter group {v.TypeCode}: AX-CAD filters by 0, 2, 6, 8 and 62");
            if (v.TypeCode == 62 ? v.Value is not (int or short or long) : v.Value is not string { Length: <= 2049 }) throw new AxCadException(0, "BAD_FILTER", $"filter group {v.TypeCode}: a {(v.TypeCode == 62 ? "colour number" : "text pattern")}");
        }
        Values = values;
        _patterns = values.Select(v => v.TypeCode == 62 ? [] : v.Value!.ToString()!.ToUpperInvariant().Split(',').Select(p => p.Trim()).ToArray()).ToArray();
    }
    public TypedValue[] Values { get; }
    readonly string[][] _patterns; // review L8: upper-cased and split once
    public TypedValue[] GetFilter() => Values;

    internal bool Matches(Entity e) => Values.Select((v, i) => v.TypeCode switch
    {
        0 => Any(i, DxfType(e)),
        2 => e is BlockReference r && Any(i, r.Name),
        6 => Any(i, e.Linetype),
        8 => Any(i, e.Layer),
        62 => Convert.ToInt64(v.Value) == e.ColorIndex,
        _ => false,
    }).All(x => x);
    bool Any(int i, string s)
    {
        var u = s.ToUpperInvariant();
        return _patterns[i].Any(p => Glob(p, u));
    }
    static string DxfType(Entity e) => e is Dimension ? "DIMENSION" : e.DxfName;
    /// <summary>* and ? wildcards, comma = or, case-insensitive (no regular expressions).</summary>
    internal static bool Wild(string pattern, string s) => pattern.Split(',').Any(p => Glob(p.Trim().ToUpperInvariant(), s.ToUpperInvariant()));
    static bool Glob(string p, string s)
    {
        int i = 0, j = 0, star = -1, mark = 0;
        while (j < s.Length)
        {
            if (i < p.Length && (p[i] == '?' || p[i] == s[j])) (i, j) = (i + 1, j + 1);
            else if (i < p.Length && p[i] == '*') (star, mark, i) = (i, j, i + 1);
            else if (star >= 0) (i, mark, j) = (star + 1, mark + 1, mark + 1);
            else return false;
        }
        while (i < p.Length && p[i] == '*') i++;
        return i == p.Length;
    }
}

public sealed class SelectionSet
{
    readonly ObjectId[] _ids;
    internal SelectionSet(ObjectId[] ids) => _ids = ids;
    public int Count => _ids.Length;
    public ObjectId[] GetObjectIds() => (ObjectId[])_ids.Clone();
}

public sealed class PromptSelectionResult
{
    internal PromptSelectionResult(ObjectId[] ids) => (Status, Value) = (ids.Length > 0 ? PromptStatus.OK : PromptStatus.Error, ids.Length > 0 ? new SelectionSet(ids) : null);
    public PromptStatus Status { get; }
    /// <summary>Null when nothing was selected (Status Error, as AutoCAD).</summary>
    public SelectionSet? Value { get; }
}

public sealed class Editor
{
    readonly Database _db;
    internal Editor(Database db) => _db = db;
    IEnumerable<Entity> Live => _db.ModelSpaceRecord.Select(id => (Entity)id.Target);
    PromptSelectionResult Pick(Func<Entity, bool> keep, SelectionFilter? filter) =>
        new(Live.Where(e => Safe(keep, e) && (filter?.Matches(e) ?? true)).Select(e => e.ObjectId).ToArray());
    /// <summary>Review M3: an entity whose box cannot be taken (no points) is just not selected.</summary>
    static bool Safe(Func<Entity, bool> keep, Entity e)
    {
        try
        {
            return keep(e);
        }
        catch (AxCadException)
        {
            return false;
        }
    }

    public PromptSelectionResult SelectAll() => SelectAll(null);
    public PromptSelectionResult SelectAll(SelectionFilter? filter) => Pick(_ => true, filter);
    /// <summary>Entities wholly inside the window.</summary>
    public PromptSelectionResult SelectWindow(Point3d pt1, Point3d pt2, SelectionFilter? filter = null)
    {
        var (lo, hi) = Box(pt1, pt2);
        return Pick(e => Inside(e.GeometricExtents, lo, hi), filter);
    }
    /// <summary>Entities whose box meets the window (AX-CAD tests the box, not the exact curve).</summary>
    public PromptSelectionResult SelectCrossingWindow(Point3d pt1, Point3d pt2, SelectionFilter? filter = null)
    {
        var (lo, hi) = Box(pt1, pt2);
        return Pick(e => { var x = e.GeometricExtents; return x.MinPoint.X <= hi.X && x.MaxPoint.X >= lo.X && x.MinPoint.Y <= hi.Y && x.MaxPoint.Y >= lo.Y; }, filter);
    }
    static (Point3d, Point3d) Box(Point3d a, Point3d b) => (new(Math.Min(a.X, b.X), Math.Min(a.Y, b.Y)), new(Math.Max(a.X, b.X), Math.Max(a.Y, b.Y)));
    static bool Inside(Extents3d x, Point3d lo, Point3d hi) => x.MinPoint.X >= lo.X && x.MinPoint.Y >= lo.Y && x.MaxPoint.X <= hi.X && x.MaxPoint.Y <= hi.Y;
    /// <summary>The script's log line (AutoCAD writes it on the command line).</summary>
    public void WriteMessage(string message, params object[] args)
    {
        // review L7: drawing text may carry control characters (terminal escapes, fake log lines)
        var text = args.Length > 0 ? string.Format(System.Globalization.CultureInfo.InvariantCulture, message, args) : message;
        Console.WriteLine(new string(text.Select(c => char.IsControl(c) && c != '\t' ? '?' : c).ToArray()));
    }
}
