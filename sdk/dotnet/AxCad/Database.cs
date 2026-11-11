// W8 (plan §22.4): a 2D drawing revision shaped like Autodesk.AutoCAD.DatabaseServices. Open it
// with AxCadClient.OpenDatabaseAsync, change it inside a transaction exactly as in AutoCAD .NET
// (GetObject ForRead/ForWrite, AppendEntity + AddNewlyCreatedDBObject, Commit; Dispose without
// Commit rolls back), then SaveAsync: the changes go to POST /api/revisions/{id}/edits like the
// 2D screen's save and make a new revision. Coordinates: WCS, mm; angles in radians.
//
// Every object keeps its state as one JSON object, so a transaction's rollback is a copy of states
// and the edit request is the difference between the state opened and the state now.
using System.Collections;
using System.Text.Json;
using System.Text.Json.Nodes;

namespace AxCad;

public enum OpenMode { ForRead = 0, ForWrite = 1, ForNotify = 2 }

/// <summary>A DXF handle (hex); 0 for an object not saved yet.</summary>
public readonly record struct Handle(long Value)
{
    public override string ToString() => Value.ToString("X");
    internal static Handle Of(string? hex) => hex is not null && long.TryParse(hex, System.Globalization.NumberStyles.HexNumber, null, out var v) ? new(v) : default;
}

/// <summary>A reference to an object of one open database (valid while the database is).</summary>
public readonly struct ObjectId : IEquatable<ObjectId>
{
    readonly Database? _db;
    readonly int _i;
    internal ObjectId(Database db, int index) => (_db, _i) = (db, index);
    public static ObjectId Null => default;
    public bool IsNull => _db is null;
    public bool IsValid => _db is not null && _i >= 0 && _i < _db.ObjectCount;
    public bool IsErased => IsValid && _db!.Object(_i).IsErased;
    public Database Database => _db ?? throw new AxCadException(0, "NULL_ID", "a null object id has no database");
    public Handle Handle => IsValid ? _db!.Object(_i).Handle : default;
    /// <summary>AutoCAD's ObjectClass.DxfName (LINE, LAYER, BLOCK_RECORD ...).</summary>
    public string ObjectClassName => IsValid ? _db!.Object(_i).DxfName : "";
    internal DBObject Target => IsValid ? _db!.Object(_i) : throw new AxCadException(0, "NULL_ID", "the object id is null or not of this database");
    /// <summary>The object, opened in the database's active transaction (AutoCAD's ObjectId.GetObject).</summary>
    public DBObject GetObject(OpenMode mode) => Database.TransactionManager.TopTransaction?.GetObject(this, mode) ?? throw new AxCadException(0, "NO_TRANSACTION", "GetObject needs an active transaction");
    public bool Equals(ObjectId o) => ReferenceEquals(_db, o._db) && _i == o._i;
    public override bool Equals(object? obj) => obj is ObjectId o && Equals(o);
    public override int GetHashCode() => HashCode.Combine(_db, _i);
    public static bool operator ==(ObjectId a, ObjectId b) => a.Equals(b);
    public static bool operator !=(ObjectId a, ObjectId b) => !a.Equals(b);
    public override string ToString() => IsNull ? "(null)" : $"({Handle})";
}

public sealed class ObjectIdCollection : IEnumerable<ObjectId>
{
    readonly List<ObjectId> _ids = [];
    public ObjectIdCollection() { }
    public ObjectIdCollection(IEnumerable<ObjectId> ids) => _ids.AddRange(ids);
    public int Count => _ids.Count;
    public ObjectId this[int index] => _ids[index];
    public int Add(ObjectId id)
    {
        _ids.Add(id);
        return _ids.Count - 1;
    }
    public bool Contains(ObjectId id) => _ids.Contains(id);
    public int IndexOf(ObjectId id) => _ids.IndexOf(id);
    public void Remove(ObjectId id) => _ids.Remove(id);
    public void Clear() => _ids.Clear();
    public IEnumerator<ObjectId> GetEnumerator() => _ids.GetEnumerator();
    IEnumerator IEnumerable.GetEnumerator() => GetEnumerator();
}

public sealed class Database
{
    readonly AxCadClient? _client;
    readonly List<DBObject> _objects = [];
    internal readonly JsonObject State = []; // clayer
    const int MaxNewObjects = 10_000;

    public string RevisionId { get; }
    public TransactionManager TransactionManager { get; }
    public ObjectId BlockTableId { get; }
    public ObjectId LayerTableId { get; }
    public ObjectId LinetypeTableId { get; }
    public ObjectId TextStyleTableId { get; }
    public ObjectId DimStyleTableId { get; }
    public ObjectId RegAppTableId { get; }
    public ObjectId NamedObjectsDictionaryId { get; }
    /// <summary>Selections over the model space (AutoCAD's Editor.Select*).</summary>
    public Editor Editor { get; }
    /// <summary>True once SaveAsync made a new revision: this database then stays as it was (read only).</summary>
    public bool IsSaved { get; private set; }

    const int MaxEntities = 500_000; // W8 review M1: what the SDK holds of one drawing

    /// <summary>A database from the render payload (GET /api/revisions/{id}/render). A payload that is
    /// not shaped as the server sends it is refused as BAD_RENDER (one exception type for callers).</summary>
    internal Database(AxCadClient? client, string revisionId, JsonElement render)
    {
        try
        {
            _client = client;
            RevisionId = revisionId;
            TransactionManager = new TransactionManager(this);
            Editor = new Editor(this);
            (BlockTableId, LayerTableId, LinetypeTableId, TextStyleTableId, DimStyleTableId, RegAppTableId, NamedObjectsDictionaryId) = Load(render);
            _loaded = _objects.Count;
        }
        catch (Exception e) when (e is KeyNotFoundException or InvalidOperationException or NullReferenceException or FormatException or InvalidCastException or JsonException or ArgumentException or IndexOutOfRangeException or ArgumentOutOfRangeException)
        {
            throw new AxCadException(0, "BAD_RENDER", $"the drawing payload is not as expected: {e.Message}");
        }
    }

