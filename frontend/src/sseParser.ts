/**
 * Incremental parser for the Server-Sent Events wire format (text/event-stream), following the
 * WHATWG spec: `field: value` lines, `:` comment lines (our heartbeats), a blank line ends an
 * event, and `id` persists until changed. It exists because the browser's EventSource cannot
 * send an Authorization header; the same stream is read with fetch instead.
 */
export interface SseMessage {
  id: string
  event: string
  data: string
}

export class SseParser {
  private buffer = ''
  private lastId = ''
  private event = ''
  private data: string[] = []

  /** Feed the next chunk of decoded text; returns the events completed by it. */
  feed(chunk: string): SseMessage[] {
    // A lone trailing \r might be the first half of \r\n, so hold it until more text arrives.
    const text = (this.buffer + chunk).replace(/\r\n/g, '\n').replace(/\r(?!$)/g, '\n')
    const lines = text.split('\n')
    this.buffer = lines.pop() ?? ''
    const out: SseMessage[] = []
    for (const line of lines) {
      if (line === '') {
        if (this.data.length > 0) {
          out.push({ id: this.lastId, event: this.event || 'message', data: this.data.join('\n') })
        }
        this.event = ''
        this.data = []
      } else if (!line.startsWith(':')) {
        const i = line.indexOf(':')
        const field = i < 0 ? line : line.slice(0, i)
        const value = i < 0 ? '' : line.slice(i + 1).replace(/^ /, '')
        if (field === 'id' && !value.includes('\0')) this.lastId = value
        else if (field === 'event') this.event = value
        else if (field === 'data') this.data.push(value)
        // "retry" and unknown fields are ignored; reconnection timing is handled by the caller.
      }
    }
    return out
  }
}
