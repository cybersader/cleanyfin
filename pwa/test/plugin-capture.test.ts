import { describe, expect, test } from 'bun:test';
import * as pwa from '../src/capture';
// The household capture page served by the plugin (slice 2) carries its own copy of the capture rules,
// because the plugin ships plain files with no bundler. This keeps the two from drifting apart.
// @ts-expect-error plain ES module without type declarations
import * as plugin from '../../plugin/Marking/Web/capture-core.js';

const reportedAtMs = Date.parse('2026-11-21T18:00:05.000Z');
const report: pwa.SessionReport = {
  serverRef: 'home-A',
  userId: '3b1c0000000000000000000000000001',
  itemId: '11111111111111111111111111111111',
  mediaSourceId: '11111111111111111111111111111111',
  positionMs: 5000,
  reportedAtMs,
  paused: false,
};

describe('plugin capture page and PWA build identical capture-v1', () => {
  test('constants and quick categories', () => {
    expect(plugin.REACTION_MS).toBe(pwa.REACTION_MS);
    expect(plugin.AFTER_MS).toBe(pwa.AFTER_MS);
    expect(plugin.QUICK_CATEGORIES).toEqual(pwa.QUICK_CATEGORIES);
  });

  for (const [name, r, age] of [['playing', report, 2300], ['paused', { ...report, paused: true }, 9000],
    ['stale report', { ...report, reportedAtMs: reportedAtMs - 700_000 }, 0]] as const) {
    test(`point and span (${name})`, () => {
      const now = reportedAtMs + age;
      expect(plugin.pointCapture(r, now, 'cap:a', 'violence')).toEqual(pwa.pointCapture(r, now, 'cap:a', 'violence'));
      expect(plugin.pointCapture(r, now, 'cap:b')).toEqual(pwa.pointCapture(r, now, 'cap:b'));
      const start = pwa.estimate(r, now - 1000);
      expect(plugin.estimate(r, now - 1000)).toEqual(start);
      expect(plugin.spanCapture(start, r, now, 'cap:c', 'other')).toEqual(pwa.spanCapture(start, r, now, 'cap:c', 'other'));
    });
  }

  test('refs', () => {
    for (const t of ['jf:user:3b1c-0000 {x}', '-lead', 'ok:1']) expect(plugin.toRef(t)).toBe(pwa.toRef(t));
    expect(plugin.newCaptureRef(1700000000000, () => 0.25)).toBe(pwa.newCaptureRef(1700000000000, () => 0.25));
  });
});