    (ObjectId, ObjectId, ObjectId, ObjectId, ObjectId, ObjectId, ObjectId) Load(JsonElement render)
    {
        if (render.GetProperty("entities").GetArrayLength() > MaxEntities) throw new AxCadException(0, "TOO_BIG", $"{MaxEntities:N0} entities at most");
        var ids = render.TryGetProperty("object_ids", out var oi) && oi.ValueKind == JsonValueKind.Object ? JsonNode.Parse(oi.GetRawText())!.AsObject() : [];
        string? Hd(params string[] path)
        {
            JsonNode? n = ids;
            foreach (var p in path) n = n is JsonObject o && o.TryGetPropertyValue(p, out var x) ? x : null;
            return n is JsonValue v && v.TryGetValue<string>(out var s) ? s : null;
        }
        var tables = render.TryGetProperty("tables", out var tb) && tb.ValueKind == JsonValueKind.Object ? JsonNode.Parse(tb.GetRawText())!.AsObject() : [];
        JsonArray Arr(string k) => tables[k] as JsonArray ?? [];

        // symbol tables and their records
        var layers = Register(new LayerTable(), Hd("tables", "layer"));
        foreach (var l in render.GetProperty("layers").EnumerateArray())
        {
            var s = JsonNode.Parse(l.GetRawText())!.AsObject();
            var rec = new LayerTableRecord(s);
            layers.Load(Register(rec, Hd("records", "layer", rec.Name)));
        }
        var layerTableId = layers.ObjectId;
        var lts = Register(new LinetypeTable(), Hd("tables", "linetype"));
        foreach (var r in Arr("linetypes")) lts.Load(Register(new LinetypeTableRecord((JsonObject)r!.DeepClone()), Hd("records", "linetype", r!["name"]!.GetValue<string>())));
        var linetypeTableId = lts.ObjectId;
        var tss = Register(new TextStyleTable(), Hd("tables", "textstyle"));
        foreach (var r in Arr("textstyles")) tss.Load(Register(new TextStyleTableRecord((JsonObject)r!.DeepClone()), Hd("records", "textstyle", r!["name"]!.GetValue<string>())));
        var textStyleTableId = tss.ObjectId;
        var dss = Register(new DimStyleTable(), Hd("tables", "dimstyle"));
        foreach (var r in Arr("dimstyles")) dss.Load(Register(new DimStyleTableRecord((JsonObject)r!.DeepClone()), Hd("records", "dimstyle", r!["name"]!.GetValue<string>())));
        var dimStyleTableId = dss.ObjectId;
        var ras = Register(new RegAppTable(), Hd("tables", "regapp"));
        foreach (var r in Arr("regapps")) ras.Load(Register(new RegAppTableRecord(new JsonObject { ["name"] = r!.GetValue<string>() }), Hd("records", "regapp", r!.GetValue<string>())));
        var regAppTableId = ras.ObjectId;

        // blocks: the model space with the editable entities, and the insertable blocks (contents on the server)
        var bt = Register(new BlockTable(), null);
        var blockTableId = bt.ObjectId;
        var ms = Register(new BlockTableRecord(new JsonObject { ["name"] = BlockTableRecord.ModelSpace, ["origin"] = new JsonArray(0.0, 0.0), ["ents"] = new JsonArray() }) { ContentsLoaded = true }, Hd("model_space"));
        bt.Load(ms);
        if (render.TryGetProperty("blocks", out var blocks) && blocks.ValueKind == JsonValueKind.Array)
            foreach (var b in blocks.EnumerateArray())
            {
                var name = b.GetProperty("name").GetString()!;
                var rec = new BlockTableRecord(new JsonObject { ["name"] = name, ["origin"] = new JsonArray(0.0, 0.0), ["ents"] = new JsonArray() });
                if (b.TryGetProperty("attdefs", out var ad) && ad.ValueKind == JsonValueKind.Array) rec.KnownAttributeTags = ad.EnumerateArray().Select(a => a.GetProperty("tag").GetString()!).ToList();
                bt.Load(Register(rec, Hd("blocks", name)));
            }
        foreach (var e in render.GetProperty("entities").EnumerateArray())
        {
            if (!e.TryGetProperty("geom", out var g) || g.ValueKind != JsonValueKind.Object) continue; // drawn by the server only
            var ent = Entity.FromPayload(JsonNode.Parse(e.GetRawText())!.AsObject());
            ms.Load(Register(ent, e.GetProperty("handle").GetString()));
            if (ent is BlockReference br) br.LoadAttributes(this);
            if (e.TryGetProperty("xdtree", out var xt) && xt.ValueKind == JsonValueKind.Object)
                ent.LoadExtensionDictionary(this, JsonNode.Parse(xt.GetRawText())!.AsObject());
        }

        // the named object dictionary's user entries (null in the payload: too many to send)
        var nod = Register(new DBDictionary(), Hd("nod"));
        var nodId = nod.ObjectId;
        if (render.TryGetProperty("dicts", out var d) && d.ValueKind == JsonValueKind.Object) nod.LoadTree(this, JsonNode.Parse(d.GetRawText())!.AsObject());
        else nod.Partial = true;

        var current = render.TryGetProperty("current_layer", out var cl) && cl.ValueKind == JsonValueKind.String ? cl.GetString() : null;
        var lt = (LayerTable)layers;
        if (!lt.Records().Any()) throw new AxCadException(0, "BAD_RENDER", "the drawing has no layers");
        State["clayer"] = (current is not null && lt.Has(current) ? lt[current] : lt.Records().FirstOrDefault(r => !((LayerTableRecord)r).IsLocked)?.ObjectId ?? lt.Records().First().ObjectId).Target.Index;
        foreach (var o in _objects) o.Opened();
        return (blockTableId, layerTableId, linetypeTableId, textStyleTableId, dimStyleTableId, regAppTableId, nodId);
    }
    readonly int _loaded; // objects that came with the drawing (the rest were made here)

    internal int ObjectCount => _objects.Count;
    internal DBObject Object(int i) => _objects[i];
    internal T Register<T>(T o, string? handle) where T : DBObject
    {
        o.Attach(this, _objects.Count, handle);
        _objects.Add(o);
        return o;
    }
    internal ObjectId Add(DBObject o, DBObject owner)
    {
        Writable();
        if (o.Db is not null) throw new AxCadException(0, "ALREADY_IN_DB", "the object is already in a database");
        if (_objects.Count - _loaded >= MaxNewObjects) throw new AxCadException(0, "TOO_MANY", "10,000 new objects per save");
        Register(o, null);
        o.Made = true;
        o.OwnerId = owner.ObjectId;
        TransactionManager.TopTransaction!.Track(o, OpenMode.ForWrite);
        return o.ObjectId;
    }

    /// <summary>The layer new entities go on (AutoCAD's CLAYER).</summary>
    public ObjectId Clayer
    {
        get => new(this, State["clayer"]!.GetValue<int>());
        set
        {
            Writable();
            if (value.Target is not LayerTableRecord l || l.IsErased) throw new AxCadException(0, "NO_LAYER", "Clayer takes a layer of this drawing");
            State["clayer"] = value.Target.Index;
        }
    }

    internal void Writable()
    {
        if (IsSaved) throw new AxCadException(0, "DRAWING_SAVED", "saved as a new revision: open that revision to go on editing");
        if (TransactionManager.TopTransaction is null) throw new AxCadException(0, "NO_TRANSACTION", "changes go inside a transaction: using var tr = db.TransactionManager.StartTransaction(); ... tr.Commit();");
    }

    internal (string Db, List<string> States, int Count) Snapshot() => (State.ToJsonString(), _objects.Select(o => o.S.ToJsonString()).ToList(), _objects.Count);
    internal void Restore((string Db, List<string> States, int Count) s)
    {
        foreach (var o in _objects.Skip(s.Count)) o.Detach(); // made in the transaction: gone
        _objects.RemoveRange(s.Count, _objects.Count - s.Count);
        for (var i = 0; i < s.Count; i++) _objects[i].S = JsonNode.Parse(s.States[i])!.AsObject();
        var db = JsonNode.Parse(s.Db)!.AsObject();
        State.Clear();
        foreach (var (k, v) in db) State[k] = v?.DeepClone();
    }

