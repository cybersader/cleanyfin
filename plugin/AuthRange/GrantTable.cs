using System;
using System.Buffers.Binary;
using System.Collections.Generic;
using System.Linq;
using System.Security.Cryptography;
using System.Text;
using Cleanyfin.Delivery;

namespace Jellyfin.Plugin.Cleanyfin.AuthRange;

internal enum Leg { List, Source, Subtitle }

// One row per offer. Neither raw A/B nor rendered text containing B is retained.
// B is reproducible only with A and a process-local random PRF key. Never serialize rows.
internal sealed class GrantTable<T>(TimeProvider clock, int capacity = 64) where T : class
{
    private sealed record Row(T Binding, string ListHash, string SourceHash, string SubtitleHash, DateTimeOffset Expires);
    private readonly Dictionary<string, Row> _rows = new(StringComparer.Ordinal);
    private readonly byte[] _key = RandomNumberGenerator.GetBytes(32);
    private DateTimeOffset _watermark;
    internal long Issued { get; private set; }

    internal static string Hash(string s) => Convert.ToHexStringLower(SHA256.HashData(Encoding.UTF8.GetBytes(s)));
    internal static bool IsHandle(string? s) => s is { Length: 32 } && s.All(c => c is >= '0' and <= '9' or >= 'a' and <= 'f');
    private static string RandomHandle() => Convert.ToHexStringLower(RandomNumberGenerator.GetBytes(16));
    internal UInt128 SourceFor(string list) => Derive(list);
    internal UInt128 SubtitleFor(string list) => Derive("subtitle:" + list);
    private UInt128 Derive(string label) => BinaryPrimitives.ReadUInt128BigEndian(HMACSHA256.HashData(_key, Encoding.ASCII.GetBytes(label)));

    internal RefusalCode? Issue(T binding, DateTimeOffset expires, out string slot, out string list)
    {
        slot = list = string.Empty;
        lock (_rows)
        {
            if (!Advance(out var now)) return RefusalCode.ClockRollback;
            if (expires <= now) return RefusalCode.Expired;
            foreach (var key in _rows.Where(p => p.Value.Expires <= now).Select(p => p.Key).ToArray()) _rows.Remove(key);
            if (_rows.Count >= capacity) return RefusalCode.LimitExceeded;
            slot = RandomHandle(); list = RandomHandle();
            var source = SourceFor(list);
            var subtitle = SubtitleFor(list);
            if (source == 0 || subtitle == 0 || source == subtitle || slot == new string('0', 32) ||
                list == source.ToString("x32") || list == subtitle.ToString("x32") || _rows.ContainsKey(slot)) return RefusalCode.InvalidIdentifier;
            _rows.Add(slot, new Row(binding, Hash(list), Hash(source.ToString("x32")), Hash(subtitle.ToString("x32")), expires));
            Issued++;
            return null;
        }
    }

    internal RefusalCode? Check(string slot, string ticket, Leg leg, string method, out T? binding)
    {
        binding = null;
        if (!IsHandle(slot) || !IsHandle(ticket) || method is not ("HEAD" or "GET")) return RefusalCode.InvalidIdentifier;
        lock (_rows)
        {
            if (!Advance(out var now)) return RefusalCode.ClockRollback;
            if (!_rows.TryGetValue(slot, out var row) || !CryptographicOperations.FixedTimeEquals(
                    Convert.FromHexString(leg switch { Leg.List => row.ListHash, Leg.Source => row.SourceHash, _ => row.SubtitleHash }),
                    Convert.FromHexString(Hash(ticket)))) return RefusalCode.InvalidIdentifier;
            if (row.Expires <= now) { _rows.Remove(slot); return RefusalCode.Expired; }
            binding = row.Binding;
            return null;
        }
    }

    internal void Revoke(Predicate<T> matches)
    {
        lock (_rows)
            foreach (var id in _rows.Where(p => matches(p.Value.Binding)).Select(p => p.Key).ToArray()) _rows.Remove(id);
    }

    private bool Advance(out DateTimeOffset now)
    {
        now = clock.GetUtcNow();
        if (now < _watermark) { _rows.Clear(); return false; }
        _watermark = now;
        return true;
    }
}
