#!/usr/bin/env bun
// Cross-platform preflight for docs/.
//
// The repo is used from both WSL (linux) and Windows. `bun install` writes
// platform-native stubs into node_modules/.bin; switching sides without
// reinstalling makes the `astro` binary look "missing". This script
// reinstalls when node_modules is absent, the astro bin is missing, or the
// recorded platform differs. Fast (~ms) on the happy path.
import { existsSync, readFileSync, writeFileSync } from 'node:fs';
import { spawnSync } from 'node:child_process';
import { fileURLToPath } from 'node:url';
import { dirname, resolve } from 'node:path';

const here = dirname(fileURLToPath(import.meta.url));
const root = resolve(here, '..');
const marker = resolve(root, 'node_modules/.platform');
const astroBin = resolve(
  root,
  process.platform === 'win32' ? 'node_modules/.bin/astro.cmd' : 'node_modules/.bin/astro'
);

function needsInstall() {
  if (!existsSync(resolve(root, 'node_modules'))) return 'no node_modules';
  if (!existsSync(astroBin)) return 'astro binary missing';
  try {
    if (readFileSync(marker, 'utf8').trim() !== process.platform) return 'platform changed';
  } catch {
    return 'no platform marker';
  }
  return null;
}

const reason = needsInstall();
if (reason) {
  console.log(`\x1b[33m[preflight]\x1b[0m installing deps (${reason})…`);
  const r = spawnSync('bun', ['install'], { cwd: root, stdio: 'inherit', shell: process.platform === 'win32' });
  if (r.status !== 0) process.exit(r.status ?? 1);
  writeFileSync(marker, process.platform);
} else {
  console.log('\x1b[90m[preflight] deps OK\x1b[0m');
}
