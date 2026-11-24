using System.Globalization;
using System.Text;
using System.Text.Json;

namespace Cleanyfin.Marking;

/// <summary>Closed-object reading with the same lexical rules as the delivery readers.</summary>
internal static class StrictJson
{
    public const int MaxDocumentBytes = 1024 * 1024;
    public const long MaxJsonInteger = 9_007_199_254_740_991;
    private static readonly UTF8Encoding StrictUtf8 = new(false, true);
    private static readonly string[] TimestampFormats = ["yyyy-MM-dd'T'HH:mm:ss'Z'", "yyyy-MM-dd'T'HH:mm:ss.FFFFFFF'Z'"];

    public static MarkingResult<T> Parse<T>(ReadOnlyMemory<byte> utf8, Func<JsonElement, T> read) where T : class
    {
        if (utf8.IsEmpty)
            return MarkingResult<T>.Refuse(MarkingRefusal.InvalidJson);
        if (utf8.Length > MaxDocumentBytes)
            return MarkingResult<T>.Refuse(MarkingRefusal.LimitExceeded);
        try
        {
            _ = StrictUtf8.GetCharCount(utf8.Span);
            using var doc = JsonDocument.Parse(utf8, new JsonDocumentOptions { MaxDepth = 8 });
            return MarkingResult<T>.Allow(read(doc.RootElement));
        }
        catch (MarkingRejected e)
        {
            return MarkingResult<T>.Refuse(e.Reason, e.DeliveryReason, e.Line);
        }
        catch (Exception e) when (e is JsonException or DecoderFallbackException)
        {
            return MarkingResult<T>.Refuse(MarkingRefusal.InvalidJson);
        }
    }

    public static Dictionary<string, JsonElement> Object(JsonElement element, params string[] names)
    {
        Check(element.ValueKind == JsonValueKind.Object, MarkingRefusal.InvalidValue);
        var fields = new Dictionary<string, JsonElement>(StringComparer.Ordinal);
        foreach (var property in element.EnumerateObject())
        {
            var name = property.Name;
            Check(names.Any(n => n[0] == '?' ? n.AsSpan(1).SequenceEqual(name.AsSpan()) : n == name), MarkingRefusal.UnknownField);
            Check(fields.TryAdd(name, property.Value), MarkingRefusal.DuplicateKey);
        }
        foreach (var name in names)
            Check(name[0] == '?' || fields.ContainsKey(name), MarkingRefusal.MissingField);
        return fields;
    }

    public static JsonElement? Optional(Dictionary<string, JsonElement> fields, string name) =>
        fields.TryGetValue(name, out var value) ? value : null;

    public static IEnumerable<JsonElement> Array(JsonElement value, int max, int min = 0)
    {
        Check(value.ValueKind == JsonValueKind.Array, MarkingRefusal.InvalidValue);
        var count = value.GetArrayLength();
        Check(count <= max, MarkingRefusal.LimitExceeded);
        Check(count >= min, MarkingRefusal.InvalidValue);
        return value.EnumerateArray();
    }

    public static long Integer(JsonElement value, long min = -MaxJsonInteger, long max = MaxJsonInteger)
    {
        Check(value.ValueKind == JsonValueKind.Number, MarkingRefusal.InvalidValue);
        Check(value.TryGetInt64(out var number), MarkingRefusal.InvalidValue);
        // Reject 5480.0 and 5.48e3: only plain base-10 integer spellings are allowed.
        Check(value.GetRawText().AsSpan().IndexOfAny('.', 'e', 'E') < 0, MarkingRefusal.InvalidValue);
        Check(number >= min && number <= max, MarkingRefusal.InvalidValue);
        return number;
    }

    public static bool Boolean(JsonElement value)
    {
        Check(value.ValueKind is JsonValueKind.True or JsonValueKind.False, MarkingRefusal.InvalidValue);
        return value.GetBoolean();
    }

    public static string Text(JsonElement value, int max = 256, MarkingRefusal reason = MarkingRefusal.InvalidValue)
    {
        Check(value.ValueKind == JsonValueKind.String, reason);
        var text = value.GetString()!;
        Check(text.Length > 0, reason);
        Check(text.Length <= max, MarkingRefusal.LimitExceeded);
        return text;
    }

    public static string OneOf(JsonElement value, IEnumerable<string> allowed, MarkingRefusal reason = MarkingRefusal.InvalidValue)
    {
        var text = Text(value, reason: reason);
        Check(allowed.Contains(text), reason);
        return text;
    }

    public static string Reference(JsonElement value)
    {
        var text = Text(value);
        Check(IsReference(text), MarkingRefusal.InvalidValue);
        return text;
    }

    public static bool IsReference(string? text) => text is { Length: > 0 and <= 256 } &&
        char.IsAsciiLetterOrDigit(text[0]) && text.All(c => char.IsAsciiLetterOrDigit(c) || c is ':' or '-' or '_' or '.');

    public static bool IsConceptId(string? text)
    {
        if (text is not { Length: > 0 and <= 128 })
            return false;
        int colon = text.IndexOf(':');
        if (colon < 1 || colon == text.Length - 1 || !char.IsAsciiLetterLower(text[0]))
            return false;
        return text[..colon].All(c => char.IsAsciiLetterLower(c) || char.IsAsciiDigit(c)) &&
            (char.IsAsciiLetterLower(text[colon + 1]) || char.IsAsciiDigit(text[colon + 1])) &&
            text[(colon + 1)..].All(c => char.IsAsciiLetterLower(c) || char.IsAsciiDigit(c) || c is '_' or '-');
    }

    public static string Digest(JsonElement value)
    {
        var text = Text(value);
        Check(IsDigest(text), MarkingRefusal.InvalidValue);
        return text;
    }

    public static bool IsDigest(string? text) => text is { Length: 64 } && text.All(c => char.IsAsciiDigit(c) || c is >= 'a' and <= 'f');

    public static DateTimeOffset Timestamp(JsonElement value)
    {
        var text = Text(value, reason: MarkingRefusal.InvalidTimestamp);
        if ((text.Length == 20 || text.Length is >= 22 and <= 28 && text[19] == '.') &&
            DateTimeOffset.TryParseExact(text, TimestampFormats, CultureInfo.InvariantCulture,
                DateTimeStyles.AssumeUniversal | DateTimeStyles.AdjustToUniversal, out var parsed))
            return parsed;
        throw new MarkingRejected(MarkingRefusal.InvalidTimestamp);
    }

    /// <summary>UTC with only as many fraction digits as needed; whole seconds have none.</summary>
    public static string FormatUtc(DateTimeOffset time)
    {
        var utc = time.UtcDateTime;
        var whole = utc.ToString("yyyy-MM-dd'T'HH:mm:ss", CultureInfo.InvariantCulture);
        var fraction = utc.Ticks % TimeSpan.TicksPerSecond;
        return fraction == 0 ? whole + "Z"
            : whole + "." + fraction.ToString("D7", CultureInfo.InvariantCulture).TrimEnd('0') + "Z";
    }

    public static void Check(bool ok, MarkingRefusal reason)
    {
        if (!ok)
            throw new MarkingRejected(reason);
    }
}
