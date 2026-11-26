using System;
using System.Collections.Generic;
using System.Globalization;
using System.IO;
using System.Linq;
using System.Security.Cryptography;
using System.Text;
using System.Text.Json;
using System.Text.Json.Nodes;
using System.Threading.Tasks;
using Cleanyfin.Delivery;
using Cleanyfin.Marking;
using Jellyfin.Plugin.Cleanyfin.AuthRange;
using MediaBrowser.Controller.Entities;
using MediaBrowser.Controller.Net;
using MediaBrowser.Model.Entities;
using Microsoft.AspNetCore.Authorization;
using Microsoft.AspNetCore.Http;
using Microsoft.AspNetCore.Mvc;

namespace Jellyfin.Plugin.Cleanyfin.Marking;

/// <summary>
/// The household marking API (slice 2) and the two pages that use it, all on Jellyfin's own origin. Every action is
/// taken as the authenticated Jellyfin user; request bodies carry values only, never an actor or a role, and every
/// role check happens here on the server through <see cref="MarkingService"/>. API keys are refused: they name no person.
/// </summary>
[ApiController]
[Authorize]
[Route("Cleanyfin/marking")]
public sealed class MarkingController(IAuthorizationContext auth) : ControllerBase
{
    private const int MaxBody = 64 * 1024;
    private static readonly string[] Assets = ["common.js", "capture.js", "capture-core.js", "review.js", "marking.css"];

    private HouseholdMarking Household => (HouseholdMarking)HttpContext.RequestServices.GetService(typeof(HouseholdMarking))!;

    // ---------------- pages (static, no data; everything else needs a login) ----------------

    [AllowAnonymous, HttpGet("capture")]
    public IActionResult CapturePage() => Page("capture.html", "text/html; charset=utf-8");

    [AllowAnonymous, HttpGet("review")]
    public IActionResult ReviewPage() => Page("review.html", "text/html; charset=utf-8");

    [AllowAnonymous, HttpGet("assets/{name}")]
    public IActionResult Asset(string name) => Assets.Contains(name)
        ? Page(name, name.EndsWith(".css", StringComparison.Ordinal) ? "text/css; charset=utf-8" : "text/javascript; charset=utf-8")
        : NotFound();

    private IActionResult Page(string name, string type)
    {
        using var s = typeof(MarkingController).Assembly.GetManifestResourceStream("Cleanyfin.Marking.Web." + name);
        if (s == null)
            return NotFound();
        using var m = new MemoryStream();
        s.CopyTo(m);
        var h = Response.Headers;
        h.CacheControl = "no-cache";
        h["X-Content-Type-Options"] = "nosniff";
        h["Referrer-Policy"] = "no-referrer";
        h["Content-Security-Policy"] = "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; " +
            "media-src 'self'; connect-src 'self'; object-src 'none'; base-uri 'none'; frame-ancestors 'none'; form-action 'none'";
        return File(m.ToArray(), type);
    }

    // ---------------- identity ----------------

    [HttpGet("me")]
    public async Task<IActionResult> Me() => await Act(null, (h, user) => h.Use(user, s => Json(new JsonObject
    {
        ["user"] = s.Actor,
        ["roles"] = new JsonArray(s.Roles.Select(r => (JsonNode)r!).ToArray()),
        ["mayMark"] = s.Governance.MayMark(s.Actor),
        ["mayReview"] = s.Governance.MayReview(s.Actor),
        ["suggester"] = s.Governance.IsSuggester(s.Actor),
        ["selfApproval"] = s.Governance.Approval.SelfApproval,
        ["governanceRev"] = s.Governance.Rev,
        ["governanceSource"] = s.GovernanceSource,
        ["serverRef"] = h.ServerRef,
    })));

