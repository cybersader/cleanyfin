using Jellyfin.Plugin.Cleanyfin.AuthRange;

namespace Delivery.Contract.Tests;

public sealed class ShelfMappingTests
{
    private static readonly Uri Origin = new("http://127.0.0.1:8096/base/");
    private static readonly string User = new('1', 32), Item = new('2', 32), Key = new('3', 32);
    private static readonly ShelfRow Row = new(Key, User, Item, Item, User, "timeline:1", "generation:1", 990000,
        ShelfRow.InertLocator(Origin, Key));

    [Fact]
    public void ResumeIdentitySurvivesInputRenewalButNeverAnotherCutViewerSourceOrOrigin()
    {
        var renewed = Row with { Generation = "generation:2" };
        Assert.Equal(renewed.Key, ShelfRow.Reusable([renewed], User, Item, Row.Timeline, Origin)!.Key);
        Assert.Null(ShelfRow.Reusable([Row], User, Item, "timeline:2", Origin));
        Assert.Null(ShelfRow.Reusable([Row], Key, Item, Row.Timeline, Origin));
        Assert.Null(ShelfRow.Reusable([Row], User, Key, Row.Timeline, Origin));
        Assert.Null(ShelfRow.Reusable([Row], User, Item, Row.Timeline, new Uri("http://127.0.0.1:8097/base")));
    }

    [Fact]
    public void CorruptInventoryCannotSupplyAnOwnerPath()
    {
        Assert.True(ShelfRow.ValidInventory([Row], 1));
        Assert.False(ShelfRow.ValidInventory([Row, Row], 8));
        Assert.False(ShelfRow.ValidInventory([Row with { Viewer = "../media" }], 8));
        Assert.False(ShelfRow.ValidInventory([Row with { Key = "../source.mp4" }], 8));
        Assert.False(ShelfRow.ValidInventory([Row with { Source = Key }], 8));
        Assert.False(ShelfRow.ValidInventory([Row with { Profile = Item }], 8));
        Assert.False(ShelfRow.ValidInventory([Row with { Locator = "/movie.mp4" }], 8));
        Assert.False(ShelfRow.ValidInventory([Row], 0));
    }
}