    internal IEnumerable<DBObject> Objects => _objects;
    internal LayerTable Layers => (LayerTable)LayerTableId.Target;
    internal BlockTableRecord ModelSpaceRecord => (BlockTableRecord)((BlockTable)BlockTableId.Target)[BlockTableRecord.ModelSpace].Target;

    /// <summary>The edit request the 2D screen would send for the changes so far.</summary>
    public JsonObject Changes()
    {
        var body = new JsonObject { ["created"] = new JsonArray(), ["modified"] = new JsonArray(), ["deleted"] = new JsonArray() };
        foreach (var o in _objects) o.Emit(body);
        foreach (var k in new[] { "layers", "tables", "blocks", "dictionaries" })
            if (body[k] is JsonArray a && a.Count == 0) body.Remove(k);
        return body;
    }

    /// <summary>Saves the changes as the next revision; returns it (revision_id, revision_no, ...).
    /// Open that revision to go on editing.</summary>
    public async Task<JsonElement> SaveAsync(CancellationToken ct = default)
    {
        if (TransactionManager.TopTransaction is not null) throw new AxCadException(0, "TRANSACTION_ACTIVE", "commit or abort the transaction before saving");
        if (IsSaved) throw new AxCadException(0, "DRAWING_SAVED", "already saved: open the new revision to go on editing");
        if (_client is null) throw new AxCadException(0, "NO_CLIENT", "this database was not opened from a server");
        var body = Changes();
        if (body.Count == 3 && body.All(kv => ((JsonArray)kv.Value!).Count == 0)) throw new AxCadException(0, "NO_CHANGES", "nothing to save");
        var saved = await _client.PostAsync($"/api/revisions/{Uri.EscapeDataString(RevisionId)}/edits", body, ct);
        IsSaved = true; // the same edits are never sent twice
        return saved;
    }

    internal static JsonArray List(JsonObject body, string key) => body[key] as JsonArray ?? (JsonArray)(body[key] = new JsonArray());
}

public enum TransactionStatus { Active, Committed, RolledBack }

public sealed class TransactionManager
{
    readonly Database _db;
    internal TransactionManager(Database db) => _db = db;
    public Transaction? TopTransaction { get; private set; }
    public int NumberOfActiveTransactions => TopTransaction is null ? 0 : 1;
    /// <summary>Starts the transaction every change goes in (one at a time; AX-CAD does not nest them).</summary>
    public Transaction StartTransaction()
    {
        if (TopTransaction is not null) throw new AxCadException(0, "TRANSACTION_ACTIVE", "commit or abort the open transaction first");
        if (_db.IsSaved) throw new AxCadException(0, "DRAWING_SAVED", "saved as a new revision: open that revision to go on editing");
        return TopTransaction = new Transaction(_db, this);
    }
    public DBObject GetObject(ObjectId id, OpenMode mode) => (TopTransaction ?? throw new AxCadException(0, "NO_TRANSACTION", "GetObject needs an active transaction")).GetObject(id, mode);
    internal void End(Transaction t)
    {
        if (TopTransaction == t) TopTransaction = null;
    }
}

/// <summary>AutoCAD-style transaction: Commit keeps the changes made since it started; Abort, or
/// Dispose without Commit, puts the drawing back as it was (nothing half-applied).</summary>
public sealed class Transaction : IDisposable
{
    readonly Database _db;
    readonly (string, List<string>, int) _snap;
    readonly HashSet<DBObject> _opened = []; // W8 review M2: opening n objects is O(n)
    internal Transaction(Database db, TransactionManager tm)
    {
        _db = db;
        TransactionManager = tm;
        _snap = db.Snapshot();
    }
    public TransactionManager TransactionManager { get; }
    public TransactionStatus Status { get; private set; } = TransactionStatus.Active;

    public DBObject GetObject(ObjectId id, OpenMode mode) => GetObject(id, mode, false);
    public DBObject GetObject(ObjectId id, OpenMode mode, bool openErased)
    {
        Active();
        var o = id.Target;
        if (!ReferenceEquals(id.Database, _db)) throw new AxCadException(0, "WRONG_DATABASE", "the object is in another database");
        if (o.IsErased && !openErased) throw new AxCadException(0, "ERASED", $"{o.DxfName} {o.Handle} is erased");
        if (mode == OpenMode.ForWrite) o.CheckWritable();
        Track(o, mode);
        return o;
    }
    /// <summary>For source compatibility: AppendEntity / SetAt already put the object in the database.</summary>
    public void AddNewlyCreatedDBObject(DBObject obj, bool add)
    {
        Active();
        if (obj.Db != _db) throw new AxCadException(0, "NOT_IN_DB", "append the object (AppendEntity / SetAt / Add) first");
        if (!add) obj.Erase();
    }
    internal void Track(DBObject o, OpenMode mode)
    {
        _opened.Add(o);
        o.Open(mode);
    }
    void Active()
    {
        if (Status != TransactionStatus.Active) throw new AxCadException(0, "TRANSACTION_ENDED", "the transaction has ended");
    }
    public void Commit()
    {
        Active();
        Status = TransactionStatus.Committed;
        Close();
    }
    public void Abort()
    {
        if (Status != TransactionStatus.Active) return;
        _db.Restore(_snap);
        Status = TransactionStatus.RolledBack;
        Close();
    }
    void Close()
    {
        foreach (var o in _opened) o.Close();
        _opened.Clear();
        TransactionManager.End(this);
    }
    public void Dispose() => Abort();
}

/// <summary>What every database object has (AutoCAD's DBObject).</summary>
public abstract class DBObject : IDisposable
{
    internal JsonObject S;
    string _base = "";
    OpenMode? _open;
    internal Database? Db;
    internal int Index = -1;
    string? _handle;

    protected DBObject(JsonObject state) => S = state;

    internal void Attach(Database db, int index, string? handle) => (Db, Index, _handle) = (db, index, handle);
    internal void Detach() => (Db, Index, _open) = (null, -1, null);
    /// <summary>The state the drawing has: what Emit compares with.</summary>
    internal void Opened() => _base = S.ToJsonString();
    internal JsonObject Base => JsonNode.Parse(_base)!.AsObject();
    internal void Open(OpenMode mode)
    {
        if (_open != OpenMode.ForWrite) _open = mode;
    }
    internal void Close() => _open = null;

    public ObjectId ObjectId => Db is null ? ObjectId.Null : new(Db, Index);
    public Database? Database => Db;
    public ObjectId OwnerId { get; internal set; }
    public Handle Handle => Handle.Of(_handle);
    internal string? FileHandle => _handle;
    /// <summary>Not saved in the drawing yet (made in this session).</summary>
    public bool IsNewObject => Made || Db is null;
    internal bool Made; // made in this session (not in the drawing)
    public bool IsErased => S["erased"]?.GetValue<bool>() == true;
    public bool IsReadEnabled => Db is null || _open is not null;
    public bool IsWriteEnabled => Db is null || _open == OpenMode.ForWrite;
    public bool IsModified => !IsNewObject && S.ToJsonString() != _base;
    public abstract string DxfName { get; }
    public virtual string ObjectClassName => DxfName;

