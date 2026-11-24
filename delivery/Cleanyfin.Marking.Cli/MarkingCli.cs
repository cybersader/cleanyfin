using System.Text;
using Cleanyfin.Delivery;

namespace Cleanyfin.Marking.Cli;

/// <summary>
/// Thin lab CLI over <see cref="MarkingService"/>. It never reads media, never talks to Jellyfin and writes only the
/// store directory and one snapshot (or policy) file.
/// </summary>
public static class MarkingCli
{
    public const string Usage = """
        cleanyfin-mark review --store DIR --governance FILE --binding FILE --captures FILE --decisions FILE
                              --actor USER --snapshot-rev REV [--taxonomy FILE] [--expires UTC]
                              (--out FILE | --shelf-root DIR)
            Imports a phone capture export, turns each decision (exact source-clock bounds) into a mark, accepts it
            as --actor (audit-logged) and publishes annotation-snapshot-v1. --shelf-root writes
            <root>/snapshots/<binding itemId>.json, the file the household plugin reads.
        cleanyfin-mark compile-policy --governance FILE --profile REF [--prefs FILE] --out FILE
            Governance floor + viewer preferences -> household-policy-v1 (stricter wins).
        cleanyfin-mark verify-audit --store DIR
            Checks the audit log's hash chain.
        """;

    public static int Run(string[] args, TextWriter output, TimeProvider clock)
    {
        try
        {
            if (args.Length == 0)
                return UsageError(output, "missing command");
            var options = Options(args[1..]);
            return args[0] switch
            {
                "review" => Review(options, output, clock),
                "compile-policy" => CompilePolicy(options, output),
                "verify-audit" => VerifyAudit(options, output),
                _ => UsageError(output, $"unknown command {args[0]}")
            };
        }
        catch (UsageException e)
        {
            return UsageError(output, e.Message);
        }
        catch (Refused e)
        {
            output.WriteLine($"refused: {e.Message}");
            return 1;
        }
        catch (Exception e) when (e is IOException or InvalidDataException or UnauthorizedAccessException)
        {
            output.WriteLine($"refused: {e.Message}");
            return 1;
        }
    }

    private static int Review(Dictionary<string, string> o, TextWriter output, TimeProvider clock)
    {
        var governance = Read(o, "governance", MarkingDocuments.ParseGovernance);
        var taxonomy = o.ContainsKey("taxonomy") ? Read(o, "taxonomy", MarkingDocuments.ParseTaxonomy) : Taxonomy.Empty;
        var binding = Read(o, "binding", MarkingDocuments.ParseBinding);
        var export = Read(o, "captures", MarkingDocuments.ParseCaptureExport);
        var decisions = Read(o, "decisions", MarkingDocuments.ParseDecisions);
        var actor = Required(o, "actor");
        var snapshotRev = Required(o, "snapshot-rev");
        DateTimeOffset? expires = o.TryGetValue("expires", out var e) ? ParseUtc(e) : null;
        var outFile = Destination(o, binding);

        var store = new FileMarkingStore(Required(o, "store"));
        var service = new MarkingService(store, governance, taxonomy, clock, store);

        foreach (var capture in export.Captures)
        {
            if (store.FindCapture(capture.Ref) is { } existing)
            {
                // Re-importing the same export is harmless; a different capture under a known ref is not.
                if (!MarkingDocuments.WriteCapture(existing).AsSpan().SequenceEqual(MarkingDocuments.WriteCapture(capture)))
                    throw new Refused($"capture {capture.Ref} differs from the stored capture");
                continue;
            }
            Require(capture.Target.ItemId == binding.ItemId && capture.Target.MediaSourceId == binding.MediaSourceId &&
                capture.Target.ServerRef == binding.ServerRef, $"capture {capture.Ref} is for a different item or source");
            Check(service.AddCapture(capture), $"capture {capture.Ref}");
        }

        foreach (var decision in decisions.Marks)
        {
            var capture = store.FindCapture(decision.CaptureRef) ?? throw new Refused($"unknown capture {decision.CaptureRef}");
            var exact = decision.Exact;
            if (exact.Category is null && exact.Subcategory is null)
                exact = exact with { Category = CoreCategories.CoreFromHint(capture.Category)
                    ?? throw new Refused($"mark {decision.MarkRef}: the capture's hint does not name one core category; set category") };
            var latest = store.FindMark(decision.MarkRef);
            if (latest is null)
            {
                var draft = service.CaptureToDraftMark(capture.Ref, decision.MarkRef, binding.BindingRev, actor);
                if (draft.Reason == MarkingRefusal.UnknownPlan)
                {
                    // Unmapped filtered capture: the reviewer's exact source bounds are the manual placement.
                    latest = Check(service.PlaceFromCapture(capture.Ref, decision.MarkRef, binding.BindingRev, exact, actor),
                        $"mark {decision.MarkRef}");
                    output.WriteLine($"placed {decision.MarkRef} manually (capture {capture.Ref} is on an unknown filtered plan)");
                }
                else
                    latest = Check(draft, $"mark {decision.MarkRef}");
            }
            Require(latest.BindingRev == binding.BindingRev, $"mark {latest.Ref} belongs to another binding; review it again");
            if (latest.BoundsSource != Mark.ReviewerBounds || latest.StartMs != exact.StartMs || latest.EndMs != exact.EndMs ||
                latest.Severity != exact.Severity || latest.Action != exact.Action || latest.Subcategory != exact.Subcategory ||
                (exact.Category is not null && latest.Category != exact.Category) || latest.State is not ("proposed" or "accepted"))
                latest = Check(service.Edit(latest.Ref, latest.Rev, exact, actor), $"mark {latest.Ref}");
            if (latest.State == "proposed")
            {
                var accepted = Check(service.Accept(latest.Ref, latest.Rev, actor), $"accept {latest.Ref}");
                output.WriteLine($"accepted {accepted.Subject} by {accepted.Actor} (audit #{accepted.Seq})");
            }
        }

        var published = Check(service.Publish(binding, actor, snapshotRev, expires), "publish");
        WriteAtomic(outFile, published.Utf8);
        output.WriteLine($"published {snapshotRev} with {published.Snapshot.Annotations.Length} mark(s) to {outFile} " +
            $"(sha256 {published.Event.Digest}, audit #{published.Event.Seq})");
        return 0;
    }