    [HttpGet("test-playback")]
    public async Task<IActionResult> TestPlayback() => await Act(null, (h, user) => h.Use(user, s =>
    {
        var enabled = HouseholdMarking.TestPlayback;
        var reports = new JsonArray();
        if (enabled && System.IO.File.Exists(Path.Combine(Plugin.Instance!.ShelfRoot, "evidence-register-v2.json")))
            foreach (var row in PointerEvidence.Load(HttpContext, test: true).Register.Rows.Where(r => PointerEvidence.CurrentBuild(r.Tuple, h.ServerVersion)))
                reports.Add(new JsonObject { ["layout"] = row.Tuple.Source.Container + ": " + row.Tuple.Source.TrackLayout,
                    ["player"] = row.Tuple.AppId + " " + row.Tuple.AppVersion + " · " + row.Tuple.DeviceMode + " · Jellyfin " + row.Tuple.JellyfinVersion,
                    ["silence"] = row.TestSeams!.Silence, ["pop"] = row.TestSeams.Pop, ["picture"] = row.TestSeams.KeptPicture,
                    ["sync"] = row.TestSeams.Sync, ["loss"] = row.TestSeams.KeptSoundLoss });
        return Json(new JsonObject { ["enabled"] = enabled, ["canChange"] = s.Roles.Contains(Roles.Admin), ["reports"] = reports });
    }));

    [HttpPost("test-playback/{setting}")]
    public async Task<IActionResult> ChangeTestPlayback(string setting) => await Act(null, (h, user) => h.Use(user, s =>
    {
        if (!s.Roles.Contains(Roles.Admin)) return Refuse(MarkingRefusal.NotPermitted);
        if (setting is not ("on" or "off")) return BadRequest();
        HouseholdMarking.SetTestPlayback(setting == "on");
        return NoContent();
    }));

    // ---------------- captures ----------------

    /// <summary>One capture-v1, as taken. Re-sending the identical capture is harmless (offline queue retries).</summary>
    [HttpPost("captures")]
    public async Task<IActionResult> AddCapture() => await Act(await Body(), (h, user, body) =>
    {
        var parsed = MarkingDocuments.ParseCapture(body);
        if (!parsed.Accepted)
            return Refuse(parsed.Reason!.Value);
        var capture = parsed.Value!;
        if (capture.Target.ServerRef != h.ServerRef || capture.Target.MediaSourceId != capture.Target.ItemId)
            return Refuse("BindingMismatch", 422);
        if (h.Visible(capture.Target.ItemId, user) == null)
            return h.IsFilteredPointer(capture.Target.ItemId) ? Refuse("FilteredItemNotSupported", 422) : Refuse("UnknownItem", 404);
        return h.Use(user, s =>
        {
            // The author is the person signed in. A body cannot speak for someone else.
            if (capture.Author != s.Actor)
                return Refuse(MarkingRefusal.NotPermitted);
            if (s.Store.FindCapture(capture.Ref) is { } existing)
                return MarkingDocuments.WriteCapture(existing).AsSpan().SequenceEqual(MarkingDocuments.WriteCapture(capture))
                    ? Json(new JsonObject { ["status"] = "already-stored", ["ref"] = capture.Ref })
                    : Refuse(MarkingRefusal.DuplicateReference);
            var added = s.Service.AddCapture(capture);
            return added.Accepted
                ? Json(new JsonObject { ["status"] = "stored", ["ref"] = capture.Ref }, 201)
                : Refuse(added.Reason!.Value);
        });
    });

    // ---------------- review data ----------------

    /// <summary>Items with captures or marks that the caller may see.</summary>
    [HttpGet("items")]
    public async Task<IActionResult> Items() => await Act(null, (h, user) => h.Use(user, s =>
    {
        var reviewer = s.Governance.MayMark(s.Actor);
        var ids = s.Store.Captures().Where(c => reviewer || c.Author == s.Actor).Select(c => c.Target.ItemId)
            .Concat(reviewer ? s.Store.LatestMarks().Select(m => ItemOf(m.BindingRev)).OfType<string>() : [])
            .Distinct(StringComparer.Ordinal).Order(StringComparer.Ordinal);
        var list = new JsonArray();
        foreach (var id in ids)
        {
            if (h.Visible(id, user) is not { } video)
                continue;
            list.Add(new JsonObject
            {
                ["itemId"] = id, ["name"] = video.Name,
                ["captures"] = s.Store.Captures().Count(c => c.Target.ItemId == id && (reviewer || c.Author == s.Actor)),
                ["marks"] = reviewer ? s.Store.LatestMarks().Count(m => ItemOf(m.BindingRev) == id) : 0,
            });
        }
        return Json(new JsonObject { ["items"] = list });
    }));