    public void UpgradeOpen()
    {
        if (_open is null) throw new AxCadException(0, "NOT_OPEN", "open the object in a transaction first");
        CheckWritable();
        _open = OpenMode.ForWrite;
    }
    public void DowngradeOpen()
    {
        if (_open is not null) _open = OpenMode.ForRead;
    }
    internal virtual void CheckWritable() => Db?.Writable();
    protected internal void AssertWriteEnabled()
    {
        if (Db is null) return; // not in a database yet: free to set up
        Db.Writable();
        if (_open != OpenMode.ForWrite) throw new AxCadException(0, "NOT_OPEN_FOR_WRITE", $"{DxfName}: open it ForWrite (tr.GetObject(id, OpenMode.ForWrite)) or call UpgradeOpen()");
    }
    protected internal void AssertReadEnabled()
    {
        if (IsErased && Db is not null && _open is null) throw new AxCadException(0, "ERASED", $"{DxfName} {Handle} is erased");
    }

    public virtual void Erase() => Erase(true);
    public virtual void Erase(bool erasing)
    {
        AssertWriteEnabled();
        S["erased"] = erasing;
    }

    // --- XDATA: (1001 app) then that application's groups, like AutoCAD's ResultBuffer ---------
    public ResultBuffer? XData
    {
        get
        {
            if (S["xd"] is not JsonObject xd || xd.Count == 0) return null;
            var rb = new ResultBuffer();
            foreach (var (app, groups) in xd)
            {
                rb.Add(new TypedValue(1001, app));
                foreach (var g in (JsonArray)groups!) rb.Add(TypedValue.FromJson(g!.AsArray()));
            }
            return rb;
        }
        set
        {
            AssertWriteEnabled();
            // W8 review L3: built aside and put in at the end (a refused group changes nothing)
            var xd = (JsonObject?)S["xd"]?.DeepClone() ?? new JsonObject();
            string? app = null;
            foreach (var tv in value ?? new ResultBuffer())
            {
                if (tv.TypeCode == 1001)
                {
                    app = tv.Value?.ToString() ?? "";
                    if (app.StartsWith("ACAD", StringComparison.OrdinalIgnoreCase) || app.StartsWith("ACDB", StringComparison.OrdinalIgnoreCase)) throw new AxCadException(0, "SYSTEM_APP", $"{app}: AutoCAD's own XDATA is not written by scripts");
                    if (Db is not null && !((RegAppTable)Db.RegAppTableId.Target).Has(app)) throw new AxCadException(0, "NO_REGAPP", $"register the application first: {app}");
                    app = xd.Select(kv => kv.Key).FirstOrDefault(k => k.Equals(app, StringComparison.OrdinalIgnoreCase)) ?? app; // one key per application
                    xd[app] = new JsonArray();
                }
                else if (app is null) throw new AxCadException(0, "BAD_XDATA", "XDATA starts with (1001, application name)");
                else ((JsonArray)xd[app]!).Add(tv.ToJson());
            }
            if (xd.ToJsonString().Length > 16 * 1024) throw new AxCadException(0, "BAD_XDATA", "XDATA is 16 KB per object at most");
            S["xd"] = xd;
        }
    }
    public ResultBuffer? GetXDataForApplication(string app)
    {
        if (S["xd"] is not JsonObject xd) return null;
        var key = xd.Select(kv => kv.Key).FirstOrDefault(k => k.Equals(app, StringComparison.OrdinalIgnoreCase));
        if (key is null || ((JsonArray)xd[key]!).Count == 0) return null;
        var rb = new ResultBuffer(new TypedValue(1001, key));
        foreach (var g in (JsonArray)xd[key]!) rb.Add(TypedValue.FromJson(g!.AsArray()));
        return rb;
    }

    /// <summary>The object's extension dictionary (Null when it has none).</summary>
    public ObjectId ExtensionDictionary => S["xdict"] is JsonValue v && Db is not null ? new ObjectId(Db, v.GetValue<int>()) : ObjectId.Null;
    public void CreateExtensionDictionary()
    {
        AssertWriteEnabled();
        if (!ExtensionDictionary.IsNull) return;
        if (Db is null || IsNewObject) throw new AxCadException(0, "NOT_SAVED", "an extension dictionary needs a saved object (save first)");
        var d = Db.Register(new DBDictionary { ExtensionOf = this }, null);
        d.Made = true;
        d.OwnerId = ObjectId;
        S["xdict"] = d.Index;
    }

    /// <summary>Adds this object's part of the edit request.</summary>
    internal virtual void Emit(JsonObject body) { }
    public void Dispose() { }
}

/// <summary>A typed DXF group: (code, value).</summary>
public readonly record struct TypedValue(int TypeCode, object? Value)
{
    public TypedValue(int typeCode) : this(typeCode, null) { }
    internal JsonArray ToJson() => new(TypeCode, Value switch
    {
        null => null,
        Point3d p => new JsonArray(ModelSpaceChecks.Finite(p.X), ModelSpaceChecks.Finite(p.Y), ModelSpaceChecks.Finite(p.Z)),
        Point2d p => new JsonArray(ModelSpaceChecks.Finite(p.X), ModelSpaceChecks.Finite(p.Y), 0.0),
        string s => s,
        bool b => b ? 1 : 0,
        short or int or long => JsonValue.Create(Convert.ToInt64(Value)),
        double d => double.IsFinite(d) ? d : throw new AxCadException(0, "BAD_XDATA", "XDATA numbers are finite"),
        float f => ModelSpaceChecks.Finite(f),
        Handle h => h.ToString(),
        ObjectId id => id.Handle.ToString(),
        _ => throw new AxCadException(0, "BAD_XDATA", $"no DXF value for {Value.GetType().Name}"),
    });
    internal static TypedValue FromJson(JsonArray a)
    {
        var code = a[0]!.GetValue<int>();
        object? v = a[1] switch
        {
            JsonArray p => new Point3d(p[0]!.GetValue<double>(), p[1]!.GetValue<double>(), p.Count > 2 ? p[2]!.GetValue<double>() : 0),
            JsonValue x when x.TryGetValue<string>(out var s) => s,
            JsonValue x when x.TryGetValue<long>(out var l) && code is >= 60 and <= 99 or >= 170 and <= 179 or >= 270 and <= 289 or >= 1060 and <= 1071 => l,
            JsonValue x => x.GetValue<double>(),
            _ => null,
        };
        return new(code, v);
    }
}

public sealed class ResultBuffer : IEnumerable<TypedValue>, IDisposable
{
    readonly List<TypedValue> _v = [];
    public ResultBuffer() { }
    public ResultBuffer(params TypedValue[] values) => _v.AddRange(values);
    public void Add(TypedValue v) => _v.Add(v);
    public TypedValue[] AsArray() => [.. _v];
    public IEnumerator<TypedValue> GetEnumerator() => _v.GetEnumerator();
    IEnumerator IEnumerable.GetEnumerator() => GetEnumerator();
    public void Dispose() { }
}

// --- symbol tables ---------------------------------------------------------------------------

