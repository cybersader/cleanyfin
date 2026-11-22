using Jellyfin.Plugin.Cleanyfin.AuthRange;

public class PinnedMp4ContainerTests
{
    [Theory]
    [InlineData("mp4", true, true)]
    [InlineData("mov", true, true)]
    [InlineData("m4a", true, true)]
    [InlineData("3gp", true, true)]
    [InlineData("3g2", true, true)]
    [InlineData("mj2", true, true)]
    [InlineData("mov,mp4,m4a,3gp,3g2,mj2", true, true)]
    [InlineData("mkv,matroska", false, false)]
    [InlineData("mkv,mov", false, true)]
    [InlineData("notmp4", false, false)]
    [InlineData("", false, false)]
    [InlineData(null, false, false)]
    public void OnlyExactFamilyTokensDescribeThePinnedSource(string? names, bool describesSource, bool offeredByProfile)
    {
        Assert.Equal(describesSource, PinnedMp4Container.DescribesSource(names));
        Assert.Equal(offeredByProfile, PinnedMp4Container.OfferedByProfile(names));
    }
}
