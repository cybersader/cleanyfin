using System.Collections.Immutable;
using System.Security.Cryptography;
using System.Text;
using System.Text.Json.Nodes;
using Cleanyfin.Delivery;
using Cleanyfin.Marking;
using Cleanyfin.Marking.Cli;
using Json.Schema;

namespace Marking.Contract.Tests;

[Trait("Category", "Marking")]
public class MarkingTests
{
    internal const string Parent = "jf:user:3b1c0000000000000000000000000001";
    internal const string SecondParent = "jf:user:parent2";
    internal const string Spouse = "jf:user:spouse";
    internal const string Kid = "jf:user:kid";
    internal const string Service = "svc:suggest";
    internal const string MarkRef = "home:fixture:mark1";
    internal static readonly DateTimeOffset Issued = new(2026, 11, 21, 18, 0, 0, TimeSpan.Zero);
    internal static readonly Governance Gov = Require(MarkingDocuments.ParseGovernance(Fixture("governance.v1.json")));
    internal static readonly Taxonomy Tax = Require(MarkingDocuments.ParseTaxonomy(Fixture("taxonomy.v1.json")));
    internal static readonly SourceBinding Binding = Require(MarkingDocuments.ParseBinding(Fixture("binding.v1.json")));
    internal static readonly ImmutableArray<Capture> Phone =
        Require(MarkingDocuments.ParseCaptureExport(Fixture("pwa-export.captures.json"))).Captures;
    private static readonly MarkEdit Golden = new(5480, 10520, "violence", null, 2, "skip");

    // ---------- the chain ----------

    [Fact]
    public void PhoneExportThroughCliAndExistingResolverGivesTheHandPlacedGoldenPlan()
    {
        var dir = Scratch();
        try
        {
            var shelf = Path.Combine(dir, "shelf");
            var output = new StringWriter();
            var exit = MarkingCli.Run(
            [
                "review", "--store", Path.Combine(dir, "store"), "--governance", FixturePath("governance.v1.json"),
                "--taxonomy", FixturePath("taxonomy.v1.json"), "--binding", FixturePath("binding.v1.json"),
                "--captures", FixturePath("pwa-export.captures.json"), "--decisions", FixturePath("reviewer-decisions.json"),
                "--actor", Parent, "--snapshot-rev", "home:review:9", "--expires", "2026-11-21T19:00:00Z", "--shelf-root", shelf
            ], output, new FixedClock(Issued));
            Assert.True(exit == 0, output.ToString());
            var written = File.ReadAllBytes(Path.Combine(shelf, "snapshots", Binding.ItemId + ".json"));

            // The published file is an ordinary annotation-snapshot-v1 the plugin already reads.
            Assert.True(SchemaAccepts("annotation-snapshot-v1.schema.json", written));
            var published = ResolverInputReader.ParseSnapshot(written);
            Assert.True(published.Accepted, published.Reason?.ToString());
            var handPlaced = ResolverInputReader.ParseSnapshot(GoldenFile("hard-fixture.snapshot.v1.json")).Value!;
            var policy = ResolverInputReader.ParsePolicy(GoldenFile("hard-fixture.policy.v1.json")).Value!;

            var fromPhone = Resolve(published.Value!, policy);
            var fromHand = Resolve(handPlaced, policy);
            // Byte-identical plan, so the mpv adapter and evidence gates see no difference.
            Assert.Equal(fromHand.Utf8.ToArray(), fromPhone.Utf8.ToArray());
            Assert.Equal(fromHand.TimelineKey, fromPhone.TimelineKey);
            Assert.Equal(JsonNode.Parse(GoldenFile("hard-fixture.v1.json"))!["operations"]!.ToJsonString(),
                JsonNode.Parse(fromPhone.Utf8.ToArray())!["operations"]!.ToJsonString());
            var edl = MpvEdlRenderer.Render(fromPhone.Plan, new Uri("https://jellyfin.example"), new MpvSourceIdentifiers(1, 2),
                MpvClockMapping.VerifiedSourceIdentity);
            Assert.Equal(GoldenFile("hard-fixture.mpv.edl"), edl.Value);

            // The household floor compiled from governance filters the same marks.
            var compiled = Require(MarkingService.CompilePolicy(Gov, "local:p2", null));
            Assert.True(SchemaAccepts("household-policy-v1.schema.json", compiled.Utf8));
            var viaGovernance = Resolve(published.Value!, compiled.Policy);
            Assert.Equal(fromHand.Plan.Operations.Single().StartMs, viaGovernance.Plan.Operations.Single().StartMs);
            Assert.Equal(fromHand.Plan.Operations.Single().EndMs, viaGovernance.Plan.Operations.Single().EndMs);
            Assert.Equal(fromHand.TimelineKey, viaGovernance.TimelineKey);

            // Every hop is on the record, and the record holds together.
            var verify = new StringWriter();
            Assert.Equal(0, MarkingCli.Run(["verify-audit", "--store", Path.Combine(dir, "store")], verify, TimeProvider.System));
            var events = Require(AuditLog.Verify(File.ReadAllLines(Path.Combine(dir, "store", "audit.jsonl"))));
            Assert.Equal(["capture.add", "capture.add", "capture.add", "mark.create", "mark.edit", "mark.accept", "snapshot.publish"],
                events.Select(e => e.Type).ToArray());
            var accept = events.Single(e => e.Type == "mark.accept");
            Assert.Equal($"{MarkRef}@2", accept.Subject);
            Assert.Equal(handPlaced.Annotations.Single().ContentSha256, accept.Digest);
            Assert.Equal(Parent, accept.Actor);
            Assert.Equal(Convert.ToHexStringLower(SHA256.HashData(written)), events[^1].Digest);

            // Running the same review again publishes the same marks without new decisions.
            var again = new StringWriter();
            Assert.Equal(0, MarkingCli.Run(
            [
                "review", "--store", Path.Combine(dir, "store"), "--governance", FixturePath("governance.v1.json"),
                "--binding", FixturePath("binding.v1.json"), "--captures", FixturePath("pwa-export.captures.json"),
                "--decisions", FixturePath("reviewer-decisions.json"), "--actor", Parent, "--snapshot-rev", "home:review:9",
                "--expires", "2026-11-21T19:00:00Z", "--out", Path.Combine(dir, "again.json")
            ], again, new FixedClock(Issued)));
            Assert.Equal(written, File.ReadAllBytes(Path.Combine(dir, "again.json")));
        }
        finally
        {
            Directory.Delete(dir, true);
        }
    }

