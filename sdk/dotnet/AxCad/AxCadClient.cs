// AX-CAD .NET SDK (2D automation A3, plan.md §20): automate AX-CAD from C# through its REST API —
// the same rules, roles and audit log as the screens; nothing runs inside the app. The same calls
// as the Python SDK (sdk/axcad.py), plus Drawing: a revision as AutoCAD-style objects.
//
//     var cad = AxCadClient.Local();                       // this PC's install (install token)
//     var cad = new AxCadClient("https://cad.example", token: await AxCadClient.LoginTokenAsync(...));
//
// Errors throw AxCadException(Status, Code, Message) with the API's error code.

using System.Net.Http.Headers;
using System.Text;
using System.Text.Json;
using System.Text.Json.Nodes;

namespace AxCad;

public sealed class AxCadException : Exception
{
    public AxCadException(int status, string code, string message) : base($"{status} {code}: {message}")
    {
        Status = status;
        Code = code;
        Detail = message;
    }

    public int Status { get; }
    public string Code { get; }
    public string Detail { get; }
}

public sealed class AxCadClient : IDisposable
{
    static readonly string[] Loopback = ["127.0.0.1", "localhost", "::1", "[::1]"];
    public static readonly TimeSpan Timeout = TimeSpan.FromSeconds(300);

    readonly HttpClient _http;
    readonly bool _ownsHttp;

    public Uri BaseUri { get; }

    /// <param name="handler">for tests: a fake transport; it must not follow redirects</param>
    public AxCadClient(string baseUrl, string? token = null, string? localToken = null, HttpMessageHandler? handler = null)
    {
        if (!Uri.TryCreate(baseUrl.TrimEnd('/'), UriKind.Absolute, out var uri) || (uri.Scheme != "http" && uri.Scheme != "https"))
            throw new AxCadException(0, "BAD_URL", "use https (plain http only for this PC)");
        var loopback = Loopback.Contains(uri.Host);
        if (uri.Scheme == "http" && !loopback && token is not null)
            throw new AxCadException(0, "BAD_URL", "use https (plain http only for this PC)");
        if (localToken is not null && !loopback)
            throw new AxCadException(0, "BAD_URL", "the install token is only sent to this PC");
        BaseUri = uri;
        // a redirect would carry the Authorization / install-token headers elsewhere: never follow
        _ownsHttp = handler is null;
        _http = new HttpClient(handler ?? new HttpClientHandler { AllowAutoRedirect = false }) { Timeout = Timeout };
        if (token is not null) _http.DefaultRequestHeaders.Authorization = new AuthenticationHeaderValue("Bearer", token);
        if (localToken is not null) _http.DefaultRequestHeaders.Add("X-AXCAD-Local", localToken);
    }

    public void Dispose()
    {
        if (_ownsHttp) _http.Dispose();
    }

    // --- connection ------------------------------------------------------------------------

    /// <summary>The per-PC install: its API on 127.0.0.1 and the install token (AXCAD_LOCAL_TOKEN,
    /// or the axcad.env of %LOCALAPPDATA%\AX-CAD\data).</summary>
    public static AxCadClient Local(int port = 18000, string? token = null)
    {
        token ??= Environment.GetEnvironmentVariable("AXCAD_LOCAL_TOKEN") ?? EnvToken();
        if (string.IsNullOrEmpty(token))
            throw new AxCadException(0, "NO_LOCAL_TOKEN", "AXCAD_LOCAL_TOKEN not set and no axcad.env found");
        return new AxCadClient($"http://127.0.0.1:{port}", localToken: token);
    }

    public static async Task<string> LoginTokenAsync(string baseUrl, string loginId, string password, HttpMessageHandler? handler = null, CancellationToken ct = default)
    {
        var u = new Uri(baseUrl);
        if (u.Scheme != "https" && !Loopback.Contains(u.Host))
            throw new AxCadException(0, "BAD_URL", "use https (plain http only for this PC)");
        using var c = new AxCadClient(baseUrl, handler: handler);
        var data = await c.PostAsync("/api/auth/login", new { login_id = loginId, password }, ct);
        return data.GetProperty("access_token").GetString()!;
    }

    static string? EnvToken()
    {
        var baseDir = Environment.GetEnvironmentVariable("LOCALAPPDATA");
        if (string.IsNullOrEmpty(baseDir)) return null;
        try
        {
            foreach (var line in File.ReadAllLines(Path.Combine(baseDir, "AX-CAD", "data", "axcad.env")))
                if (line.StartsWith("LOCAL_TOKEN=", StringComparison.Ordinal))
                    return line["LOCAL_TOKEN=".Length..].Trim() is { Length: > 0 } t ? t : null;
        }
        catch (IOException)
        {
        }
        catch (UnauthorizedAccessException)
        {
        }
        return null;
    }

    // --- raw calls -------------------------------------------------------------------------