    /// <summary>
    /// Captures and current marks for one item, its verified binding and frame rate. Markers and reviewers see the whole
    /// household's work on the item; anyone else sees only their own captures.
    /// </summary>
    [HttpGet("items/{itemId}")]
    public async Task<IActionResult> Item(string itemId) => await Act(null, (h, user) =>
    {
        if (h.Visible(itemId, user) is not { } video)
            return Refuse("UnknownItem", 404);
        SourceBinding? binding = null;
        string? bindingRefusal = null;
        try { binding = h.Bind(video); }
        catch (Refused r) { bindingRefusal = r.Reason.ToString(); }
        var edition = h.EditionStatus(video);
        var stream = video.GetMediaSources(false).FirstOrDefault()?.MediaStreams.FirstOrDefault(m => m.Type == MediaStreamType.Video);
        return h.Use(user, s =>
        {
            var team = s.Governance.MayMark(s.Actor);
            var captures = new JsonArray(s.Store.Captures().Where(c => c.Target.ItemId == itemId && (team || c.Author == s.Actor))
                .Select(c => JsonNode.Parse(MarkingDocuments.WriteCapture(c))).ToArray());
            var marks = new JsonArray();
            if (team)
                foreach (var m in s.Store.LatestMarks().Where(m => ItemOf(m.BindingRev) == itemId))
                {
                    var node = JsonNode.Parse(MarkingDocuments.WriteMark(m))!.AsObject();
                    // Marks stay with the exact file they were made on. A changed file is shown, never carried over.
                    node["fileChanged"] = binding == null || m.BindingRev != binding.BindingRev;
                    marks.Add(node);
                }
            return Json(new JsonObject
            {
                ["item"] = new JsonObject
                {
                    ["itemId"] = itemId, ["name"] = video.Name, ["mediaSourceId"] = itemId,
                    ["runtimeMs"] = (video.RunTimeTicks ?? 0) / 10_000,
                    ["frameRate"] = stream?.RealFrameRate is { } fps ? JsonValue.Create((double)fps) : null,
                },
                ["binding"] = binding == null ? null : JsonNode.Parse(MarkingDocuments.WriteBinding(binding)),
                ["bindingRefusal"] = bindingRefusal,
                // The file check: queued / verifying (with bytes done of total) / verified / refused.
                ["edition"] = new JsonObject
                {
                    ["state"] = edition.State, ["bytesDone"] = edition.Done, ["bytesTotal"] = edition.Total, ["reason"] = edition.Reason,
                },
                // Open marks made on another edition of this item: they stay, never move, and block publishing.
                ["reviewAgain"] = team && binding != null ? s.Service.StaleMarks(binding).Count() : 0,
                ["captures"] = captures,
                ["marks"] = marks,
            });
        });
    });

    // ---------------- marks ----------------

    /// <summary>A draft over a capture's search window, or a mark placed at exact values the caller supplies.</summary>
    [HttpPost("marks")]
    public async Task<IActionResult> CreateMark() => await Act(await Body(), (h, user, body) =>
    {
        var parsed = MarkingRequests.ParseDraft(body);
        if (!parsed.Accepted)
            return Refuse(parsed.Reason!.Value);
        var request = parsed.Value!;
        var target = h.Use(user, s => s.Store.FindCapture(request.CaptureRef)?.Target.ItemId);
        if (target == null || h.Visible(target, user) is not { } video)
            return Refuse(MarkingRefusal.UnknownCapture);
        var binding = h.Bind(video);
        return h.Use(user, s =>
        {
            var markRef = "mark:" + Guid.NewGuid().ToString("N");
            var made = request.Exact is { } exact
                ? s.Service.PlaceFromCapture(request.CaptureRef, markRef, binding.BindingRev, exact, s.Actor)
                : s.Service.CaptureToDraftMark(request.CaptureRef, markRef, binding.BindingRev, s.Actor);
            return made.Accepted ? MarkJson(made.Value!, binding, 201) : Refuse(made.Reason!.Value);
        });
    });