    [Fact]
    public void PublishedSnapshotValidatesAgainstTheExistingSchemaAndDigestsMatchTheReadmeFormula()
    {
        var (service, _) = Household();
        Accepted(service, MarkRef, Golden);
        var other = new MarkEdit(12000, 12500, null, "household:blasphemy", 1, "mark");
        Accepted(service, "home:mark:2", other, Phone[1].Ref);
        var published = Require(service.Publish(Binding, Parent, "home:review:10"));
        Assert.True(SchemaAccepts("annotation-snapshot-v1.schema.json", published.Utf8));
        Assert.Equal(2, published.Snapshot.Annotations.Length);
        foreach (var a in published.Snapshot.Annotations)
        {
            var text = $"annotation-v1|{a.Ref}|{a.Category}|{a.Severity}|{a.Action}|{a.StartMs}|{a.EndMs}";
            Assert.Equal(Convert.ToHexStringLower(SHA256.HashData(Encoding.UTF8.GetBytes(text))), a.ContentSha256);
        }
        Assert.Equal("profanity", published.Snapshot.Annotations[1].Category);
        Assert.Equal(Issued.AddDays(365), published.Snapshot.ExpiresAt);
        Assert.Equal(MarkingRefusal.InvalidExpiry, service.Publish(Binding, Parent, "home:review:11", Issued.AddDays(366)).Reason);
        Assert.Equal(MarkingRefusal.InvalidExpiry, service.Publish(Binding, Parent, "home:review:11", Issued).Reason);
        // A mark longer than the bound file is refused by the existing reader, not published.
        var (tooLong, _) = Household();
        Accepted(tooLong, MarkRef, Golden with { EndMs = Binding.DurationMs + 1 });
        var refused = tooLong.Publish(Binding, Parent, "home:review:12");
        Assert.Equal(MarkingRefusal.SnapshotInvalid, refused.Reason);
        Assert.Equal(RefusalCode.InvalidInterval, refused.DeliveryReason);
    }

