using System;
using System.IO;

namespace Jellyfin.Plugin.Cleanyfin;

/// <summary>
/// OpenSubtitles-style "moviehash" (OSHash): filesize + the sum of the first and
/// last 64 KiB read as little-endian UInt64 words, rendered as 16 hex chars.
/// This is the release fingerprint that maps a crowdsourced segment to the RIGHT
/// file/rip (decision R04). It is fast (never reads the whole file) and is the
/// same scheme OpenSubtitles has used at scale for subtitles.
/// </summary>
public static class Moviehash
{
    private const int ChunkSize = 64 * 1024; // 65536

    /// <summary>
    /// The fingerprint cleanyfin keys segments on: "osh:{moviehash}" when the
    /// file is readable, else a "jf:{itemId}" fallback so marking still works.
    /// Plugin provider and fingerprint endpoint MUST agree via this one method.
    /// </summary>
    public static string Fingerprint(string? path, Guid itemId)
    {
        if (!string.IsNullOrEmpty(path))
        {
            var hash = FromFile(path);
            if (hash is not null)
            {
                return "osh:" + hash;
            }
        }

        return "jf:" + itemId.ToString("N");
    }

    /// <summary>Computes the moviehash of a file, or null if it cannot be read.</summary>
    public static string? FromFile(string path)
    {
        try
        {
            using var fs = File.OpenRead(path);
            return FromStream(fs, fs.Length);
        }
        catch
        {
            return null;
        }
    }

    /// <summary>Computes the moviehash of a stream of the given total size.</summary>
    public static string FromStream(Stream stream, long size)
    {
        ulong hash = (ulong)size;
        var buffer = new byte[ChunkSize];

        // First 64 KiB.
        var n = ReadUpTo(stream, buffer);
        hash = AddChunks(hash, buffer, n);

        // Last 64 KiB.
        var tail = Math.Max(0, size - ChunkSize);
        stream.Seek(tail, SeekOrigin.Begin);
        n = ReadUpTo(stream, buffer);
        hash = AddChunks(hash, buffer, n);

        return hash.ToString("x16");
    }

    private static ulong AddChunks(ulong hash, byte[] buffer, int len)
    {
        var words = len / 8;
        for (var i = 0; i < words; i++)
        {
            // OSHash reads little-endian words; all Jellyfin platforms are LE.
            hash = unchecked(hash + BitConverter.ToUInt64(buffer, i * 8));
        }

        return hash;
    }

    private static int ReadUpTo(Stream stream, byte[] buffer)
    {
        var total = 0;
        while (total < buffer.Length)
        {
            var read = stream.Read(buffer, total, buffer.Length - total);
            if (read == 0)
            {
                break;
            }

            total += read;
        }

        return total;
    }
}