    private static int CompilePolicy(Dictionary<string, string> o, TextWriter output)
    {
        var governance = Read(o, "governance", MarkingDocuments.ParseGovernance);
        HouseholdPolicy? prefs = o.ContainsKey("prefs") ? Read(o, "prefs", MarkingDocuments.ParsePolicy) : null;
        var compiled = Check(MarkingService.CompilePolicy(governance, Required(o, "profile"), prefs), "compile-policy");
        var outFile = Required(o, "out");
        WriteAtomic(outFile, compiled.Utf8);
        output.WriteLine($"compiled {compiled.Policy.PolicyRev} with {compiled.Policy.Rules.Length} rule(s) to {outFile}");
        return 0;
    }

    private static int VerifyAudit(Dictionary<string, string> o, TextWriter output)
    {
        var path = Path.Combine(Required(o, "store"), "audit.jsonl");
        var lines = File.Exists(path) ? File.ReadAllText(path, new UTF8Encoding(false, true)).Split('\n').Where(l => l.Length > 0).ToList() : [];
        var verified = AuditLog.Verify(lines);
        if (!verified.Accepted)
        {
            output.WriteLine($"refused: audit chain broken at line {verified.Line}");
            return 1;
        }
        var head = lines.Count == 0 ? AuditLog.Genesis : AuditLog.LineDigest(lines[^1]);
        output.WriteLine($"audit chain intact: {lines.Count} event(s), head {head}");
        return 0;
    }

    private static string Destination(Dictionary<string, string> o, SourceBinding binding)
    {
        bool hasOut = o.TryGetValue("out", out var file), hasShelf = o.TryGetValue("shelf-root", out var root);
        if (hasOut == hasShelf)
            throw new UsageException("give exactly one of --out or --shelf-root");
        if (hasOut)
            return file!;
        // The plugin reads <root>/snapshots/<item>.json for a 32-hex Jellyfin item id; refuse anything else.
        Require(binding.ItemId.Length == 32 && binding.ItemId.All(c => char.IsAsciiDigit(c) || c is >= 'a' and <= 'f'),
            "binding itemId is not a 32-hex Jellyfin item id");
        var snapshots = Path.Combine(root!, "snapshots");
        Directory.CreateDirectory(snapshots);
        return Path.Combine(snapshots, binding.ItemId + ".json");
    }

    private static void WriteAtomic(string path, byte[] bytes)
    {
        var full = Path.GetFullPath(path);
        Directory.CreateDirectory(Path.GetDirectoryName(full)!);
        var tmp = full + "." + Guid.NewGuid().ToString("N") + ".tmp";
        try
        {
            using (var f = new FileStream(tmp, FileMode.CreateNew, FileAccess.Write, FileShare.None))
            {
                f.Write(bytes);
                f.Flush(true);
            }
            File.Move(tmp, full, overwrite: true);
        }
        finally
        {
            if (File.Exists(tmp))
                File.Delete(tmp);
        }
    }

    private static T Read<T>(Dictionary<string, string> o, string name, Func<ReadOnlyMemory<byte>, MarkingResult<T>> parse)
        where T : class => Check(parse(File.ReadAllBytes(Required(o, name))), $"--{name}");

    private static T Check<T>(MarkingResult<T> result, string what) where T : class =>
        result.Value ?? throw new Refused($"{what}: {result}");

    private static void Require(bool ok, string message)
    {
        if (!ok)
            throw new Refused(message);
    }

    private static string Required(Dictionary<string, string> o, string name) =>
        o.TryGetValue(name, out var value) ? value : throw new UsageException($"missing --{name}");

    private static DateTimeOffset ParseUtc(string text) =>
        DateTimeOffset.TryParseExact(text, ["yyyy-MM-dd'T'HH:mm:ss'Z'", "yyyy-MM-dd'T'HH:mm:ss.FFFFFFF'Z'"],
            System.Globalization.CultureInfo.InvariantCulture,
            System.Globalization.DateTimeStyles.AssumeUniversal | System.Globalization.DateTimeStyles.AdjustToUniversal, out var t)
            ? t : throw new UsageException("--expires must be UTC like 2026-11-21T19:00:00Z");

    private static Dictionary<string, string> Options(string[] args)
    {
        var options = new Dictionary<string, string>(StringComparer.Ordinal);
        for (int i = 0; i < args.Length; i += 2)
        {
            if (!args[i].StartsWith("--", StringComparison.Ordinal) || i + 1 >= args.Length)
                throw new UsageException($"expected --name value at {args[i]}");
            if (!options.TryAdd(args[i][2..], args[i + 1]))
                throw new UsageException($"{args[i]} given twice");
        }
        return options;
    }

    private static int UsageError(TextWriter output, string message)
    {
        output.WriteLine($"usage error: {message}");
        output.WriteLine(Usage);
        return 2;
    }

    private sealed class UsageException(string message) : Exception(message);
    private sealed class Refused(string message) : Exception(message);
}
