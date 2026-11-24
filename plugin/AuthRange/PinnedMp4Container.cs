using System;
using System.Linq;

namespace Jellyfin.Plugin.Cleanyfin.AuthRange;

// These labels describe the same demuxer family, not an asset identity. Only
// use them with the independently pinned and physically checked MP4 fixture.
internal static class PinnedMp4Container
{
    private static bool IsAlias(string name) => name is "mov" or "mp4" or "m4a" or "3gp" or "3g2" or "mj2";

    // A source declaration must contain only family names. A client profile
    // instead advertises alternatives and is allowed to support other formats.
    internal static bool DescribesSource(string? names) => names?.Split(',').All(IsAlias) == true;
    internal static bool OfferedByProfile(string? names) => names?.Split(',').Any(IsAlias) == true;
}
