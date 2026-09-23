/**
 * Incremental RFC 4180 CSV reader over a gzipped file.
 *
 * Streaming rather than read-whole-file, because the normalized store grows by a
 * file a night and a lookup should not scale its memory with the archive.
 */
import { createReadStream } from 'node:fs';
import { createGunzip } from 'node:zlib';

export class CsvChunkParser {
  private field = '';
  private row: string[] = [];
  private inQuotes = false;
  private quoteSeen = false;

  push(chunk: string, onRow: (row: string[]) => void): void {
    for (let i = 0; i < chunk.length; i++) {
      const c = chunk[i];
      if (this.inQuotes) {
        if (this.quoteSeen) {
          this.quoteSeen = false;
          if (c === '"') {
            this.field += '"';
            continue;
          }
          this.inQuotes = false;
          // fall through and handle `c` as an unquoted character
        } else if (c === '"') {
          this.quoteSeen = true;
          continue;
        } else {
          this.field += c;
          continue;
        }
      }
      if (c === '"') {
        this.inQuotes = true;
        continue;
      }
      if (c === ',') {
        this.row.push(this.field);
        this.field = '';
        continue;
      }
      if (c === '\r') continue;
      if (c === '\n') {
        this.row.push(this.field);
        onRow(this.row);
        this.row = [];
        this.field = '';
        continue;
      }
      this.field += c;
    }
  }

  end(onRow: (row: string[]) => void): void {
    this.quoteSeen = false;
    this.inQuotes = false;
    if (this.field !== '' || this.row.length > 0) {
      this.row.push(this.field);
      onRow(this.row);
      this.row = [];
      this.field = '';
    }
  }
}

/** Parse a whole CSV string into rows (used for the small manifest file). */
export function parseCsvText(text: string): string[][] {
  const parser = new CsvChunkParser();
  const rows: string[][] = [];
  parser.push(text.charCodeAt(0) === 0xfeff ? text.slice(1) : text, (r) => rows.push(r));
  parser.end((r) => rows.push(r));
  return rows;
}

export type RowVisitor = (record: Record<string, string>) => void | 'stop';

/**
 * Stream a gzipped (or plain) CSV with a header row, calling `visit` per record.
 * Returning 'stop' from the visitor ends the scan of that file early.
 */
export async function streamCsvRecords(path: string, visit: RowVisitor): Promise<void> {
  const source = createReadStream(path);
  const stream = path.endsWith('.gz') ? source.pipe(createGunzip()) : source;
  stream.setEncoding('utf8');
  const parser = new CsvChunkParser();
  let header: string[] | undefined;
  let stopped = false;

  const onRow = (row: string[]) => {
    if (stopped) return;
    if (!header) {
      header = row.map((h) => h.replace(/^﻿/, '').trim());
      return;
    }
    if (row.length === 1 && row[0] === '') return;
    const record: Record<string, string> = {};
    for (let i = 0; i < header.length; i++) record[header[i]] = row[i] ?? '';
    if (visit(record) === 'stop') stopped = true;
  };

  try {
    for await (const chunk of stream as AsyncIterable<string>) {
      parser.push(chunk, onRow);
      if (stopped) break;
    }
  } finally {
    source.destroy();
  }
  if (!stopped) parser.end(onRow);
}