    static readonly JsonSerializerOptions Json = new() { DefaultIgnoreCondition = System.Text.Json.Serialization.JsonIgnoreCondition.WhenWritingNull };

    /// <summary>The API's data on success; JSON responses come back as a JsonElement, files as byte[].</summary>
    public async Task<object> RequestAsync(HttpMethod method, string path, object? body = null, HttpContent? raw = null, CancellationToken ct = default)
    {
        if (!path.StartsWith('/')) throw new ArgumentException("path must start with /", nameof(path));
        using var req = new HttpRequestMessage(method, new Uri(BaseUri, path));
        if (body is not null) req.Content = new StringContent(JsonSerializer.Serialize(body, Json), Encoding.UTF8, "application/json");
        else if (raw is not null) req.Content = raw;
        using var resp = await _http.SendAsync(req, ct);
        var payload = await resp.Content.ReadAsByteArrayAsync(ct);
        var status = (int)resp.StatusCode;
        var json = resp.Content.Headers.ContentType?.MediaType == "application/json";
        if (!json)
        {
            if (status >= 300) // a redirect is not followed (it would carry the token)
                throw new AxCadException(status, "HTTP_ERROR", Encoding.UTF8.GetString(payload, 0, Math.Min(200, payload.Length)));
            return payload;
        }
        using var doc = JsonDocument.Parse(payload.Length == 0 ? "{}"u8.ToArray() : payload);
        var root = doc.RootElement;
        if (!root.TryGetProperty("success", out var ok) || ok.ValueKind != JsonValueKind.True)
        {
            var err = root.TryGetProperty("error", out var e) && e.ValueKind == JsonValueKind.Object ? e : default;
            string Get(string k) => err.ValueKind == JsonValueKind.Object && err.TryGetProperty(k, out var v) && v.ValueKind == JsonValueKind.String ? v.GetString()! : "";
            throw new AxCadException(status, Get("code") is { Length: > 0 } c ? c : "ERROR", Get("message"));
        }
        return root.GetProperty("data").Clone();
    }

    public async Task<JsonElement> GetAsync(string path, CancellationToken ct = default) => AsJson(await RequestAsync(HttpMethod.Get, path, ct: ct));
    public async Task<JsonElement> PostAsync(string path, object? body = null, CancellationToken ct = default) => AsJson(await RequestAsync(HttpMethod.Post, path, body ?? new { }, ct: ct));
    public async Task<byte[]> GetBytesAsync(string path, CancellationToken ct = default) =>
        await RequestAsync(HttpMethod.Get, path, ct: ct) is byte[] b ? b : throw new AxCadException(0, "NOT_A_FILE", $"{path} returned JSON, not a file");

    static JsonElement AsJson(object o) => o is JsonElement e ? e : throw new AxCadException(0, "NOT_JSON", "the API returned a file, not JSON");
    static string Esc(string s) => Uri.EscapeDataString(s);

    // --- projects and documents ------------------------------------------------------------

    public Task<JsonElement> CreateProjectAsync(string code, string name, bool needsQuote = true, CancellationToken ct = default) =>
        PostAsync("/api/projects", new { project_code = code, project_name = name, needs_quote = needsQuote }, ct);

    public Task<JsonElement> CreateDocumentAsync(long projectId, string docNo, string docType, string title, bool? needsQuote = null, CancellationToken ct = default) =>
        PostAsync($"/api/projects/{projectId}/documents", new { doc_no = docNo, doc_type = docType, title, needs_quote = needsQuote }, ct);

    public async Task<JsonElement> UploadDxfAsync(long documentId, string path, CancellationToken ct = default)
    {
        var name = new string(Path.GetFileName(path).Where(c => c is not ('"' or '\r' or '\n' or '\\')).ToArray());
        using var form = new MultipartFormDataContent();
        var file = new ByteArrayContent(await File.ReadAllBytesAsync(path, ct));
        file.Headers.ContentType = new MediaTypeHeaderValue("application/dxf");
        form.Add(file, "file", name);
        return AsJson(await RequestAsync(HttpMethod.Post, $"/api/documents/{documentId}/dxf", raw: form, ct: ct));
    }

    /// <summary>A blank drawing (sheet A4..A0) as the document's first revision.</summary>
    public Task<JsonElement> BlankDrawingAsync(long documentId, string sheet = "A3", bool portrait = false, CancellationToken ct = default) =>
        PostAsync($"/api/documents/{documentId}/blank", new { sheet, portrait }, ct);

    /// <summary>A revision of a 2D drawing as AutoCAD-style objects; SaveAsync makes the next revision.</summary>
    public async Task<Drawing> OpenDrawingAsync(string revisionId, CancellationToken ct = default) =>
        new(this, revisionId, await GetAsync($"/api/revisions/{Esc(revisionId)}/render", ct));

    // --- 3D model --------------------------------------------------------------------------