    // ---------- review rules ----------

    [Fact]
    public void EditingAnAcceptedMarkDropsItFromTheNextSnapshotUntilReaccepted()
    {
        var (service, store) = Household();
        Accepted(service, MarkRef, Golden);
        Assert.Single(Require(service.Publish(Binding, Parent, "home:review:1")).Snapshot.Annotations);

        var edited = Require(service.Edit(MarkRef, 2, Golden with { EndMs = 10600 }, Parent));
        Assert.Equal(3, edited.Rev);
        Assert.Equal("proposed", edited.State);
        Assert.NotEqual(store.FindMark(MarkRef, 2)!.ContentSha256, edited.ContentSha256);
        Assert.Empty(Require(service.Publish(Binding, Parent, "home:review:2")).Snapshot.Annotations);
        // The old acceptance cannot be replayed onto the new revision.
        Assert.Equal(MarkingRefusal.StaleRevision, service.Accept(MarkRef, 2, Parent).Reason);

        Require(service.Accept(MarkRef, 3, Parent));
        var republished = Require(service.Publish(Binding, Parent, "home:review:3")).Snapshot.Annotations.Single();
        Assert.Equal(10600, republished.EndMs);
        // Earlier revisions stay in the record.
        Assert.Equal(10520, store.FindMark(MarkRef, 2)!.EndMs);

        Require(service.Withdraw(MarkRef, 3, Parent));
        Assert.Empty(Require(service.Publish(Binding, Parent, "home:review:4")).Snapshot.Annotations);
    }

    [Fact]
    public void AChangedFileRefusesPublishingUntilItsOldMarksAreReviewedAndNothingMovesAcross()
    {
        var (service, store) = Household();
        Accepted(service, MarkRef, Golden);
        Assert.Single(Require(service.Publish(Binding, Parent, "home:review:1")).Snapshot.Annotations);

        // The same item now plays a different edition (replaced or re-encoded file).
        var replaced = Binding with { Sha256 = new string('b', 64), BindingRev = "binding:4" };
        Assert.Equal([MarkRef], service.StaleMarks(replaced).Select(m => m.Ref).ToArray());
        // Neither with the old marks (other bytes) nor silently without them (unfiltered where it was filtered).
        Assert.Equal(MarkingRefusal.FileChanged, service.Publish(replaced, Parent, "home:review:2").Reason);
        // The old edition still publishes exactly as reviewed; its own marks are not "changed".
        Assert.Empty(service.StaleMarks(Binding));

        // The reviewer withdraws the old mark (it stays on record) and marks the new file afresh.
        Require(service.Withdraw(MarkRef, 2, Parent));
        Assert.Empty(service.StaleMarks(replaced));
        var draft = Require(service.CaptureToDraftMark(Phone[1].Ref, "home:fixture:mark2", replaced.BindingRev, Parent));
        var mark = Require(service.Edit(draft.Ref, draft.Rev, Golden with { StartMs = 5500 }, Parent));
        Require(service.Accept(mark.Ref, mark.Rev, Parent));
        var snapshot = Require(service.Publish(replaced, Parent, "home:review:3")).Snapshot;
        Assert.Equal((5500L, "home:fixture:mark2"), (snapshot.Annotations.Single().StartMs, snapshot.Annotations.Single().Ref));
        Assert.Equal(replaced.Sha256, snapshot.Binding.Sha256);
        Assert.Equal(10520, store.FindMark(MarkRef, 2)!.EndMs);
    }

