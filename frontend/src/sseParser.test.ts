// Run with `npm test` (Node's built-in test runner; no extra dependencies).
import assert from 'node:assert/strict'
import { test } from 'node:test'
import { SseParser } from './sseParser.ts'

const FRAME = 'id: 7\nevent: span.end\ndata: {"a":1}\n\n'
const EXPECTED = [{ id: '7', event: 'span.end', data: '{"a":1}' }]

test('parses a whole frame', () => {
  assert.deepEqual(new SseParser().feed(FRAME), EXPECTED)
})

test('gives the same result however the bytes are split across chunks', () => {
  for (let i = 1; i < FRAME.length; i++) {
    const p = new SseParser()
    assert.deepEqual([...p.feed(FRAME.slice(0, i)), ...p.feed(FRAME.slice(i))], EXPECTED, `split at ${i}`)
  }
})

test('ignores heartbeat comments and handles several frames per chunk', () => {
  const p = new SseParser()
  assert.deepEqual(p.feed(': heartbeat\n\n'), [])
  const out = p.feed('id: 1\nevent: a\ndata: x\n\nid: 2\nevent: b\ndata: y\n\n')
  assert.deepEqual(
    out.map((m) => [m.id, m.event, m.data]),
    [
      ['1', 'a', 'x'],
      ['2', 'b', 'y'],
    ],
  )
})

test('handles CRLF, including a CRLF split across chunks, and multi-line data', () => {
  assert.deepEqual(new SseParser().feed('data: l1\r\ndata: l2\r\n\r\n'), [
    { id: '', event: 'message', data: 'l1\nl2' },
  ])
  const p = new SseParser()
  const out = [...p.feed('data: z\r'), ...p.feed('\n\r'), ...p.feed('\n')]
  assert.deepEqual(out, [{ id: '', event: 'message', data: 'z' }])
})

test('the id persists across events until changed; the event name resets', () => {
  const out = new SseParser().feed('id: 5\nevent: a\ndata: 1\n\ndata: 2\n\n')
  assert.deepEqual(
    out.map((m) => [m.id, m.event]),
    [
      ['5', 'a'],
      ['5', 'message'],
    ],
  )
})

test("parses the server's end frame", () => {
  assert.deepEqual(new SseParser().feed('event: end\ndata: {}\n\n'), [
    { id: '', event: 'end', data: '{}' },
  ])
})
