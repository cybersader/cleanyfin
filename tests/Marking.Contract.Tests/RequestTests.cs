using System.Text;
using Cleanyfin.Marking;

namespace Marking.Contract.Tests;

/// <summary>The household store's request bodies (slice 2) carry values only; authority comes from the login.</summary>
[Trait("Category", "Marking")]
public class RequestTests
{
    private static byte[] B(string json) => Encoding.UTF8.GetBytes(json);

    [Fact]
    public void AnEditCarriesTheRevisionAndExactValuesOnly()
    {
        var edit = MarkingRequests.ParseEdit(B("""{"rev":2,"startMs":5480,"endMs":10520,"category":"violence","severity":2,"action":"skip"}"""));
        Assert.True(edit.Accepted);
        Assert.Equal(2, edit.Value!.Rev);
        Assert.Equal(new MarkEdit(5480, 10520, "violence", null, 2, "skip"), edit.Value.Edit);
    }

    [Theory]
    [InlineData("""{"rev":2,"startMs":1,"endMs":2,"severity":2,"action":"skip","actor":"jf:user:admin"}""", MarkingRefusal.UnknownField)]
    [InlineData("""{"rev":2,"startMs":1,"endMs":2,"severity":2,"action":"skip","state":"accepted"}""", MarkingRefusal.UnknownField)]
    [InlineData("""{"rev":2,"startMs":1,"endMs":2,"severity":2,"action":"skip","rev":3}""", MarkingRefusal.DuplicateKey)]
    [InlineData("""{"rev":2,"startMs":1.0,"endMs":2,"severity":2,"action":"skip"}""", MarkingRefusal.InvalidValue)]
    [InlineData("""{"rev":2,"startMs":1,"endMs":2,"severity":4,"action":"skip"}""", MarkingRefusal.InvalidValue)]
    [InlineData("""{"rev":2,"startMs":1,"endMs":2,"severity":2,"action":"blur"}""", MarkingRefusal.InvalidValue)]
    [InlineData("""{"rev":2,"startMs":1,"endMs":2,"category":"romance","severity":2,"action":"skip"}""", MarkingRefusal.UnknownCategory)]
    [InlineData("""{"startMs":1,"endMs":2,"severity":2,"action":"skip"}""", MarkingRefusal.MissingField)]
    public void AnEditCannotNameAnActorStateOrOutOfRangeValue(string json, MarkingRefusal reason) =>
        Assert.Equal(reason, MarkingRequests.ParseEdit(B(json)).Reason);

    [Fact]
    public void DecisionsDraftsAndPublishAreClosed()
    {
        Assert.Equal(3, MarkingRequests.ParseRevision(B("""{"rev":3}""")).Value!.Rev);
        Assert.Equal(MarkingRefusal.UnknownField, MarkingRequests.ParseRevision(B("""{"rev":3,"actor":"x"}""")).Reason);
        Assert.Equal(MarkingRefusal.InvalidValue, MarkingRequests.ParseRevision(B("""{"rev":0}""")).Reason);
        var draft = MarkingRequests.ParseDraft(B("""{"captureRef":"cap:1"}""")).Value!;
        Assert.Equal(("cap:1", (MarkEdit?)null), (draft.CaptureRef, draft.Exact));
        var placed = MarkingRequests.ParseDraft(B("""{"captureRef":"cap:1","exact":{"startMs":1,"endMs":2,"category":"gore","severity":1,"action":"cut"}}""")).Value!;
        Assert.Equal(new MarkEdit(1, 2, "gore", null, 1, "cut"), placed.Exact);
        Assert.Equal(MarkingRefusal.InvalidValue, MarkingRequests.ParseDraft(B("""{"captureRef":" cap"}""")).Reason);
        Assert.Null(MarkingRequests.ParsePublish(B("{}")).Value!.ExpiresAt);
        Assert.Equal(MarkingRefusal.InvalidTimestamp, MarkingRequests.ParsePublish(B("""{"expiresAt":"tomorrow"}""")).Reason);
        Assert.Equal(MarkingRefusal.UnknownField, MarkingRequests.ParsePublish(B("""{"snapshotRev":"mine"}""")).Reason);
    }

    [Fact]
    public void OversizedBodiesAreRefusedBeforeParsing() =>
        Assert.Equal(MarkingRefusal.LimitExceeded, MarkingRequests.ParseRevision(new byte[16 * 1024 + 1]).Reason);
}