    [Fact]
    public void OnlyReviewersAcceptOrPublishAndAServiceOnlySuggests()
    {
        var (service, _) = Household();
        Assert.Equal(MarkingRefusal.NotPermitted, service.CaptureToDraftMark(Phone[0].Ref, "m:kid", Binding.BindingRev, Kid).Reason);
        var draft = Require(service.CaptureToDraftMark(Phone[0].Ref, "m:spouse", Binding.BindingRev, Spouse));
        var refined = Require(service.Edit(draft.Ref, 1, Golden, Spouse));
        Assert.Equal(MarkingRefusal.NotPermitted, service.Accept(refined.Ref, refined.Rev, Spouse).Reason);
        Assert.Equal(MarkingRefusal.NotPermitted, service.Accept(refined.Ref, refined.Rev, Kid).Reason);
        Assert.Equal(MarkingRefusal.NotPermitted, service.Publish(Binding, Spouse, "home:review:1").Reason);
        // A marker edits only their own proposed marks.
        var parentDraft = Require(service.CaptureToDraftMark(Phone[1].Ref, "m:parent", Binding.BindingRev, Parent));
        Assert.Equal(MarkingRefusal.NotPermitted, service.Edit(parentDraft.Ref, 1, Golden, Spouse).Reason);
        Require(service.Accept(refined.Ref, refined.Rev, Parent));
        Assert.Equal(MarkingRefusal.NotPermitted, service.Edit(refined.Ref, refined.Rev, Golden with { Severity = 3 }, Spouse).Reason);

        // A household compute service submits suggested captures through the same store, and nothing else.
        var suggestion = Phone[2] with { Ref = "cap:svc1", Author = Service, Surface = "service" };
        Require(service.AddCapture(suggestion));
        Assert.Equal(MarkingRefusal.NotPermitted, service.AddCapture(Phone[2] with { Ref = "cap:svc2", Author = Service }).Reason);
        Assert.Equal(MarkingRefusal.NotPermitted, service.AddCapture(Phone[2] with { Ref = "cap:svc3", Surface = "service" }).Reason);
        Assert.Equal(MarkingRefusal.UnknownMember, service.AddCapture(Phone[2] with { Ref = "cap:x", Author = "jf:user:stranger" }).Reason);
        Assert.Equal(MarkingRefusal.NotPermitted, service.CaptureToDraftMark("cap:svc1", "m:svc", Binding.BindingRev, Service).Reason);
        Assert.Equal(MarkingRefusal.NotPermitted, service.Publish(Binding, Service, "home:review:2").Reason);
        var fromService = Require(service.CaptureToDraftMark("cap:svc1", "m:svc", Binding.BindingRev, Parent));
        Assert.Equal("auto:service", fromService.Provenance.Method);
        Assert.Equal("proposed", fromService.State);
        // Governance can never give a suggester a role that approves.
        var mixed = Encoding.UTF8.GetString(Fixture("governance.v1.json"))
            .Replace("\"roles\": [\"suggester\"]", "\"roles\": [\"suggester\", \"reviewer\"]");
        Assert.Equal(MarkingRefusal.ConflictingInput, MarkingDocuments.ParseGovernance(Encoding.UTF8.GetBytes(mixed)).Reason);
    }

    [Fact]
    public void OneParentMayMarkAndApproveLoggedUnlessASecondReviewerIsRequired()
    {
        var (service, store) = Household();
        Accepted(service, MarkRef, Golden);
        var accept = Require(AuditLog.Verify(store.AuditLines())).Single(e => e.Type == "mark.accept");
        Assert.Equal(Parent, accept.Actor);

        var strict = Gov with { Approval = Gov.Approval with { SelfApproval = false } };
        var (twoPerson, _) = Household(strict);
        var draft = Require(twoPerson.CaptureToDraftMark(Phone[0].Ref, MarkRef, Binding.BindingRev, Parent));
        var mark = Require(twoPerson.Edit(draft.Ref, 1, Golden, Parent));
        Assert.Equal(MarkingRefusal.SecondReviewerRequired, twoPerson.Accept(mark.Ref, mark.Rev, Parent).Reason);
        Require(twoPerson.Accept(mark.Ref, mark.Rev, SecondParent));
    }

