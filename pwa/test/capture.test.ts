import { describe, expect, test } from 'bun:test';
import { AFTER_MS, REACTION_MS, estimate, newCaptureRef, pointCapture, spanCapture, toRef, type SessionReport } from '../src/capture';

const reportedAtMs = Date.parse('2026-11-21T18:00:05.000Z');
const report: SessionReport = {
  serverRef: 'home-A',
  userId: '3b1c0000000000000000000000000001',
  itemId: '11111111111111111111111111111111',
  mediaSourceId: '11111111111111111111111111111111',
  positionMs: 5000,
  reportedAtMs,
  paused: false,
};
const REF = /^[A-Za-z0-9][A-Za-z0-9:._-]*$/;

describe('capture-v1 from a session report', () => {
  test('a press adds the age of the last report while playing and widens the window by it', () => {
    const c = pointCapture(report, reportedAtMs + 2000, 'cap:test1', 'violence');
    expect(c.atMs).toBe(7000);
    expect(c.window).toEqual({ beforeMs: REACTION_MS + 2000, afterMs: AFTER_MS });
    expect(c.observed).toEqual({ sessionPositionMs: 5000, reportedAt: '2026-11-21T18:00:05.000Z', paused: false });
    expect(c.clock).toEqual({ kind: 'source' });
    expect(c.surface).toBe('phone-remote');
    expect(c.category).toBe('violence');
    expect(c.author).toBe('jf:user:3b1c0000000000000000000000000001');
    expect('endAtMs' in c).toBe(false);
  });

  test('a paused report is not advanced', () => {
    expect(estimate({ ...report, paused: true }, reportedAtMs + 9000).atMs).toBe(5000);
  });

  test('a one-tap mark has no category, and a span always ends after it starts', () => {
    expect('category' in pointCapture(report, reportedAtMs, 'cap:x')).toBe(false);
    const start = estimate(report, reportedAtMs);
    const span = spanCapture(start, { ...report, positionMs: 4000 }, reportedAtMs, 'cap:y');
    expect(span.kind).toBe('span');
    expect(span.endAtMs).toBe(start.atMs + 1);
  });

  test('refs and ids always match the schema ref pattern', () => {
    expect(newCaptureRef(Date.now(), () => 0.5)).toMatch(REF);
    expect(toRef('jf:user:3b1c-0000 {x}')).toBe('jf:user:3b1c-0000x');
    expect(toRef('-lead')).toMatch(REF);
  });
});
