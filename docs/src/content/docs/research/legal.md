---
title: Legal & IP landscape
description: How the Family Movie Act, DMCA, and the ClearPlay/VidAngel precedent define cleanyfin's metadata-only legal boundary.
sidebar:
  order: 1
---

*A research deep-dive from the 2026-07-21 cleanyfin research fan-out. Lightly formatted raw findings with confidence levels and sources preserved.*

## TL;DR

The legal path for cleanyfin is well-lit and defensible IF it copies the ClearPlay/SponsorBlock model and never touches copyrighted A/V bits. US law (the Family Movie Act, 17 U.S.C. §110(11)) expressly legalizes real-time "making imperceptible" (skip/mute) of limited portions of an AUTHORIZED copy for private home viewing, PROVIDED no fixed edited copy is created. VidAngel lost catastrophically (~$62.4M jury verdict, settled to $9.9M) NOT because filtering is illegal, but because it ripped DRM-protected discs (DMCA §1201 circumvention) and streamed its own unauthorized decrypted copies — two things the FMA does not cover. A crowdsourced database of timestamps + category labels is almost certainly uncopyrightable facts (thin-to-no copyright), which is why SponsorBlock has run openly for years; the residual legal exposure is user-contributed expression (copied dialogue/subtitles), managed via §512 notice-and-takedown. The core design rule: cleanyfin must distribute ONLY timestamps + categories + version identifiers and apply them in real time to the user's own copy in the user's own player, never producing, caching, transcoding, proxying, or exporting a single frame of A/V, and never performing DRM decryption.

## Key Findings

### 1. The Family Movie Act (17 U.S.C. §110(11), enacted April 27, 2005) is the exact statutory safe harbor cleanyfin should build on, and its operative limits map directly onto the design.  ·  🟢 high

Public Law 109-9 (Family Entertainment and Copyright Act of 2005) added §110(11), which exempts from infringement: 'the making imperceptible, by or at the direction of a member of a private household, of limited portions of audio or video content of a motion picture, during a performance in or transmitted to that household for private home viewing, from an authorized copy of the motion picture, or the creation or provision of a computer program or other technology that enables such making imperceptible ... if no fixed copy of the altered version of the motion picture is created by such computer program or other technology.' Four load-bearing conditions: (1) 'making imperceptible' = skip/mute only — the same section states it 'does not include the addition of audio or video content ... over or in place of existing content'; (2) 'authorized copy' — the source must be lawfully obtained; (3) 'private home viewing'; (4) 'no fixed copy of the altered version' may be created. The statute explicitly blesses providing the enabling technology, not just end-user use.

Sources: <https://www.copyright.gov/legislation/pl109-9.html> · <https://www.congress.gov/109/plaws/publ9/PLAW-109publ9.htm>

### 2. VidAngel lost because of DRM circumvention and unauthorized copies — NOT because content filtering itself is illegal; the FMA did not save it.  ·  🟢 high

In Disney Enterprises, Lucasfilm, and Twentieth Century Fox v. VidAngel (C.D. Cal.), a March 2019 summary judgment found VidAngel liable for (a) DMCA §1201 anti-circumvention — it decrypted DRM on purchased DVDs/Blu-rays to make digital copies — and (b) copyright infringement — it streamed those unauthorized decrypted copies to customers, sometimes before licensed streaming windows. A June 2019 jury awarded $62,448,780: $75,000 statutory damages per each of 819 infringed works, plus $1,250 per DMCA violation. A 2016 preliminary injunction (upheld by the 9th Circuit in 2017) had already shut the disc model down. VidAngel's FMA defense failed specifically because the filtered copies were NOT 'authorized copies' and the model created intermediate fixed copies — the two conditions §110(11) requires.

Sources: <https://variety.com/2019/biz/news/vidangel-jury-verdict-damages-1203245947> · <https://copyright.byu.edu/willful-infringement-leads-to-multi-million-dollar-damages> · <https://en.wikipedia.org/wiki/VidAngel>