    [Fact]
    public void ADraftFromACaptureIsOnlyASearchWindowAndCannotBeAccepted()
    {
        var (service, _) = Household();
        var capture = Phone[0];
        var draft = Require(service.CaptureToDraftMark(capture.Ref, MarkRef, Binding.BindingRev, Parent));
        Assert.Equal(Mark.CaptureWindowBounds, draft.BoundsSource);
        Assert.Equal(Math.Max(0, capture.AtMs - capture.Window.BeforeMs), draft.StartMs);
        Assert.Equal(capture.EndAtMs + capture.Window.AfterMs, draft.EndMs);
        Assert.Null(draft.Category);
        Assert.Equal("live", draft.Provenance.Method);
        Assert.Equal(MarkingRefusal.IncompleteMark, service.Accept(MarkRef, 1, Parent).Reason);
        Assert.Equal(MarkingRefusal.DuplicateReference, service.CaptureToDraftMark(capture.Ref, MarkRef, Binding.BindingRev, Parent).Reason);
        // Even a fully classified mark is refused while its bounds are still the capture's window.
        var store = new InMemoryMarkingStore();
        var windowOnly = draft with { Category = "violence", Severity = 2, Action = "skip" };
        store.PutMark(windowOnly with { ContentSha256 = windowOnly.ComputeContentSha256() });
        var service2 = new MarkingService(store, Gov, Tax, new FixedClock(Issued));
        Assert.Equal(MarkingRefusal.BoundsNotRefined, service2.Accept(MarkRef, 1, Parent).Reason);
    }

    [Fact]
    public void PublishNeedsARecordedAcceptanceNotJustAnAcceptedState()
    {
        var store = new InMemoryMarkingStore();
        var mark = new Mark(MarkRef, 1, Binding.BindingRev, 5480, 10520, Mark.ReviewerBounds, "violence", null, 2, "skip",
            "accepted", new([], "refined", Parent, "household-private"), Parent, null);
        store.PutMark(mark with { ContentSha256 = mark.ComputeContentSha256() });
        var service = new MarkingService(store, Gov, Tax, new FixedClock(Issued));
        Assert.Equal(MarkingRefusal.NoRecordedAcceptance, service.Publish(Binding, Parent, "home:review:1").Reason);
    }

    // ---------- policy ----------

    [Fact]
    public void AViewerPreferenceCanTightenButNeverLoosenTheFloor()
    {
        HouseholdPolicy Prefs(string rev, params CategoryRule[] rules) => new HouseholdPolicy("local:p2", rev, "trust:viewer", [.. rules]);

        var loosen = Require(MarkingService.CompilePolicy(Gov, "local:p2", Prefs("prefs:1", new CategoryRule("violence", 3, true))));
        Assert.Equal(new CategoryRule("violence", 2, true), loosen.Policy.Rules.Single());
        var off = Require(MarkingService.CompilePolicy(Gov, "local:p2", Prefs("prefs:2", new CategoryRule("violence", 0, false))));
        Assert.Equal(new CategoryRule("violence", 2, true), off.Policy.Rules.Single());

        var tighten = Require(MarkingService.CompilePolicy(Gov, "local:p2",
            Prefs("prefs:3", new CategoryRule("violence", 1, true), new CategoryRule("profanity", 2, true))));
        Assert.Equal([new CategoryRule("profanity", 2, true), new CategoryRule("violence", 1, true)], tighten.Policy.Rules.ToArray());
        Assert.True(SchemaAccepts("household-policy-v1.schema.json", tighten.Utf8));
        Assert.Equal("gov:4", tighten.Policy.TrustRev);
        Assert.NotEqual(loosen.Policy.PolicyRev, tighten.Policy.PolicyRev);

        // A locked profile ignores preferences entirely.
        var locked = Require(MarkingService.CompilePolicy(Gov, "local:p3", Prefs("prefs:4", new CategoryRule("violence", 0, true)) with { ProfileRef = "local:p3" }));
        Assert.Equal(new CategoryRule("nudity", 1, true), locked.Policy.Rules.Single());
        Assert.Equal(MarkingRefusal.ProfileMismatch, MarkingService.CompilePolicy(Gov, "local:p3", Prefs("prefs:5")).Reason);
        Assert.Equal(MarkingRefusal.UnknownProfile, MarkingService.CompilePolicy(Gov, "local:nobody", null).Reason);
    }

    // ---------- taxonomy ----------

