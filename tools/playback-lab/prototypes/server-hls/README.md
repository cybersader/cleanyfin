# Boundary-only HLS for the hard fixture

This offline prototype removes source frames 137–262 and the corresponding audio from the shared 20 s fixture. It publishes a 14.96 s VOD playlist. Of its 374 pictures, it copies 300 from six closed GOPs and re-encodes 74 from the two partial GOPs. The cut is not snapped to a keyframe. The checker verifies the filtered presentation clock and scores a linear playlist read, a fresh decoder per segment, and seeks at 4.48 s and 5.48 s.

From the repository root, with Python 3.11+ and Docker:

```sh
python3 tools/playback-lab/prototypes/server-hls/check-hls.py
```

To retain the playlist for offline inspection, use an existing fixture and a **new** output directory:

```sh
python3 tools/playback-lab/prototypes/server-hls/package-hls.py --source FIXTURE/source.mp4 --plan FIXTURE/plan.json --out NEW_DIRECTORY
```

Both commands use the fixture kit’s pinned Jellyfin FFmpeg and FFprobe in network-disabled containers, not a Jellyfin server. The packager validates the source digest, muxed timestamps and closed GOP layout. It checks copied packet hashes and requires identical AVC configuration in the copied and re-encoded chunks, so this fixture needs no HLS discontinuity. Unsupported inputs fail without publishing `main.m3u8`; there is no unfiltered fallback. `packaging.json` records counts, wall time and the format choice.

The packager tries fMP4 first. It joins and decodes linearly, but the pinned FFmpeg’s input seek at 4.48 s lands on the next segment at 5.48 s. The packager rejects that candidate and uses MPEG-TS. There is one published segment per kept range (5.48 s and 9.48 s); grouping avoids further AAC decoder resets while preserving transport timestamps and counters. The full kept **audio** track is trimmed at exact decoded sample indexes and encoded once as AAC-LC, rather than copying AAC packets that straddle the cut. AAC priming and tail padding remain; the fixture scorer is a thresholded tone test, not sample-exact proof. For seeks, the checker joins the already-verified prefix to the untouched seek output so the scorer can verify a complete fixture.

This is one synthetic source, one cut and one audio track. It does not test a real player, HTTP delivery, Jellyfin integration, subtitles, arbitrary source encodings or production performance. The resume helpers map between the timelines; a source position inside the cut resolves forward to the cut end on inverse mapping. No Jellyfin progress state is read or written.
