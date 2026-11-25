using System;
using System.Threading.Tasks;
using MediaBrowser.Controller.Entities;
using MediaBrowser.Controller.Library;
using Microsoft.AspNetCore.Mvc;
using Microsoft.AspNetCore.Mvc.Filters;
using Microsoft.Extensions.DependencyInjection;

namespace Jellyfin.Plugin.Cleanyfin.AuthRange;

// The ordinary native routes address the item, not the filtered timeline. Do not let a download,
// image or trickplay request under a generated identity acquire a representation of the movie.
internal sealed class ShelfAncillaryFilter : IAsyncActionFilter
{
    public async Task OnActionExecutionAsync(ActionExecutingContext action, ActionExecutionDelegate next)
    {
        var route = action.ActionDescriptor.AttributeRouteInfo?.Template;
        if (route == null || !(route.EndsWith("/Download", StringComparison.OrdinalIgnoreCase) ||
            route.Contains("/Images/", StringComparison.OrdinalIgnoreCase) || route.Contains("/Trickplay/", StringComparison.OrdinalIgnoreCase) ||
            route.Contains("/Subtitles/", StringComparison.OrdinalIgnoreCase)) ||
            !(action.ActionArguments.TryGetValue("itemId", out var id) || action.ActionArguments.TryGetValue("id", out id)) || id is not Guid guid)
        { await next(); return; }
        var services = action.HttpContext.RequestServices;
        if (services.GetRequiredService<ILibraryManager>().GetItemById(guid) is Video item && services.GetRequiredService<ShelfStore>().Identified(item))
        { GrantPipeline.Private(action.HttpContext); action.Result = new NotFoundResult(); return; }
        await next();
    }
}