public abstract class SymbolTable : DBObject, IEnumerable<ObjectId>
{
    protected SymbolTable() : base(new JsonObject { ["recs"] = new JsonArray() }) { }
    internal void Load(DBObject rec)
    {
        rec.OwnerId = ObjectId;
        ((JsonArray)S["recs"]!).Add(rec.Index);
    }
    internal IEnumerable<SymbolTableRecord> Records() => ((JsonArray)S["recs"]!).Select(i => (SymbolTableRecord)Db!.Object(i!.GetValue<int>())).Where(r => !r.IsErased);
    public bool Has(string name) => Find(name) is not null;
    public bool Has(ObjectId id) => Records().Any(r => r.ObjectId == id);
    SymbolTableRecord? Find(string name) => Records().FirstOrDefault(r => r.Name.Equals(name, StringComparison.OrdinalIgnoreCase));
    public ObjectId this[string name] => Find(name)?.ObjectId ?? throw new AxCadException(0, "KEY_NOT_FOUND", $"{DxfName}: no record {name}");
    /// <summary>Adds a record (open the table ForWrite); returns its id.</summary>
    public ObjectId Add(SymbolTableRecord record)
    {
        AssertWriteEnabled();
        record.CheckName(record.Name);
        if (Has(record.Name)) throw new AxCadException(0, "DUPLICATE_RECORD", $"{DxfName}: {record.Name} exists");
        Accepts(record);
        var id = Db!.Add(record, this);
        ((JsonArray)S["recs"]!).Add(record.Index);
        return id;
    }
    internal abstract void Accepts(SymbolTableRecord record);
    public IEnumerator<ObjectId> GetEnumerator() => Records().Select(r => r.ObjectId).ToList().GetEnumerator();
    IEnumerator IEnumerable.GetEnumerator() => GetEnumerator();
}

public abstract class SymbolTableRecord : DBObject
{
    protected SymbolTableRecord(JsonObject s) : base(s) { }
    public string Name
    {
        get => S["name"]!.GetValue<string>();
        set
        {
            AssertWriteEnabled();
            CheckName(value);
            if (Db is not null && OwnerId.Target is SymbolTable t && t.Has(value) && !value.Equals(Name, StringComparison.OrdinalIgnoreCase)) throw new AxCadException(0, "DUPLICATE_RECORD", $"{value} exists");
            S["name"] = value;
        }
    }
    internal virtual void CheckName(string n)
    {
        if (n.Length is 0 or > 255 || n.Any(c => c < 0x20 || c is >= '\x7f' and <= '\x9f' || "<>/\\\":;?*|,=`".Contains(c)))
            throw new AxCadException(0, "BAD_NAME", $"not a symbol name: {n}");
    }
    protected string BaseName => Base["name"]!.GetValue<string>();
    /// <summary>The name the drawing has (before a rename not saved yet).</summary>
    internal string SavedName => IsNewObject ? Name : BaseName;
    /// <summary>A table operation (TableOp) for this record: create, modify, rename, delete.</summary>
    protected void EmitTable(JsonObject body, string kind, params string[] fields)
    {
        var ops = Database.List(body, "tables");
        if (IsNewObject)
        {
            if (IsErased) return;
            var op = new JsonObject { ["kind"] = kind, ["op"] = "create", ["name"] = Name };
            foreach (var f in fields) if (S[f] is { } v) op[f] = v.DeepClone();
            ops.Add(op);
            return;
        }
        var b = Base;
        if (IsErased)
        {
            ops.Add(new JsonObject { ["kind"] = kind, ["op"] = "delete", ["name"] = BaseName });
            return;
        }
        var mod = new JsonObject { ["kind"] = kind, ["op"] = "modify", ["name"] = BaseName };
        foreach (var f in fields) if (S[f] is { } v && v.ToJsonString() != b[f]?.ToJsonString()) mod[f] = v.DeepClone();
        if (mod.Count > 3) ops.Add(mod);
        if (Name != BaseName) ops.Add(new JsonObject { ["kind"] = kind, ["op"] = "rename", ["name"] = BaseName, ["new_name"] = Name });
    }
}

public sealed class LayerTable : SymbolTable
{
    public override string DxfName => "TABLE";
    internal override void Accepts(SymbolTableRecord record)
    {
        if (record is not LayerTableRecord) throw new AxCadException(0, "WRONG_TYPE", "the layer table takes LayerTableRecord");
    }
}

public sealed class LayerTableRecord : SymbolTableRecord
{
    public LayerTableRecord() : base([]) { S["name"] = ""; }
    internal LayerTableRecord(JsonObject s) : base(s) { }
    public override string DxfName => "LAYER";
    /// <summary>ACI colour 1..255 (7 = white/black).</summary>
    public int ColorIndex
    {
        get => S["aci"] is JsonValue v ? v.GetValue<int>() : 7;
        set
        {
            AssertWriteEnabled();
            S["aci"] = value is >= 1 and <= 255 ? value : throw new AxCadException(0, "BAD_COLOR", "layer colour index 1..255");
        }
    }
    public bool IsLocked { get => Flag("locked"); set => SetFlag("locked", value); }
    public bool IsOff { get => Flag("off"); set => SetFlag("off", value); }
    public bool IsFrozen { get => Flag("frozen"); set => SetFlag("frozen", value); }
    public bool IsPlottable { get => S["plot"]?.GetValue<bool>() != false; set => SetFlag("plot", value); }
    /// <summary>The layer's linetype by name (AutoCAD: LinetypeObjectId).</summary>
    public string Linetype
    {
        get => S["linetype"]?.GetValue<string>() ?? "Continuous";
        set
        {
            AssertWriteEnabled();
            S["linetype"] = value;
        }
    }
    public ObjectId LinetypeObjectId
    {
        get => Db is not null && ((LinetypeTable)Db.LinetypeTableId.Target).Has(Linetype) ? ((LinetypeTable)Db.LinetypeTableId.Target)[Linetype] : ObjectId.Null;
        set => Linetype = ((LinetypeTableRecord)value.Target).Name;
    }
    /// <summary>1/100 mm (-3 = default).</summary>
    public int LineWeight
    {
        get => S["lwt"] is JsonValue v ? v.GetValue<int>() : -3;
        set
        {
            AssertWriteEnabled();
            S["lwt"] = ModelSpaceChecks.LineWeight(value, layer: true);
        }
    }
    public string Description
    {
        get => S["description"]?.GetValue<string>() ?? "";
        set
        {
            AssertWriteEnabled();
            S["description"] = value.Length <= 255 && !value.Any(char.IsControl) ? value : throw new AxCadException(0, "BAD_TEXT", "one line, 255 characters");
        }
    }
    bool Flag(string k) => S[k]?.GetValue<bool>() == true;
    void SetFlag(string k, bool v)
    {
        AssertWriteEnabled();
        S[k] = v;
    }
    internal override void CheckWritable()
    {
        base.CheckWritable();
    }
    public override void Erase(bool erasing) => throw new AxCadException(0, "NOT_SUPPORTED", "layers are deleted with PURGE / the layer panel, not by scripts");