    public Task<JsonElement> AddFeatureAsync(long documentId, string featureType, object parameters, CancellationToken ct = default) =>
        PostAsync($"/api/documents/{documentId}/features", new { feature_type = featureType, @params = parameters }, ct);

    public async Task<JsonElement> FeaturesAsync(long documentId, CancellationToken ct = default) =>
        (await GetAsync($"/api/documents/{documentId}/features", ct)).GetProperty("items");

    public Task<JsonElement> SketchAsync(long documentId, IEnumerable<JsonObject> entities, object? plane = null, CancellationToken ct = default)
    {
        var ents = new JsonArray();
        var i = 0;
        foreach (var e in entities)
        {
            var copy = (JsonObject)e.DeepClone();
            copy["id"] = ++i;
            ents.Add(copy);
        }
        plane ??= new { origin = new[] { 0, 0, 0 }, normal = new[] { 0, 0, 1 }, xdir = new[] { 1, 0, 0 } };
        return AddFeatureAsync(documentId, "SKETCH", new { plane, entities = ents, constraints = Array.Empty<object>() }, ct);
    }

    public Task<byte[]> ExportModelAsync(long documentId, string format = "STEP", CancellationToken ct = default) =>
        GetBytesAsync($"/api/documents/{documentId}/model?format={Esc(format)}", ct);

    // --- sheet metal -----------------------------------------------------------------------

    public Task<JsonElement> SheetBaseAsync(long documentId, long sketchId, double thickness, double? bendRadius = null, double kFactor = 0.44, CancellationToken ct = default) =>
        AddFeatureAsync(documentId, "SHEET_BASE", new { sketch_feature_id = sketchId, thickness, bend_radius = bendRadius, k_factor = kFactor }, ct);

    public Task<JsonElement> FlangeAsync(long documentId, long targetId, int edge, double length, double angle = 90, bool up = true, double insetStart = 0, double insetEnd = 0, CancellationToken ct = default) =>
        AddFeatureAsync(documentId, "SHEET_FLANGE", new { target_feature_id = targetId, edge, length, angle, up, inset_start = insetStart, inset_end = insetEnd }, ct);

    public Task<JsonElement> FlatPatternAsync(long featureId, CancellationToken ct = default) => GetAsync($"/api/features/{featureId}/sheet", ct);
    public Task<byte[]> FlatDxfAsync(long featureId, CancellationToken ct = default) => GetBytesAsync($"/api/features/{featureId}/sheet?format=dxf", ct);

    // --- assemblies ------------------------------------------------------------------------

    public Task<JsonElement> AddComponentAsync(long assemblyId, long featureId, double[]? t = null, double[]? r = null, bool? @fixed = null, CancellationToken ct = default) =>
        PostAsync($"/api/documents/{assemblyId}/components", new { source_feature_id = featureId, placement = new { t = t ?? [0, 0, 0], r = r ?? [0, 0, 0] }, @fixed }, ct);

    public Task<JsonElement> AddMateAsync(long assemblyId, string mateType, (long Component, JsonElement Face) a, (long Component, JsonElement Face) b, double? value = null, bool flip = false, CancellationToken ct = default) =>
        PostAsync($"/api/documents/{assemblyId}/mates", new { mate_type = mateType, a = new { component_id = a.Component, face = a.Face }, b = new { component_id = b.Component, face = b.Face }, value, flip }, ct);

    public Task<JsonElement> InterferenceAsync(long assemblyId, double clearance = 0, CancellationToken ct = default) =>
        PostAsync($"/api/documents/{assemblyId}/interference", new { clearance }, ct);

    /// <summary>Face signatures of a body (what a mate or a fillet pick sends).</summary>
    public async Task<List<JsonElement>> FacesAsync(long featureId, CancellationToken ct = default) =>
        (await GetAsync($"/api/features/{featureId}/mesh", ct)).GetProperty("faces").EnumerateArray().Select(f => f.GetProperty("sig").Clone()).ToList();

    // --- parametric blocks and quotes ------------------------------------------------------

    public Task<JsonElement> ParamBlockAsync(string template, IDictionary<string, double> values, string? state = null, CancellationToken ct = default) =>
        PostAsync($"/api/param-blocks/{Esc(template)}/instantiate", new { values, state }, ct);

    public Task<JsonElement> QuoteModelAsync(long documentId, int qty, string materialCode, CancellationToken ct = default) =>
        PostAsync("/api/quotes", new { document_id = documentId, qty, material_code = materialCode }, ct);

    public Task<JsonElement> QuoteDrawingAsync(string revisionId, int qty, string materialCode, string thicknessMm, CancellationToken ct = default) =>
        PostAsync("/api/quotes", new { revision_id = revisionId, qty, material_code = materialCode, thickness_mm = thicknessMm }, ct);

    public Task<byte[]> QuoteReportAsync(long quoteId, string format = "xlsx", CancellationToken ct = default) =>
        GetBytesAsync($"/api/quotes/{quoteId}/report?format={Esc(format)}", ct);

}