### 3. VidAngel settled the $62.4M verdict down to $9.9M (Sept 2020) and pivoted to filtering streams the user already licenses — the model cleanyfin should emulate conceptually.  ·  🟢 high

In September 2020 VidAngel settled with Disney and Warner Bros. for $9.9M paid over ~14 years to emerge from Chapter 11 bankruptcy, and agreed NOT to stream the studios' content. Its surviving/relaunched model requires users to hold their OWN paid Netflix/Amazon Prime accounts; VidAngel applies real-time skip/mute filters over the user's authorized stream without storing or decrypting the underlying media. That post-pivot model has NOT been successfully challenged in court, which is strong (though not dispositive) evidence the timestamp-overlay-on-your-own-copy approach is legally durable.

Sources: <https://deadline.com/2020/09/vidangel-settles-4-year-battle-with-disney-and-warner-bros-agreeing-to-pay-9-9m-to-emerge-from-bankruptcy-1234571195/> · <https://variety.com/2020/biz/news/vidangel-copyright-suit-studios-1234760033/> · <https://en.wikipedia.org/wiki/VidAngel>

### 4. ClearPlay survived for 20+ years on exactly the architecture cleanyfin proposes: ship a timeline edit list, let the user's own player skip/mute their own copy.  ·  🟢 high

ClearPlay's technology modifies the media player to read a timeline-based edit list marking objectionable material with frame-accurate 'in' and 'out' points; the player skips or mutes during playback. The A/V content is never redistributed — only the edit-decision metadata is. This is the FMA-compliant design (authorized copy, real-time making-imperceptible, no fixed edited copy), and it is why ClearPlay was never enjoined while VidAngel was. ClearPlay still operates in 2026, filtering Netflix/Amazon/HBO Max/Disney+ via a browser extension over the user's own accounts.

Sources: <https://en.wikipedia.org/wiki/ClearPlay> · <https://try.clearplay.com/what-is-clearplay/>

### 5. DMCA §1201 (anti-circumvention) is the single biggest legal landmine, and cleanyfin avoids it entirely by never decrypting anything.  ·  🟢 high

