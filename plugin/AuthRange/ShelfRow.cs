using System;
using System.Collections.Generic;
using System.Linq;

namespace Jellyfin.Plugin.Cleanyfin.AuthRange;

// The filename never identifies an original. The private row is the authority, and its opaque key
// is allocated once, not derived from title, profile, source, input generation or plan revision.
internal sealed record ShelfRow(string Key, string Viewer, string Item, string Source, string Profile,
    string Timeline, string Generation, long Ticks, string Locator)
{
    internal static ShelfRow? Reusable(IEnumerable<ShelfRow> inventory, string viewer, string item,
        string timeline, Uri origin) => inventory.SingleOrDefault(r => r.Viewer == viewer && r.Item == item &&
            r.Profile == viewer && r.Timeline == timeline && r.Locator == InertLocator(origin, r.Key));

    internal static string InertLocator(Uri origin, string key) => origin.AbsoluteUri.TrimEnd('/') + "/Cleanyfin/pointer/" + key;

    internal static bool ValidInventory(IReadOnlyCollection<ShelfRow> rows, int limit) => rows.Count <= limit &&
        rows.All(r => r is not null && GrantTable<object>.IsHandle(r.Key) && GrantTable<object>.IsHandle(r.Viewer) &&
            GrantTable<object>.IsHandle(r.Item) && r.Source == r.Item && r.Profile == r.Viewer && r.Ticks > 0 &&
            r.Locator is not null && r.Locator.EndsWith("/Cleanyfin/pointer/" + r.Key, StringComparison.Ordinal)) &&
        rows.Select(r => r.Key).Distinct(StringComparer.Ordinal).Count() == rows.Count;
}