    static readonly (string Key, string Api)[] Fields = [("aci", "color"), ("linetype", "linetype"), ("locked", "locked"), ("lwt", "lineweight"), ("plot", "plot"), ("off", "off"), ("frozen", "frozen"), ("description", "description")];
    internal override void Emit(JsonObject body)
    {
        var ops = Database.List(body, "layers");
        var op = new JsonObject { ["name"] = IsNewObject ? Name : BaseName };
        if (IsNewObject) op["create"] = true;
        var b = IsNewObject ? [] : Base;
        foreach (var (k, api) in Fields) if (S[k] is { } v && v.ToJsonString() != b[k]?.ToJsonString()) op[api] = v.DeepClone();
        if (!IsNewObject && Name != BaseName) op["rename"] = Name;
        if (IsNewObject || op.Count > 1) ops.Add(op);
    }
}

public sealed class LinetypeTable : SymbolTable
{
    public override string DxfName => "TABLE";
    internal override void Accepts(SymbolTableRecord r)
    {
        if (r is not LinetypeTableRecord) throw new AxCadException(0, "WRONG_TYPE", "the linetype table takes LinetypeTableRecord");
    }
}

public sealed class LinetypeTableRecord : SymbolTableRecord
{
    public LinetypeTableRecord() : base(new JsonObject { ["name"] = "", ["pattern"] = new JsonArray() }) { }
    internal LinetypeTableRecord(JsonObject s) : base(s) { }
    public override string DxfName => "LTYPE";
    public string AsciiDescription
    {
        get => S["description"]?.GetValue<string>() ?? "";
        set
        {
            AssertWriteEnabled();
            S["description"] = value;
        }
    }
    JsonArray Pattern => S["pattern"] as JsonArray ?? (JsonArray)(S["pattern"] = new JsonArray());
    public int NumDashes
    {
        get => Pattern.Count;
        set
        {
            AssertWriteEnabled();
            if (value is < 0 or > 12) throw new AxCadException(0, "BAD_LINETYPE", "0..12 dashes");
            while (Pattern.Count < value) Pattern.Add(0.0);
            while (Pattern.Count > value) Pattern.RemoveAt(Pattern.Count - 1);
        }
    }
    /// <summary>dash &gt; 0, gap &lt; 0, dot 0 (mm).</summary>
    public double DashLengthAt(int i) => Pattern[i]!.GetValue<double>();
    public void SetDashLengthAt(int i, double v)
    {
        AssertWriteEnabled();
        Pattern[i] = ModelSpaceChecks.Finite(v);
    }
    public double PatternLength => Pattern.Sum(v => Math.Abs(v!.GetValue<double>()));
    internal override void Emit(JsonObject body) => EmitTable(body, "linetype", "description", "pattern");
}

public sealed class TextStyleTable : SymbolTable
{
    public override string DxfName => "TABLE";
    internal override void Accepts(SymbolTableRecord r)
    {
        if (r is not TextStyleTableRecord) throw new AxCadException(0, "WRONG_TYPE", "the text style table takes TextStyleTableRecord");
    }
}

public sealed class TextStyleTableRecord : SymbolTableRecord
{
    public TextStyleTableRecord() : base(new JsonObject { ["name"] = "", ["font"] = "txt.shx" }) { }
    internal TextStyleTableRecord(JsonObject s) : base(s) { }
    public override string DxfName => "STYLE";
    /// <summary>A font file name (.ttf/.ttc/.otf/.shx) the server finds in its font folders.</summary>
    public string FileName { get => S["font"]?.GetValue<string>() ?? ""; set { AssertWriteEnabled(); S["font"] = value[(value.LastIndexOfAny(['/', '\\']) + 1)..]; } } // a file name only, whatever the OS
    /// <summary>0 = the height is asked when text is placed.</summary>
    public double TextSize { get => S["height"]?.GetValue<double>() ?? 0; set { AssertWriteEnabled(); S["height"] = ModelSpaceChecks.Finite(value); } }
    public double XScale { get => S["width"]?.GetValue<double>() ?? 1; set { AssertWriteEnabled(); S["width"] = ModelSpaceChecks.Positive(value, "XScale"); } }
    /// <summary>radians</summary>
    public double ObliquingAngle { get => (S["oblique"]?.GetValue<double>() ?? 0) * Math.PI / 180; set { AssertWriteEnabled(); S["oblique"] = ModelSpaceChecks.Deg(value); } }
    internal override void Emit(JsonObject body) => EmitTable(body, "textstyle", "font", "height", "width", "oblique");
}

public sealed class DimStyleTable : SymbolTable
{
    public override string DxfName => "TABLE";
    internal override void Accepts(SymbolTableRecord r)
    {
        if (r is not DimStyleTableRecord) throw new AxCadException(0, "WRONG_TYPE", "the dimension style table takes DimStyleTableRecord");
    }
}

public sealed class DimStyleTableRecord : SymbolTableRecord
{
    public DimStyleTableRecord() : base(new JsonObject { ["name"] = "", ["dim"] = new JsonObject() }) { }
    internal DimStyleTableRecord(JsonObject s) : base(s) { }
    public override string DxfName => "DIMSTYLE";
    JsonObject Dim => S["dim"] as JsonObject ?? (JsonObject)(S["dim"] = new JsonObject());
    double Len(string k, double d) => Dim[k]?.GetValue<double>() ?? d;
    void Set(string k, JsonNode v)
    {
        AssertWriteEnabled();
        Dim[k] = v;
    }
    public double Dimtxt { get => Len("dimtxt", 2.5); set => Set("dimtxt", ModelSpaceChecks.Finite(value)); }
    public double Dimasz { get => Len("dimasz", 2.5); set => Set("dimasz", ModelSpaceChecks.Finite(value)); }
    public double Dimexe { get => Len("dimexe", 1.25); set => Set("dimexe", ModelSpaceChecks.Finite(value)); }
    public double Dimexo { get => Len("dimexo", 0.625); set => Set("dimexo", ModelSpaceChecks.Finite(value)); }
    public double Dimgap { get => Len("dimgap", 0.625); set => Set("dimgap", ModelSpaceChecks.Finite(value)); }
    public double Dimscale { get => Len("dimscale", 1); set => Set("dimscale", ModelSpaceChecks.Positive(value, "Dimscale")); }
    public int Dimdec { get => Dim["dimdec"]?.GetValue<int>() ?? 2; set => Set("dimdec", value is >= 0 and <= 8 ? value : throw new AxCadException(0, "BAD_DIMVAR", "Dimdec 0..8")); }
    /// <summary>The dimension text style by name (AutoCAD: Dimtxsty ObjectId).</summary>
    public string DimtxstyName { get => Dim["dimtxsty"]?.GetValue<string>() ?? "Standard"; set => Set("dimtxsty", value); }
    internal override void Emit(JsonObject body) => EmitTable(body, "dimstyle", "dim");
}

public sealed class RegAppTable : SymbolTable
{
    public override string DxfName => "TABLE";
    internal override void Accepts(SymbolTableRecord r)
    {
        if (r is not RegAppTableRecord) throw new AxCadException(0, "WRONG_TYPE", "the application table takes RegAppTableRecord");
    }
}

public sealed class RegAppTableRecord : SymbolTableRecord
{
    public RegAppTableRecord() : base(new JsonObject { ["name"] = "" }) { }
    internal RegAppTableRecord(JsonObject s) : base(s) { }
    public override string DxfName => "APPID";
    internal override void Emit(JsonObject body) => EmitTable(body, "regapp");
}

// --- blocks ----------------------------------------------------------------------------------