    [Fact]
    public void AHouseholdSubcategoryRollsUpToItsCoreCategory()
    {
        Assert.Equal("disturbing", Require(Tax.RollUp("household:jump-scare")));
        Assert.Equal("violence", Require(Tax.RollUp("household:fistfight")));
        var (service, _) = Household();
        var draft = Require(service.CaptureToDraftMark(Phone[0].Ref, MarkRef, Binding.BindingRev, Parent));
        var mark = Require(service.Edit(draft.Ref, 1, new MarkEdit(5480, 10520, null, "household:fistfight", 2, "skip"), Parent));
        Assert.Equal("violence", mark.Category);
        Assert.Equal("household:fistfight", mark.Subcategory);
        // Subcategory is household detail: the snapshot carries only the core category and the same digest formula.
        Assert.Equal(new AcceptedAnnotation(MarkRef, "", "violence", 2, "skip", 5480, 10520).ComputeContentSha256(), mark.ContentSha256);
        Assert.Equal(MarkingRefusal.SubcategoryMismatch,
            service.Edit(MarkRef, 2, new MarkEdit(5480, 10520, "nudity", "household:fistfight", 2, "skip"), Parent).Reason);
        Assert.Equal(MarkingRefusal.UnknownConcept,
            service.Edit(MarkRef, 2, new MarkEdit(5480, 10520, null, "household:nope", 2, "skip"), Parent).Reason);
        Assert.Equal(MarkingRefusal.ConceptDeprecated,
            service.Edit(MarkRef, 2, new MarkEdit(5480, 10520, null, "household:old-label", 2, "skip"), Parent).Reason);

        static Taxonomy With(params TaxonomyConcept[] concepts) => new("tax:t", [.. concepts]);
        // At most two non-core concepts between a concept and its core category.
        Assert.Equal("violence", Require(With(new("household:a", "a", "household:b"), new("household:b", "b", "household:c"),
            new("household:c", "c", "violence")).RollUp("household:a")));
        Assert.Equal(MarkingRefusal.ConceptTooDeep, With(new("household:a", "a", "household:b"), new("household:b", "b", "household:c"),
            new("household:c", "c", "household:d"), new("household:d", "d", "violence")).RollUp("household:a").Reason);
        Assert.Equal(MarkingRefusal.ConceptCycle, With(new("household:a", "a", "household:b"),
            new("household:b", "b", "household:a")).RollUp("household:a").Reason);
        var cyclic = """{"taxonomyVersion":1,"rev":"tax:1","concepts":[{"id":"household:a","label":"a","broader":"household:a"}]}""";
        Assert.Equal(MarkingRefusal.ConceptCycle, MarkingDocuments.ParseTaxonomy(Encoding.UTF8.GetBytes(cyclic)).Reason);
    }

    // ---------- clocks ----------

    [Fact]
    public void AFilteredClockCaptureMapsThroughTheIssuedPlanOrRefuses()
    {
        var handPlaced = ResolverInputReader.ParseSnapshot(GoldenFile("hard-fixture.snapshot.v1.json")).Value!;
        var policy = ResolverInputReader.ParsePolicy(GoldenFile("hard-fixture.policy.v1.json")).Value!;
        var plan = Resolve(handPlaced, policy).Plan;
        var store = new InMemoryMarkingStore();
        var filtered = Phone[1] with { Clock = new CaptureClock("filtered", plan.Revision), Kind = "span", AtMs = 5000, EndAtMs = 6000 };

        Assert.Equal(MarkingRefusal.UnknownPlan, MarkingService.FilteredToSource(filtered, store).Reason);
        Assert.Equal(MarkingRefusal.UnknownPlan, MarkingService.FilteredToSource(filtered, null).Reason);
        store.RegisterIssuedPlan(plan);
        // 5480..10520 was cut: filtered 5000 is before it; filtered 6000 is 520 ms after the splice.
        var mapped = Require(MarkingService.FilteredToSource(filtered, store));
        Assert.Equal(new SourcePosition(5000, 11040), mapped);
        Assert.Equal(new SourcePosition(10520, null),
            Require(MarkingService.FilteredToSource(filtered with { Kind = "point", AtMs = 5480, EndAtMs = null }, store)));
        Assert.Equal(MarkingRefusal.OutsideTimeline, MarkingService.FilteredToSource(filtered with { EndAtMs = 15000 }, store).Reason);
        Assert.Equal(MarkingRefusal.BindingMismatch, MarkingService.FilteredToSource(
            filtered with { Target = filtered.Target with { ItemId = new string('2', 32) } }, store).Reason);

        // In the service: an unmapped capture yields no draft, but a reviewer can still place it by hand.
        var (service, _) = Household(captures: [filtered with { Clock = new CaptureClock("filtered", "local:unknown:7") }]);
        Assert.Equal(MarkingRefusal.UnknownPlan, service.CaptureToDraftMark(filtered.Ref, MarkRef, Binding.BindingRev, Parent).Reason);
        var placed = Require(service.PlaceFromCapture(filtered.Ref, MarkRef, Binding.BindingRev, Golden, Parent));
        Assert.Equal(Mark.ReviewerBounds, placed.BoundsSource);
        Require(service.Accept(MarkRef, 1, Parent));
    }

