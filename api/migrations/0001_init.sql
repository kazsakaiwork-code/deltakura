-- Deltakura signals D1 schema, v0.
--
-- Loaded only after a Cloudflare account is provisioned (pending operator approval):
--   wrangler d1 migrations apply signals --local     # offline, for development
--   wrangler d1 migrations apply signals --remote    # needs the account
--
-- The column list is the response allowlist from mcp/src/query/nta.ts and
-- nothing else. There is deliberately no 担当者, no 氏名, no 代表者, no
-- street-level address and no free-text 変更事由 column: a personal string
-- cannot be stored here, so it cannot be served from here.

CREATE TABLE IF NOT EXISTS nta_change (
  corporate_number           TEXT    NOT NULL,
  change_date                TEXT    NOT NULL,
  sequence_number            INTEGER NOT NULL,
  correct_flag               INTEGER NOT NULL DEFAULT 0,
  process_code               TEXT    NOT NULL,
  update_date                TEXT,
  file_date                  TEXT    NOT NULL,
  name                       TEXT,
  kind_code                  TEXT,
  prefecture                 TEXT,
  prefecture_code            TEXT,
  city                       TEXT,
  close_date                 TEXT,
  close_cause                TEXT,
  successor_corporate_number TEXT,
  assignment_date            TEXT,
  latest_flag                INTEGER NOT NULL DEFAULT 0,
  PRIMARY KEY (corporate_number, change_date, sequence_number, correct_flag)
);

-- The lookup path: one corporate number, newest change first.
CREATE INDEX IF NOT EXISTS idx_nta_change_number_date
  ON nta_change (corporate_number, change_date DESC, sequence_number DESC);

-- Backfill and day-range scans.
CREATE INDEX IF NOT EXISTS idx_nta_change_file_date
  ON nta_change (file_date);

-- Pre-aggregated daily volume, so the feed and the summary never scan records.
CREATE TABLE IF NOT EXISTS nta_daily (
  file_date TEXT PRIMARY KEY,
  changes   INTEGER NOT NULL,
  status    TEXT NOT NULL DEFAULT 'ok'
);

CREATE TABLE IF NOT EXISTS nta_daily_kind (
  file_date    TEXT    NOT NULL,
  process_code TEXT    NOT NULL,
  changes      INTEGER NOT NULL,
  PRIMARY KEY (file_date, process_code)
);

-- What the ETL has loaded, so /v0/health can state its own coverage honestly.
CREATE TABLE IF NOT EXISTS load_log (
  loaded_at   TEXT NOT NULL,
  source_id   TEXT NOT NULL,
  file_date   TEXT NOT NULL,
  rows_loaded INTEGER NOT NULL,
  PRIMARY KEY (source_id, file_date)
);