public sealed class BlockTable : SymbolTable
{
    public override string DxfName => "TABLE";
    internal override void Accepts(SymbolTableRecord r)
    {
        if (r is not BlockTableRecord) throw new AxCadException(0, "WRONG_TYPE", "the block table takes BlockTableRecord");
    }
}

/// <summary>The model space or a block. The model space has the drawing's editable entities; a
/// block made here takes entities and attribute definitions; a block of the drawing can be renamed
/// or erased (its contents stay on the server: redefine it in the 2D screen).</summary>
public sealed class BlockTableRecord : SymbolTableRecord, IEnumerable<ObjectId>
{
    public const string ModelSpace = "*Model_Space";
    public const string PaperSpace = "*Paper_Space";
    public BlockTableRecord() : base(new JsonObject { ["name"] = "", ["origin"] = new JsonArray(0.0, 0.0), ["ents"] = new JsonArray() }) { }
    internal BlockTableRecord(JsonObject s) : base(s) { }
    public override string DxfName => "BLOCK_RECORD";
    internal bool ContentsLoaded { get; init; }
    internal List<string> KnownAttributeTags { get; set; } = [];
    public bool IsLayout => Name.Equals(ModelSpace, StringComparison.OrdinalIgnoreCase);
    public bool IsFromExternalReference => false; // AX-CAD lists no external references (plan §22.5)
    public bool IsAnonymous => Name.StartsWith('*') && !IsLayout;
    public bool HasAttributeDefinitions => IsNewObject ? Ids().Any(i => i.Target is AttributeDefinition) : KnownAttributeTags.Count > 0;
    public Point3d Origin
    {
        get => new(S["origin"]![0]!.GetValue<double>(), S["origin"]![1]!.GetValue<double>());
        set
        {
            AssertWriteEnabled();
            if (!IsNewObject) throw new AxCadException(0, "NOT_SUPPORTED", "a drawing block's base point is changed by redefining it in the 2D screen");
            S["origin"] = new JsonArray(ModelSpaceChecks.Finite(value.X), ModelSpaceChecks.Finite(value.Y));
        }
    }
    internal override void CheckName(string n)
    {
        base.CheckName(n.StartsWith('*') && IsLayout ? "M" : n);
        if (n.StartsWith('*') && !n.Equals(ModelSpace, StringComparison.OrdinalIgnoreCase)) throw new AxCadException(0, "BAD_NAME", "anonymous (*) blocks are not made by scripts");
    }
    IEnumerable<ObjectId> Ids()
    {
        if (!IsNewObject && !ContentsLoaded) throw new AxCadException(0, "NOT_LOADED", $"{Name}: a drawing block's contents stay on the server (AX-CAD sends the model space only)");
        return ((JsonArray)S["ents"]!).Select(i => new ObjectId(Db!, i!.GetValue<int>())).Where(id => !id.IsErased);
    }
    public IEnumerator<ObjectId> GetEnumerator() => Ids().ToList().GetEnumerator();
    IEnumerator IEnumerable.GetEnumerator() => GetEnumerator();

    internal void Load(Entity e)
    {
        e.OwnerId = ObjectId;
        ((JsonArray)S["ents"]!).Add(e.Index);
    }
    /// <summary>Puts a new entity in this space or block (open it ForWrite); returns its id.</summary>
    public ObjectId AppendEntity(Entity entity)
    {
        AssertWriteEnabled();
        if (Db is null) throw new AxCadException(0, "NOT_IN_DB", "add the block to the block table first");
        if (!IsLayout && !IsNewObject) throw new AxCadException(0, "NOT_SUPPORTED", $"{Name}: a drawing block is redefined in the 2D screen (scripts add to blocks they make)");
        if (entity is AttributeReference) throw new AxCadException(0, "WRONG_TYPE", "attribute references go in BlockReference.AttributeCollection");
        if (entity is AttributeDefinition && IsLayout) throw new AxCadException(0, "NOT_SUPPORTED", "attribute definitions go in a block");
        if (entity is BlockReference br && !IsLayout && Reaches(br.BlockTableRecord, this)) throw new AxCadException(0, "SELF_REFERENCE", $"{Name} would contain itself");
        entity.CheckLayer(Db);
        var id = Db.Add(entity, this);
        ((JsonArray)S["ents"]!).Add(entity.Index);
        return id;
    }
    static bool Reaches(ObjectId from, BlockTableRecord target)
    {
        var seen = new HashSet<int>();
        bool Walk(BlockTableRecord b) => b == target || (b.IsNewObject && seen.Add(b.Index) && b.Ids().Any(i => i.Target is BlockReference r && Walk((BlockTableRecord)r.BlockTableRecord.Target)));
        return !from.IsNull && Walk((BlockTableRecord)from.Target);
    }
    public override void Erase(bool erasing)
    {
        if (IsLayout) throw new AxCadException(0, "NOT_SUPPORTED", "the model space stays");
        AssertWriteEnabled();
        if (erasing && Db!.Objects.OfType<BlockReference>().Any(r => !r.IsErased && r.BlockTableRecord == ObjectId)) throw new AxCadException(0, "BLOCK_IN_USE", $"{Name} is in use");
        S["erased"] = erasing;
    }

    internal override void Emit(JsonObject body)
    {
        if (IsLayout) return;
        var ops = Database.List(body, "blocks");
        if (IsNewObject)
        {
            if (IsErased) return;
            var ents = new JsonArray();
            var defs = new JsonArray();
            var o = Origin;
            foreach (var id in Ids())
            {
                if (id.Target is AttributeDefinition a) defs.Add(a.Def(o));
                else ents.Add(((Entity)id.Target).Edit(withHandle: false, onlyChanged: false));
            }
            if (ents.Count + defs.Count == 0) return; // an empty block is not saved
            var op = new JsonObject { ["op"] = "create", ["name"] = Name, ["base"] = new JsonArray(o.X, o.Y), ["entities"] = ents };
            if (defs.Count > 0) op["attdefs"] = defs;
            ops.Add(op);
            return;
        }
        if (IsErased) ops.Add(new JsonObject { ["op"] = "delete", ["name"] = BaseName });
        else if (Name != BaseName) ops.Add(new JsonObject { ["op"] = "rename", ["name"] = BaseName, ["new_name"] = Name });
    }
}

// --- dictionaries ------------------------------------------------------------------------------

public readonly record struct DBDictionaryEntry(string Key, ObjectId Value);

/// <summary>The named object dictionary, a dictionary in it, or an entity's extension dictionary.
/// Entries are XRecords and dictionaries; an XRecord set here is saved with its path.</summary>
public sealed class DBDictionary : DBObject, IEnumerable<DBDictionaryEntry>
{
    public DBDictionary() : base(new JsonObject { ["entries"] = new JsonObject() }) { }
    public override string DxfName => "DICTIONARY";
    internal bool Partial; // the payload did not carry it (too many entries)
    internal DBObject? ExtensionOf;
    JsonObject Entries => (JsonObject)S["entries"]!;

