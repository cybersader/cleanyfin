using Jellyfin.Plugin.Cleanyfin.AuthRange;
using Microsoft.AspNetCore.Hosting;
using Microsoft.AspNetCore.Mvc;
using MediaBrowser.Controller;
using MediaBrowser.Controller.MediaSegments;
using MediaBrowser.Controller.Plugins;
using Microsoft.Extensions.DependencyInjection;

namespace Jellyfin.Plugin.Cleanyfin;

/// <summary>Registers the cleanyfin segment provider with Jellyfin's DI container.</summary>
public class ServiceRegistrator : IPluginServiceRegistrator
{
    /// <inheritdoc />
    public void RegisterServices(IServiceCollection serviceCollection, IServerApplicationHost applicationHost)
    {
        serviceCollection.AddSingleton<GrantService>(s => new(s.GetRequiredService<MediaBrowser.Controller.Session.ISessionManager>(),
            s.GetRequiredService<MediaBrowser.Controller.Library.IUserManager>()));
        serviceCollection.AddSingleton(_ => new PinnedAsset());
        serviceCollection.AddSingleton<HouseholdEditions>();
        serviceCollection.AddSingleton<Editions>();
        serviceCollection.AddSingleton<ShelfStore>();
        serviceCollection.AddSingleton<Marking.HouseholdMarking>();
        serviceCollection.AddHostedService<ShelfWorker>();
        serviceCollection.AddScoped<FixtureGate>();
        serviceCollection.AddSingleton<IStartupFilter, GrantPipeline>();
        serviceCollection.Configure<MvcOptions>(o => o.Filters.Add(new GrantActionFilter()));
        serviceCollection.Configure<MvcOptions>(o => o.Filters.Add(new PointerPlaybackFilter()));
        serviceCollection.Configure<MvcOptions>(o => o.Filters.Add(new ShelfAncillaryFilter()));
        serviceCollection.AddSingleton<MediaBrowser.Controller.Providers.IMetadataProvider, PointerMetadata>();
        serviceCollection.AddSingleton<IMediaSegmentProvider, CleanyfinSegmentProvider>();
    }
}
