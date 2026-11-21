# Selective dialogue removal: a word over music vs. a word over another speaker

2026-11-18/19. Audio-only proof of concept; not integrated with the player. The owner suggested looking at stems rather than assuming nearby dialogue could be trimmed. No extra trim allowance was agreed. Synthetic voices and generated backing only; all inference local. No GPU, external speech API, movie media or distribution. A separate playback trial is in `08-jellyfin-web-skip-lab.md`.

**Question:** given the correct word boundaries, does an estimated voice stem let a filter preserve music and unrelated dialogue? Six seconds of stereo PCM, 44.1 kHz. Known separate sources: chord backing, synthetic lead voice saying “The code word is” then “Blueberry”, and in the second fixture another voice saying “The train arrives at nine” during the word. The edit window was 2.49–3.53 s. Target word energy outside the window was exactly zero by construction. This removes ASR/timing as a confound, not as a solved product problem.

**Method:** sherpa-onnx 2.0.1, two CPU threads, [UVR-MDX-NET-Inst_HQ_3.onnx](https://github.com/k2-fsa/sherpa-onnx/releases/tag/source-separation-models), SHA-256 `c207b845d51dc1303c543503f6f5a8b596da5d7de97bead77b3e16ec38e8d5b3`. Calibrate output roles using known references, not just example script labels: for this model the measured second output was voice-dominant. Subtract that estimate from the original **only within the word window**, preserving everything else. Baselines: original mix; reference made by removing just the known target source. No loudness normalisation between them. A two-pass model process hit local memory throttling on its second pass and was stopped; overlapping condition completed in a fresh single-pass process (6.46 s). This is not a serving-performance benchmark.

**Results:** least-squares projection on the known source waveforms (left channel, edited interval; amplitude, not perceptual loudness):

| Mix | Target voice retained | Wanted second voice retained | Backing retained |
| --- | ---: | ---: | ---: |
| Voice + chord | 0.0050 (−46 dB) | n/a | 0.972 |
| Two voices + chord | 0.0214 (−33 dB) | 0.0149 | 0.977 |

Nonzero target-correlated signal remained. Projection does not measure intelligibility, capture every artifact or certify privacy/no exposure. The other speaker also largely disappeared during the edit. Only one off-the-shelf music separator tested, not a rejection of all separation models. The ideal reference keeps the second speaker and backing because it has the actual original sources. The more realistic next trials are film-trained [dialogue/music/effects separation](https://github.com/merlresearch/cocktail-fork-separation) and true isolated production tracks when available, with independent word timing and a listening test. A dialogue stem may contain multiple voices. For mixed overlapping speech, isolate the target speaker as a separate experiment or use an explicitly chosen fallback. Keep audio edits separate from visual filters, which this experiment does not address.
