FROM debian:trixie-slim@sha256:a72b4433de1cf7d6bae0c4cee74d0547f58601a643b3375f070c51600d63b6b2 AS base

FROM base AS mpv
RUN apt-get update \
 && apt-get install -y --no-install-recommends mpv \
 && rm -rf /var/lib/apt/lists/*

FROM base AS kodi
RUN apt-get update \
 && apt-get install -y --no-install-recommends kodi xvfb xauth pulseaudio pulseaudio-utils ffmpeg python3 libgl1-mesa-dri \
 && rm -rf /var/lib/apt/lists/*