    // ---------- audit ----------

    [Fact]
    public void TheAuditChainDetectsARemovedEditedOrReorderedLine()
    {
        var (service, store) = Household();
        Accepted(service, MarkRef, Golden);
        Require(service.Publish(Binding, Parent, "home:review:1"));
        var lines = store.AuditLines().ToList();
        Assert.True(lines.Count >= 6);
        Assert.True(AuditLog.Verify(lines).Accepted);
        foreach (var line in lines)
            Assert.True(SchemaAccepts("audit-event-v1.schema.json", Encoding.UTF8.GetBytes(line)));

        var removed = lines.Where((_, i) => i != 3).ToList();
        var r = AuditLog.Verify(removed);
        Assert.Equal(MarkingRefusal.AuditChainBroken, r.Reason);
        Assert.Equal(4, r.Line);

        var edited = lines.ToList();
        edited[2] = edited[2].Replace(Parent, SecondParent);
        // A canonical rewrite of one line is caught where the next line's prev no longer matches it.
        Assert.Equal(4, AuditLog.Verify(edited).Line);
        var respaced = lines.ToList();
        respaced[1] = respaced[1].Replace("\",\"", "\", \"");
        Assert.Equal(2, AuditLog.Verify(respaced).Line);
        var swapped = lines.ToList();
        (swapped[1], swapped[2]) = (swapped[2], swapped[1]);
        Assert.Equal(2, AuditLog.Verify(swapped).Line);

        // A broken chain also stops publishing.
        var tampered = new TamperedStore(store, removed);
        Assert.Equal(MarkingRefusal.AuditChainBroken, new MarkingService(tampered, Gov, Tax, new FixedClock(Issued))
            .Publish(Binding, Parent, "home:review:2").Reason);
    }

    [Fact]
    public void TheFileStoreReopensStrictlyAndRefusesABrokenLog()
    {
        var dir = Scratch();
        try
        {
            var first = new FileMarkingStore(dir);
            var service = new MarkingService(first, Gov, Tax, new FixedClock(Issued), first);
            foreach (var c in Phone)
                Require(service.AddCapture(c));
            Accepted(service, MarkRef, Golden);

            var reopened = new FileMarkingStore(dir);
            Assert.Equal(Phone.Length, reopened.Captures().Count);
            Assert.Equal("accepted", reopened.FindMark(MarkRef)!.State);
            Assert.Equal(first.AuditLines(), reopened.AuditLines());
            var again = new MarkingService(reopened, Gov, Tax, new FixedClock(Issued.AddMinutes(1)), reopened);
            Assert.Single(Require(again.Publish(Binding, Parent, "home:review:1")).Snapshot.Annotations);
            foreach (var line in File.ReadAllLines(Path.Combine(dir, "marks.jsonl")))
                Assert.True(SchemaAccepts("mark-v1.schema.json", Encoding.UTF8.GetBytes(line)), line);
            foreach (var line in File.ReadAllLines(Path.Combine(dir, "captures.jsonl")))
                Assert.True(SchemaAccepts("capture-v1.schema.json", Encoding.UTF8.GetBytes(line)), line);

            var audit = Path.Combine(dir, "audit.jsonl");
            var kept = File.ReadAllLines(audit).Where((_, i) => i != 1);
            File.WriteAllLines(audit, kept);
            Assert.Throws<InvalidDataException>(() => new FileMarkingStore(dir));
        }
        finally
        {
            Directory.Delete(dir, true);
        }
    }

