using System;
using System.Threading.Tasks;
using Cleanyfin.Delivery;
using Microsoft.AspNetCore.Authorization;
using Microsoft.AspNetCore.Http;
using Microsoft.AspNetCore.Mvc;
using Microsoft.Extensions.DependencyInjection;

namespace Jellyfin.Plugin.Cleanyfin.AuthRange;

[ApiController]
[Authorize]
[Route("Cleanyfin/lab")]
public sealed class AuthRangeController : ControllerBase
{
    [HttpPost("issue")]
    public async Task<IActionResult> Issue()
    {
        GrantPipeline.Private(HttpContext);
        try
        {
            if (Request.QueryString.HasValue) throw new Refused(RefusalCode.InvalidIdentifier);
            var gate = HttpContext.RequestServices.GetRequiredService<FixtureGate>();
            return Ok(new { ListUrl = gate.Issue((await gate.Prepare(HttpContext)).Binding) });
        }
        catch (Exception ex)
        {
            GrantPipeline.Deny(HttpContext, ex is Refused r ? r.Reason : RefusalCode.UnverifiedBinding);
            return new EmptyResult();
        }
    }

    [HttpGet("descriptor"), HttpHead("descriptor")]
    public IActionResult Descriptor()
    {
        if (HttpContext.Items[GrantPipeline.Marker] is not Admission { Leg: Leg.List, Content: {} bytes }) return NotFound();
        Response.ContentType = "text/plain; charset=utf-8";
        Response.ContentLength = bytes.Length;
        return HttpMethods.IsHead(Request.Method) ? new EmptyResult() : File(bytes, Response.ContentType);
    }

    [HttpGet("subtitle"), HttpHead("subtitle")]
    public IActionResult Subtitle()
    {
        if (HttpContext.Items[GrantPipeline.Marker] is not Admission { Leg: Leg.Subtitle, Content: {} bytes } sub) return NotFound();
        return File(bytes, sub.Ass ? "text/x-ssa; charset=utf-8" : "application/x-subrip; charset=utf-8", enableRangeProcessing: true);
    }
}
