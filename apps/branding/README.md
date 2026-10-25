# Parallel Codex Branding

`parallel-codex-icon-1024.png` is the canonical app artwork. Both applications
consume this file during their normal builds:

- Android generates its launcher and in-app resource under `app/build/`.
- macOS packages the PNG directly for in-app artwork. Its icon builder separately
  normalizes opacity and canvas insets before generating `.icns` under `.build/`.
  The small sizes use PNG-backed Retina representations, which macOS also scales
  for 1x displays. This avoids Tahoe adding a white backing tile to the legacy
  ARGB representations emitted by `iconutil` for 16/32 px 1x slots.
  The bundle uses `ParallelCodex.icns` so the normalized asset does not reuse
  AppKit's cached `AppIcon.icns` appearance after an upgrade.

Run `bash apps/macos/scripts/test-app-icon.sh` on macOS to verify the raster sizes,
transparency, ICNS decoding, and actual system icon rendering from 16 to 1024 px.
The build does not modify the shared master or Android's icon resources.

Generated platform copies do not belong in source directories or `dist`. Keep
new brand work outside this directory until it replaces the canonical master;
this avoids multiple similarly named icons drifting apart.