    /// <summary>Exact bounds and classification as a new revision; the caller names the revision they edited.</summary>
    [HttpPut("marks/{markRef}")]
    public async Task<IActionResult> EditMark(string markRef) => await Act(await Body(), (h, user, body) =>
    {
        var parsed = MarkingRequests.ParseEdit(body);
        if (!parsed.Accepted)
            return Refuse(parsed.Reason!.Value);
        return OnMark(h, user, markRef, s =>
        {
            var edited = s.Service.Edit(markRef, parsed.Value!.Rev, parsed.Value.Edit, s.Actor);
            return edited.Accepted ? MarkJson(edited.Value!, null) : Refuse(edited.Reason!.Value);
        });
    });

    [HttpPost("marks/{markRef}/accept")]
    public Task<IActionResult> Accept(string markRef) => Decide(markRef, (s, rev) => s.Service.Accept(markRef, rev, s.Actor));

    [HttpPost("marks/{markRef}/reject")]
    public Task<IActionResult> Reject(string markRef) => Decide(markRef, (s, rev) => s.Service.Reject(markRef, rev, s.Actor));

    [HttpPost("marks/{markRef}/withdraw")]
    public Task<IActionResult> Withdraw(string markRef) => Decide(markRef, (s, rev) => s.Service.Withdraw(markRef, rev, s.Actor));

    private async Task<IActionResult> Decide(string markRef, Func<MarkingSession, int, MarkingResult<AuditEvent>> decide) =>
        await Act(await Body(), (h, user, body) =>
        {
            var parsed = MarkingRequests.ParseRevision(body);
            if (!parsed.Accepted)
                return Refuse(parsed.Reason!.Value);
            return OnMark(h, user, markRef, s =>
            {
                var decided = decide(s, parsed.Value!.Rev);
                return decided.Accepted ? Json(EventJson(decided.Value!)) : Refuse(decided.Reason!.Value);
            });
        });

    private IActionResult OnMark(HouseholdMarking h, Guid user, string markRef, Func<MarkingSession, IActionResult> body)
    {
        var item = h.Use(user, s => s.Store.FindMark(markRef) is { } m ? ItemOf(m.BindingRev) : null);
        if (item == null || h.Visible(item, user) == null)
            return Refuse(MarkingRefusal.UnknownMark);
        return h.Use(user, body);
    }

    // ---------------- publish and audit ----------------

    /// <summary>
    /// Accepted marks for the item's current binding become annotation-snapshot-v1, written to the shelf's
    /// <c>snapshots/&lt;item&gt;.json</c>, where the existing resolver reads it. Reviewers only.
    /// </summary>
    [HttpPost("items/{itemId}/publish")]
    public async Task<IActionResult> Publish(string itemId) => await Act(await Body(), (h, user, body) =>
    {
        var parsed = MarkingRequests.ParsePublish(body);
        if (!parsed.Accepted)
            return Refuse(parsed.Reason!.Value);
        if (h.Visible(itemId, user) is not { } video)
            return Refuse("UnknownItem", 404);
        var binding = h.Bind(video);
        return h.Use(user, s =>
        {
            var rev = "home:snap:" + DateTimeOffset.UtcNow.ToString("yyyyMMdd'T'HHmmssfffffff", CultureInfo.InvariantCulture);
            var published = s.Service.Publish(binding, s.Actor, rev, parsed.Value!.ExpiresAt);
            if (!published.Accepted)
                return Refuse(published.Reason!.Value);
            HouseholdMarking.WriteSnapshot(itemId, published.Value!.Utf8);
            return Json(new JsonObject
            {
                ["snapshotRev"] = rev,
                ["annotations"] = published.Value.Snapshot.Annotations.Length,
                ["sha256"] = published.Value.Event.Digest,
                ["auditSeq"] = published.Value.Event.Seq,
                ["bindingRev"] = binding.BindingRev,
            });
        });
    });

    /// <summary>The audit log's length and head digest, after verifying the whole chain and its recorded head.</summary>
    [HttpGet("audit")]
    public async Task<IActionResult> Audit() => await Act(null, (h, user) => h.Use(user, s =>
    {
        if (!s.Governance.MayMark(s.Actor))
            return Refuse(MarkingRefusal.NotPermitted);
        var lines = s.Store.AuditLines();
        return Json(new JsonObject { ["verified"] = true, ["count"] = lines.Count, ["head"] = PluginMarkingStore.Head(lines) });
    }));