    internal void LoadTree(Database db, JsonObject tree)
    {
        foreach (var (key, node) in tree)
        {
            if (node is not JsonObject n) continue;
            var type = n["type"]?.GetValue<string>();
            DBObject child;
            if (type == "DICTIONARY")
            {
                var d = db.Register(new DBDictionary(), n["handle"]?.GetValue<string>());
                if (n["entries"] is JsonObject sub) d.LoadTree(db, sub);
                else d.Partial = true; // sent as a reference only (the same object under another key)
                child = d;
            }
            else if (type == "XRECORD")
            {
                var x = db.Register(new Xrecord(), n["handle"]?.GetValue<string>());
                x.S["tags"] = n["tags"]?.DeepClone() ?? new JsonArray();
                child = x;
            }
            else continue;
            child.OwnerId = ObjectId;
            Entries[key] = child.Index;
        }
    }
    void Readable()
    {
        if (Partial) throw new AxCadException(0, "NOT_LOADED", "this dictionary did not come with the drawing (too many entries): use GET /api/revisions/{id}/dictionaries");
    }
    public int Count
    {
        get
        {
            Readable();
            return this.Count();
        }
    }
    public bool Contains(string key) => Find(key) is not null;
    string? Find(string key)
    {
        Readable();
        return Entries.Select(kv => kv.Key).FirstOrDefault(k => k.Equals(key, StringComparison.OrdinalIgnoreCase) && !Child(k).IsErased);
    }
    DBObject Child(string key) => Db!.Object(Entries[key]!.GetValue<int>());
    public ObjectId GetAt(string key) => Find(key) is { } k ? Child(k).ObjectId : throw new AxCadException(0, "KEY_NOT_FOUND", $"no entry {key}");
    /// <summary>Puts an XRecord or a dictionary under a key (open this dictionary ForWrite).</summary>
    public ObjectId SetAt(string key, DBObject value)
    {
        AssertWriteEnabled();
        if (Db is null) throw new AxCadException(0, "NOT_IN_DB", "the dictionary is not in a database");
        if (value is not (Xrecord or DBDictionary)) throw new AxCadException(0, "WRONG_TYPE", "dictionaries hold XRecords and dictionaries");
        if (key.Length is 0 or > 255 || key.Any(char.IsControl)) throw new AxCadException(0, "BAD_KEY", "a dictionary key is one line of 1..255 characters");
        if (Depth() >= 7) throw new AxCadException(0, "TOO_DEEP", "dictionaries nest 8 deep at most");
        if (Find(key) is { } old) Child(old).S["erased"] = true; // replaced
        var id = Db.Add(value, this);
        Entries[key] = value.Index;
        return id;
    }
    public void Remove(string key)
    {
        AssertWriteEnabled();
        var k = Find(key) ?? throw new AxCadException(0, "KEY_NOT_FOUND", $"no entry {key}");
        Child(k).S["erased"] = true;
    }
    public void Remove(ObjectId id)
    {
        var k = Entries.FirstOrDefault(kv => kv.Value!.GetValue<int>() == id.Target.Index).Key ?? throw new AxCadException(0, "KEY_NOT_FOUND", "not in this dictionary");
        Remove(k);
    }
    public string NameAt(ObjectId id) => Entries.FirstOrDefault(kv => kv.Value!.GetValue<int>() == id.Target.Index).Key ?? throw new AxCadException(0, "KEY_NOT_FOUND", "not in this dictionary");
    public IEnumerator<DBDictionaryEntry> GetEnumerator()
    {
        Readable();
        return Entries.Where(kv => !Child(kv.Key).IsErased).Select(kv => new DBDictionaryEntry(kv.Key, Child(kv.Key).ObjectId)).ToList().GetEnumerator();
    }
    IEnumerator IEnumerable.GetEnumerator() => GetEnumerator();

    int Depth() => OwnerId.IsNull || OwnerId.Target is not DBDictionary p ? 0 : 1 + p.Depth();
    /// <summary>The path from the root dictionary and the entity whose extension dictionary that root is.</summary>
    internal (List<string> Path, string? Handle) PathOf(DBObject child)
    {
        var path = new List<string> { NameAt(child.ObjectId) };
        var d = this;
        while (!d.OwnerId.IsNull && d.OwnerId.Target is DBDictionary up)
        {
            path.Insert(0, up.NameAt(d.ObjectId));
            d = up;
        }
        var owner = d.ExtensionOf ?? (d.OwnerId.IsNull ? null : d.OwnerId.Target);
        return (path, owner?.FileHandle);
    }
    internal override void Emit(JsonObject body) { } // an empty dictionary is made by the XRecords set in it
}

public sealed class Xrecord : DBObject
{
    public Xrecord() : base(new JsonObject { ["tags"] = new JsonArray() }) { }
    public override string DxfName => "XRECORD";
    public ResultBuffer? Data
    {
        get => new(((JsonArray)S["tags"]!).Select(t => TypedValue.FromJson(t!.AsArray())).ToArray());
        set
        {
            AssertWriteEnabled();
            var tags = new JsonArray();
            foreach (var tv in value ?? new ResultBuffer())
            {
                if (tv.TypeCode is 0 or 5 or 100 or 102 or 105 or >= 1000) throw new AxCadException(0, "BAD_XRECORD", $"group code {tv.TypeCode} is not for XRecord data");
                tags.Add(tv.ToJson());
            }
            if (tags.Count > 10_000) throw new AxCadException(0, "BAD_XRECORD", "10,000 groups at most");
            S["tags"] = tags;
        }
    }
    internal override void Emit(JsonObject body)
    {
        if (OwnerId.IsNull || OwnerId.Target is not DBDictionary d) return;
        var gone = IsErased || d.IsErased;
        if (IsNewObject && gone) return;
        if (!IsNewObject && !gone && !IsModified) return;
        var (path, handle) = d.PathOf(this);
        var op = new JsonObject { ["path"] = new JsonArray(path.Select(p => (JsonNode)p).ToArray()), ["op"] = gone ? "delete" : "set" };
        if (handle is not null) op["handle"] = handle;
        if (!gone) op["xrecord"] = S["tags"]!.DeepClone();
        Database.List(body, "dictionaries").Add(op);
    }
}

internal static class ModelSpaceChecks
{
    internal static double Finite(double v) => double.IsFinite(v) && Math.Abs(v) <= 1e9 ? v : throw new AxCadException(0, "BAD_GEOMETRY", "value out of range");
    internal static double Positive(double v, string what) => Finite(v) > 0 ? v : throw new AxCadException(0, "BAD_GEOMETRY", $"{what} must be > 0");
    internal static double Deg(double rad) => Finite(rad) * 180 / Math.PI;
    internal static double Rad(double deg) => deg * Math.PI / 180;
    internal static double Norm(double deg) => ((deg % 360) + 360) % 360;
    static readonly int[] Weights = [0, 5, 9, 13, 15, 18, 20, 25, 30, 35, 40, 50, 53, 60, 70, 80, 90, 100, 106, 120, 140, 158, 200, 211];
    /// <summary>AutoCAD's lineweights (1/100 mm), -3 default; -1 BYLAYER, -2 BYBLOCK for entities.</summary>
    internal static int LineWeight(int v, bool layer) => Weights.Contains(v) || v == -3 || (!layer && v is -1 or -2) ? v : throw new AxCadException(0, "BAD_VALUE", $"not a lineweight: {v}");
}