17 U.S.C. §1201 prohibits circumventing technological protection measures (DRM) that control access to copyrighted works, independent of whether any infringement occurs — this is precisely what sank VidAngel's disc model. A filter that only ships timestamps + category labels, which the user's own player applies to a file the user already possesses in decrypted form, never circumvents any TPM and never triggers §1201. Critical nuance: cleanyfin itself must not perform, bundle, or instruct DRM-ripping; if a user's underlying library files were themselves ripped from DRM discs, that is the user's separate §1201 exposure, and cleanyfin should not facilitate or depend on that step. Filtering Jellyfin libraries of user-provided files (home videos, DRM-free purchases, user's own rips) keeps the project clear.

Sources: <https://www.copyright.gov/1201/> · <https://en.wikipedia.org/wiki/VidAngel>

### 6. Timestamp + category-label lists are almost certainly uncopyrightable facts (thin-to-no copyright), the legal basis SponsorBlock has relied on for years.  ·  🟢 high

Under Feist Publications v. Rural Telephone (1991, U.S. Supreme Court), facts and 'sweat of the brow' compilations lacking original expression are not copyrightable; only original selection/arrangement gets 'thin' protection. An in/out timestamp pair + a category enum ('profanity', 'nudity') describing where content occurs in someone else's film is factual, not expressive — it is not a copy of the work. SponsorBlock has crowdsourced millions of such timestamps since 2019 without a successful copyright suit. Residual risk lives in user-submitted EXPRESSION (verbatim transcribed dialogue, copied subtitle text, thumbnails/clips) — cleanyfin should forbid storing any of that. Note: SponsorBlock's own database is licensed CC BY-NC-SA 4.0 (non-commercial, share-alike) — cleanyfin must NOT reuse SponsorBlock data if it wants unrestricted use, and should build its own DB under a permissive license (e.g., CC0/ODbL) it chooses deliberately.

Sources: <https://github.com/ajayyy/SponsorBlock/wiki/Database-and-API-License> · <https://api.sponsor.ajay.app/database> · <https://creativecommons.org/public-domain/#cc0>

### 7. DMCA §512 safe harbor is the right shield for the crowdsourced DB: register a DMCA agent and run notice-and-takedown + moderation.  ·  🟢 high

17 U.S.C. §512(c) shields online service hosts from liability for user-submitted material if they (1) lack knowledge of infringement, (2) act expeditiously to remove on notice, and (3) designate a registered DMCA agent with the Copyright Office. Even though bare timestamps likely aren't infringing, a federated project that accepts user contributions should still adopt §512 hygiene (agent registration, takedown workflow, repeat-infringer policy) as cheap insurance against rogue submissions containing copyrighted expression. This mirrors SponsorBlock's moderation + vote-based removal system.

Sources: <https://www.copyright.gov/512/> · <https://github.com/ajayyy/SponsorBlock>

### 8. The foundational 'ship-an-edit-list / skip-mute-by-timestamp' patents (priority ~2000-2001) have expired; newer ClearPlay patents (2020s) are the live design-around risk.  ·  🟡 medium

In VidAngel LLC v. ClearPlay Inc. (D. Utah, Case 2:14-cv-00160), VidAngel asserted 7 filtering patents — US6889383B1, US6898799B1, US7526784B2, US7543318B2, US7577970B2, US7975021B2, US8117282B2 — covering delivering navigation/filter data to a player to skip/mute A/V. Priority dates circa 2000-2001 mean the earliest expired ~2020-2022 (20-year term), and the case was dismissed WITH PREJUDICE on Sept 16, 2024 by stipulation, with NO ruling on validity or infringement (so no precedent either way). The core concept cleanyfin uses is thus largely in the public domain via patent expiry. HOWEVER, ClearPlay holds a NEWER portfolio (e.g., US9762963, US10313744, and US11750887 'Digital content controller', issued 2023) that may remain in force into the 2030s-2040s. These newer claims — not the old ones — are the real design-around concern. Uncertainty is high without a formal freedom-to-operate opinion; recommend patent counsel before any funded launch.

Sources: <https://www.patsnap.com/resources/blog/litigation/vidangel-v-clearplay-video-content-filtering-patent-dispute-patsnap/> · <https://image-ppubs.uspto.gov/dirsearch-public/print/downloadPdf/11750887>

### 9. Jellyfin's trademark policy permits FLOSS ecosystem naming but requires a distinct logo and, ideally, leadership sign-off — the name 'cleanyfin' is workable with care.  ·  🟢 high

Per Jellyfin's branding page, 'Jellyfin' and the primary logo are registered trademarks of Jellyfin, Inc. (an Ontario, Canada non-profit) in Canada, US, EU, and China. Rules: 'All 3rd party projects should use their own logo to clearly differentiate,' but 'exceptions may be granted for free-and-libre-open-source (FLOSS) projects by contacting the leadership team.' 'Any fork of Jellyfin or an official client application for public distribution must use a different name and logo.' Importantly, 'The logo colours are not subject to trademark, and the purple-blue gradient theme may be used with another logo shape for identification as part of the Jellyfin ecosystem without limitation.' cleanyfin is a PLUGIN, not a fork, so it has latitude, but it should: use its own distinct logo, avoid implying official endorsement, and email team@jellyfin.org for the FLOSS blessing.

Sources: <https://jellyfin.org/docs/project/branding/>

### 10. This entire analysis is US-centric; the FMA has NO equivalent in the EU/UK and most other jurisdictions, so federation must be jurisdiction-aware.  ·  🟡 medium

The §110(11) exemption is a peculiarity of US copyright law with no direct counterpart abroad. The EU operates under the InfoSoc Directive (2001/29/EC) and DSM Directive (2019/790) with a closed list of exceptions and NO broad fair-use/family-filtering carve-out; the moral-rights 'right of integrity' in many civil-law countries could even be argued against altering a film. Bare factual timestamp databases are likely still fine in the EU (facts aren't protected), but the sui generis EU Database Right (Directive 96/9/EC) can protect substantial investment in a database's compilation — relevant to how cleanyfin licenses its own DB. Practical implication for a federated design: node operators bear their own local legal risk; cleanyfin should document that the FMA safe harbor is US-only and not promise legality elsewhere.

Sources: <https://eur-lex.europa.eu/eli/dir/2001/29/oj> · <https://eur-lex.europa.eu/eli/dir/96/9/oj>

## Recommendations for cleanyfin

**R1. HARD RULE #1: cleanyfin must never ship, store, cache, transcode, proxy, decrypt, or export a single frame of A/V. The federated database contains ONLY: title/version identifier, in-point, out-point, category enum, and moderation metadata. Filtering happens in real time in the user's own player against the user's own file.**

- *Why:* This is the exact line between ClearPlay (survived 20+ years) and VidAngel ($62M verdict). It simultaneously avoids the copyright-reproduction claim, DMCA §1201 circumvention, and the FMA 'no fixed copy' violation. It is the single most important constraint in the whole project.
- *Risk / tradeoff:* Tempting shortcuts (server-side transcoding to bake in skips, caching thumbnails for the marking UI, storing subtitle snippets to identify segments) each re-introduce legal exposure. The architecture must make these impossible, not merely discouraged.

**R2. Implement filtering strictly as real-time skip/mute ('making imperceptible'), never as generation of a new edited media file or export. Do not add overlays or replacement audio/video.**

- *Why:* §110(11) requires 'no fixed copy of the altered version' AND excludes 'addition of audio or video content ... over or in place of existing content.' A skip-list applied live by the player is squarely inside the exemption; an exported edited MP4 is squarely outside it.
- *Risk / tradeoff:* Users may request an 'export filtered copy' feature for offline/other players; that feature would forfeit FMA protection and should be refused as an explicit non-goal.

**R3. Do NOT perform, bundle, script, or document DRM circumvention, and design the plugin to operate only on files already present in the user's Jellyfin library. Add explicit terms that users are responsible for the lawful, authorized status of their own media.**

- *Why:* DMCA §1201 is strict-liability and destroyed VidAngel independent of infringement. cleanyfin stays clear only if it never touches a TPM. Keeping the ripping/acquisition step entirely outside the project (the user's own separate act) isolates cleanyfin from §1201.
- *Risk / tradeoff:* If cleanyfin ever integrates a 'rip your disc' helper or depends on decrypted-on-the-fly commercial streams, it inherits VidAngel's exact liability. Keep that boundary bright.

**R4. Build cleanyfin's OWN segment database from scratch under a deliberately permissive license (CC0 or ODbL). Do NOT import or depend on SponsorBlock's data, whose CC BY-NC-SA 4.0 license forbids commercial use and imposes share-alike.**

- *Why:* Timestamps+labels are factual and thin/uncopyrightable, so a fresh DB is legally clean and maximally reusable across federated nodes. Reusing SponsorBlock data would drag NC/share-alike restrictions (and possible EU database-right issues) into an otherwise-free project.
- *Risk / tradeoff:* Choosing CC0 means others can fork the data commercially; if the project wants to prevent proprietary capture, ODbL/share-alike is the alternative — a values decision the maintainer must make consciously up front, because relicensing a crowdsourced DB later is nearly impossible.

**R5. Prohibit copyrighted EXPRESSION in the database schema: no verbatim dialogue, no subtitle text, no transcripts, no thumbnails, no clips. Store only category + timestamps + minimal identifiers. Pair this with DMCA §512 hygiene: register a designated agent, publish a takedown process, and adopt a repeat-infringer/moderation policy.**

- *Why:* The one place the 'facts aren't copyrightable' shield leaks is user-submitted expression. Forbidding it at the schema level removes the leak; §512 provides host safe harbor for whatever slips through, exactly as SponsorBlock moderates crowd submissions.
- *Risk / tradeoff:* Contributors will want to attach a quoted line ('skips the F-word at 12:03') for context; even short quotes create needless exposure. Enforce category-only descriptions in the client and API validation.

**R6. Get a freedom-to-operate patent review focused on ClearPlay's POST-2015 patents (e.g., US9762963, US10313744, US11750887) before any funded/promoted launch. Rely on the fact that the foundational skip/mute-by-edit-list patents (priority ~2000-2001) have expired, and design around specific live claims.**

- *Why:* The core concept is now public-domain via patent expiry (confirmed by the 2024 dismissal-with-prejudice of VidAngel v. ClearPlay with no validity ruling), but ClearPlay's newer patents could read on specific novel UI/sync/architecture features. Counsel can steer implementation away from live claims cheaply if done early.
- *Risk / tradeoff:* Patent uncertainty is genuinely high and I cannot give a clearance opinion; a non-practicing entity or ClearPlay could assert a newer patent even against a FLOSS project. Budget for a real FTO opinion; treat any 'novel' filtering-sync mechanism as a patent-review trigger.

**R7. Name and brand carefully: keep 'cleanyfin' as a clearly third-party PLUGIN (not a fork), ship a distinct original logo, avoid any implication of official Jellyfin endorsement, and email team@jellyfin.org to request the FLOSS naming exception. The purple-blue gradient is explicitly free to use.**

- *Why:* Jellyfin's branding policy allows FLOSS ecosystem names with a differentiating logo and welcomes contact toward official-project status. Proactive sign-off converts a gray area into an endorsed relationship and de-risks the '-fin' name.
- *Risk / tradeoff:* If Jellyfin, Inc. objects, a rename is disruptive after adoption — get the conversation done before the name is load-bearing in user installs and repos.

**R8. Document prominently that cleanyfin's legal safe harbor (the Family Movie Act) is US-only, and make the federated design jurisdiction-aware so node operators understand they bear local legal risk (EU has no FMA equivalent; moral-rights and database-right regimes differ).**

- *Why:* Overseas users/operators may assume legality that doesn't exist outside the US. Honest scoping protects both the project's reputation and its contributors, and informs how nodes federate across borders.
- *Risk / tradeoff:* Being explicit about non-US uncertainty may deter some international adoption, but silent over-promising invites worse outcomes for operators in stricter jurisdictions.

## Open Questions

- **Should cleanyfin's crowdsourced segment database be licensed CC0 (maximal freedom, allows commercial forks) or ODbL/CC-BY-SA (share-alike, prevents proprietary capture)?** — *lean:* CC0 for the timestamp/label facts, to match the 'free and DMCA-safe, maximally reusable, federated' ethos and avoid share-alike friction across nodes — but this is a values call the maintainer must make deliberately since it is effectively irreversible for a crowdsourced DB.
- **Does cleanyfin ever need to touch commercial DRM-protected STREAMS (Netflix/Disney+ style, VidAngel's pivot model) or only the user's own Jellyfin library files?** — *lean:* Only user-owned Jellyfin library files. Filtering commercial DRM streams pulls in browser-injection/TOS and potential §1201 questions; staying inside the user's self-hosted library is the cleanest, most defensible scope and fits the self-host mission.
- **Is a formal freedom-to-operate (FTO) patent opinion on ClearPlay's post-2015 patents worth the cost for a non-commercial FLOSS side project?** — *lean:* Get at least a lightweight FTO review before any funded promotion or donations; the foundational patents are expired, but a targeted look at US9762963/US10313744/US11750887 is cheap insurance against a surprise assertion.
- **Should cleanyfin proactively seek Jellyfin, Inc.'s FLOSS naming/branding blessing, or rely on the general third-party allowance?** — *lean:* Proactively email team@jellyfin.org — the policy invites it and official-ecosystem status is worth far more than the small effort, especially before the name is embedded in installs.
- **How should the federated model handle liability when a node operator is in a jurisdiction (EU/UK) with no Family Movie Act equivalent?** — *lean:* Ship clear per-jurisdiction documentation and keep nodes independently operated so no single entity aggregates cross-border liability; do not centralize hosting of contributions in a way that makes the core project the responsible party globally.

## Sources

- [U.S. Copyright Office — Family Movie Act / Public Law 109-9 (2005)](https://www.copyright.gov/legislation/pl109-9.html) — Primary source: exact operative text of 17 U.S.C. §110(11), including 'authorized copy,' 'making imperceptible,' and the 'no fixed copy' limitation.
- [Congress.gov — PLAW-109publ9 (Family Entertainment and Copyright Act of 2005)](https://www.congress.gov/109/plaws/publ9/PLAW-109publ9.htm) — Full enacted statutory text of the law containing the Family Movie Act.
- [Variety — VidAngel Hit With $62.4M Jury Verdict (June 2019)](https://variety.com/2019/biz/news/vidangel-jury-verdict-damages-1203245947) — Damages breakdown: $75k x 819 works + $1,250 per DMCA violation = $62,448,780.
- [BYU Copyright — Willful Infringement Leads to Multi-Million Dollar Damages (VidAngel)](https://copyright.byu.edu/willful-infringement-leads-to-multi-million-dollar-damages) — Explains the §1201 circumvention and unauthorized-copy findings and why the FMA defense failed.
- [Deadline — VidAngel Settles With Disney/Warner Bros. for $9.9M (Sept 2020)](https://deadline.com/2020/09/vidangel-settles-4-year-battle-with-disney-and-warner-bros-agreeing-to-pay-9-9m-to-emerge-from-bankruptcy-1234571195/) — Settlement terms: $9.9M over ~14 years, agreement not to stream studios' content, exit from bankruptcy.
- [Wikipedia — VidAngel](https://en.wikipedia.org/wiki/VidAngel) — Timeline of litigation, injunction, bankruptcy, and pivot to filtering user-licensed Netflix/Amazon streams.
- [Wikipedia — ClearPlay](https://en.wikipedia.org/wiki/ClearPlay) — How the FMA-compliant edit-list/skip-mute architecture works and why ClearPlay was never enjoined.
- [PatSnap — VidAngel v. ClearPlay Video Content Filtering Patent Dispute](https://www.patsnap.com/resources/blog/litigation/vidangel-v-clearplay-video-content-filtering-patent-dispute-patsnap/) — The 7 asserted filtering patents (US6889383B1 etc.), ~2000-2001 priority dates, and Sept 16 2024 dismissal with prejudice (no validity ruling).
- [USPTO — US Patent 11,750,887 'Digital content controller' (ClearPlay, 2023)](https://image-ppubs.uspto.gov/dirsearch-public/print/downloadPdf/11750887) — Example of ClearPlay's NEWER, still-live patent portfolio that a design-around/FTO review must consider.
- [SponsorBlock — Database and API License (CC BY-NC-SA 4.0)](https://github.com/ajayyy/SponsorBlock/wiki/Database-and-API-License) — Confirms SponsorBlock's crowdsourced timestamp DB is non-commercial/share-alike — cleanyfin should NOT reuse it and should pick its own license.
- [SponsorBlock — main repository](https://github.com/ajayyy/SponsorBlock) — The working precedent: crowdsourced timestamp DB with vote-based moderation, running openly since 2019 without a successful copyright suit.
- [Jellyfin — Branding / Trademark Policy](https://jellyfin.org/docs/project/branding/) — Trademark ownership (Jellyfin, Inc., Ontario), FLOSS naming exception, distinct-logo requirement, and free use of the purple-blue gradient.
- [U.S. Copyright Office — DMCA §1201 anti-circumvention](https://www.copyright.gov/1201/) — The strict-liability DRM-circumvention rule that sank VidAngel and that a timestamp-only filter avoids by never decrypting.
- [U.S. Copyright Office — DMCA §512 safe harbor](https://www.copyright.gov/512/) — Notice-and-takedown / designated-agent safe harbor for hosting user-contributed segment data.
