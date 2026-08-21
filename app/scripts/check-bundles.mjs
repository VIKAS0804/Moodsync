#!/usr/bin/env node
/**
 * Assert every platform actually produced a bundle, then look for web-only
 * globals reached without a Platform guard.
 *
 * `tsc` and a web bundle both compiled cleanly through two crashes that only
 * happened on a device: `window.addEventListener` (window exists in React
 * Native but has no DOM events) and platform APIs called unguarded. Types can't
 * catch those, so this checks the artefacts instead.
 */

import { readFileSync, readdirSync, rmSync, existsSync } from 'node:fs';
import { join } from 'node:path';

const DIST = '.verify-dist';
const JS = join(DIST, '_expo/static/js');
const PLATFORMS = ['web', 'ios', 'android'];

let failed = false;
const fail = (message) => {
  console.error(`  FAIL  ${message}`);
  failed = true;
};
const pass = (message) => console.log(`  ok    ${message}`);

// Bundle checks need artefacts; the source audit doesn't. Keeping them
// independent means the guard check is usable on its own, which is when you
// actually want it -- mid-edit, not after a three-minute export.
const haveBundles = existsSync(JS);
if (!haveBundles) {
  console.log('  skip  bundle sizes (no export found; run `npm run verify`)');
}

for (const platform of haveBundles ? PLATFORMS : []) {
  const dir = join(JS, platform);
  if (!existsSync(dir)) {
    fail(`${platform}: no bundle produced`);
    continue;
  }
  const files = readdirSync(dir).filter((f) => /\.(js|hbc)$/.test(f));
  if (files.length === 0) {
    fail(`${platform}: bundle directory is empty`);
    continue;
  }
  const bytes = files.reduce((total, f) => total + readFileSync(join(dir, f)).length, 0);
  if (bytes < 500_000) {
    fail(`${platform}: bundle is only ${bytes} bytes — probably truncated`);
    continue;
  }
  pass(`${platform}: ${(bytes / 1e6).toFixed(2)} MB`);
}

// Source-level guard check. Anything touching a DOM-only global has to be
// reachable only on web, and `typeof window === 'undefined'` does not establish
// that -- React Native defines `window`.
const SOURCES = ['app', 'src'];
const WEB_ONLY = /\b(window\.addEventListener|window\.removeEventListener|document\.\w+|localStorage\.\w+)/;

function walk(dir) {
  const out = [];
  for (const entry of readdirSync(dir, { withFileTypes: true })) {
    const path = join(dir, entry.name);
    if (entry.isDirectory()) out.push(...walk(path));
    else if (/\.tsx?$/.test(entry.name)) out.push(path);
  }
  return out;
}

let unguarded = 0;
for (const file of SOURCES.flatMap(walk)) {
  const text = readFileSync(file, 'utf8');
  if (!WEB_ONLY.test(text)) continue;
  // The file must establish a web-only path somehow.
  const guarded =
    text.includes("Platform.OS === 'web'") ||
    text.includes("Platform.OS !== 'web'") ||
    text.includes('webPlaybackSupported');
  if (!guarded) {
    fail(`${file}: uses a DOM-only global with no Platform.OS guard`);
    unguarded += 1;
  }
}
if (unguarded === 0) pass('DOM-only globals are all behind Platform guards');

rmSync(DIST, { recursive: true, force: true });
console.log(failed ? '\nverify FAILED' : '\nverify passed');
process.exit(failed ? 1 : 0);