    /// <summary>Which browsers have a measured frame-accurate timing result for the review page.</summary>
    [HttpGet("timing")]
    public async Task<IActionResult> Timing() => await Act(null, (_, _) =>
    {
        GrantPipeline.Private(HttpContext);
        return File(HouseholdMarking.TimingRegister(), "application/json");
    });

    // ---------------- plumbing ----------------

    private async Task<byte[]?> Body()
    {
        using var m = new MemoryStream();
        var buffer = new byte[8192];
        int read;
        while ((read = await Request.Body.ReadAsync(buffer, HttpContext.RequestAborted)) > 0)
        {
            if (m.Length + read > MaxBody)
                return null;
            m.Write(buffer, 0, read);
        }
        return m.ToArray();
    }

    private Task<IActionResult> Act(byte[]? body, Func<HouseholdMarking, Guid, IActionResult> run) =>
        Act(body ?? [], (h, u, _) => run(h, u));

    private async Task<IActionResult> Act(byte[]? body, Func<HouseholdMarking, Guid, byte[], IActionResult> run)
    {
        GrantPipeline.Private(HttpContext);
        var a = await auth.GetAuthorizationInfo(HttpContext);
        if (!a.IsAuthenticated || a.IsApiKey || a.User == null || a.UserId == Guid.Empty)
            return Refuse("NotAPerson", 403);
        if (body == null)
            return Refuse(MarkingRefusal.LimitExceeded);
        try
        {
            return run(Household, a.UserId, body);
        }
        catch (StoreUnreadable e)
        {
            var o = new JsonObject { ["refusal"] = e.Message };
            if (e.Line is { } line)
                o["line"] = line;
            Response.Headers["X-Cleanyfin-Refusal"] = e.Message;
            return Json(o, 409);
        }
        catch (Refused r)
        {
            return Refuse(r.Reason.ToString(), 422);
        }
        catch (Exception e) when (e is IOException or UnauthorizedAccessException)
        {
            return Refuse("StoreUnavailable", 503);
        }
    }

    private static string? ItemOf(string bindingRev)
    {
        var parts = bindingRev.Split(':');
        return parts.Length == 3 && parts[0] == "bind" && GrantTable<Binding>.IsHandle(parts[1]) ? parts[1] : null;
    }

    private IActionResult MarkJson(Mark m, SourceBinding? binding, int status = 200)
    {
        var node = JsonNode.Parse(MarkingDocuments.WriteMark(m))!.AsObject();
        if (binding != null)
            node["fileChanged"] = m.BindingRev != binding.BindingRev;
        return Json(node, status);
    }

    private static JsonObject EventJson(AuditEvent e) => new()
    {
        ["seq"] = e.Seq, ["type"] = e.Type, ["subject"] = e.Subject, ["actor"] = e.Actor, ["digest"] = e.Digest,
    };

    private IActionResult Refuse(MarkingRefusal reason) => Refuse(reason.ToString(), reason switch
    {
        MarkingRefusal.NotPermitted or MarkingRefusal.UnknownMember or MarkingRefusal.SecondReviewerRequired => 403,
        MarkingRefusal.UnknownCapture or MarkingRefusal.UnknownMark => 404,
        MarkingRefusal.StaleRevision or MarkingRefusal.DuplicateReference or MarkingRefusal.NotProposed => 409,
        MarkingRefusal.AuditChainBroken => 409,
        MarkingRefusal.LimitExceeded => 413,
        MarkingRefusal.InvalidJson or MarkingRefusal.DuplicateKey or MarkingRefusal.UnknownField or
            MarkingRefusal.MissingField or MarkingRefusal.UnsupportedVersion => 400,
        _ => 422,
    });

    private IActionResult Refuse(string reason, int status)
    {
        Response.Headers["X-Cleanyfin-Refusal"] = reason;
        return Json(new JsonObject { ["refusal"] = reason }, status);
    }

    private static ContentResult Json(JsonNode node, int status = 200) => new()
    {
        Content = node.ToJsonString(), ContentType = "application/json; charset=utf-8", StatusCode = status,
    };
}