    // ---------- helpers ----------

    internal static (MarkingService Service, InMemoryMarkingStore Store) Household(Governance? governance = null,
        IEnumerable<Capture>? captures = null)
    {
        var store = new InMemoryMarkingStore();
        var service = new MarkingService(store, governance ?? Gov, Tax, new FixedClock(Issued), store);
        foreach (var c in captures ?? Phone)
            Require(service.AddCapture(c));
        return (service, store);
    }

    /// <summary>Draft from a capture, refine to exact bounds, accept as the parent.</summary>
    internal static Mark Accepted(MarkingService service, string markRef, MarkEdit exact, string? captureRef = null)
    {
        var draft = Require(service.CaptureToDraftMark(captureRef ?? Phone[0].Ref, markRef, Binding.BindingRev, Parent));
        var mark = Require(service.Edit(draft.Ref, draft.Rev, exact, Parent));
        Require(service.Accept(mark.Ref, mark.Rev, Parent));
        return mark;
    }

    internal static ResolvedPlaybackPlan Resolve(AcceptedAnnotationSnapshot snapshot, HouseholdPolicy policy)
    {
        var request = new ResolverRequest("local:v1", policy.ProfileRef, snapshot.Binding.ServerRef,
            new VerifiedSourceIdentity(snapshot.Binding.ItemId, snapshot.Binding.MediaSourceId, snapshot.Binding.Sha256,
                snapshot.Binding.SizeBytes, snapshot.Binding.DurationMs),
            new FixedClock(Issued), Issued, TimeSpan.FromMinutes(30));
        var result = HouseholdResolver.Resolve(snapshot, policy, request, new OneRevision());
        Assert.True(result.Accepted, result.Reason?.ToString());
        return result.Value!;
    }

    internal static T Require<T>(MarkingResult<T> result) where T : class
    {
        Assert.True(result.Accepted, result.ToString());
        return result.Value!;
    }

    internal static byte[] Fixture(string name) => File.ReadAllBytes(FixturePath(name));
    internal static string FixturePath(string name) => Path.Combine(AppContext.BaseDirectory, "Fixtures", name);
    internal static byte[] GoldenFile(string name) => File.ReadAllBytes(Path.Combine(AppContext.BaseDirectory, "Golden", name));

    internal static bool SchemaAccepts(string schema, byte[] json) => JsonSchema.FromText(File.ReadAllText(
        Path.Combine(AppContext.BaseDirectory, schema))).Evaluate(JsonNode.Parse(json),
        new EvaluationOptions { RequireFormatValidation = true }).IsValid;

    internal static string Scratch()
    {
        var dir = Path.Combine(Path.GetTempPath(), "cleanyfin-marking-tests", Guid.NewGuid().ToString("N"));
        Directory.CreateDirectory(dir);
        return dir;
    }

    internal sealed class FixedClock(DateTimeOffset now) : TimeProvider
    {
        public override DateTimeOffset GetUtcNow() => now;
    }

    private sealed class OneRevision : IPlanRevisionAllocator
    {
        public string AllocateRevision() => "local:resolved:1";
    }

    private sealed class TamperedStore(IMarkingStore inner, IReadOnlyList<string> audit) : IMarkingStore
    {
        public void AppendCapture(Capture capture) => inner.AppendCapture(capture);
        public Capture? FindCapture(string captureRef) => inner.FindCapture(captureRef);
        public IReadOnlyList<Capture> Captures() => inner.Captures();
        public void PutMark(Mark mark) => inner.PutMark(mark);
        public Mark? FindMark(string markRef, int? rev = null) => inner.FindMark(markRef, rev);
        public IReadOnlyList<Mark> LatestMarks() => inner.LatestMarks();
        public AuditEvent AppendAudit(DateTimeOffset at, string actor, string type, string subject, string? digest) =>
            throw new InvalidOperationException("not reached");
        public IReadOnlyList<string> AuditLines() => audit;
    }
}
