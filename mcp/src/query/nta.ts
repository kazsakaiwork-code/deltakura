/**
 * Pure helpers for the 国税庁 法人番号 (corporate number) change register.
 *
 * No I/O. Shared by the MCP server (local file mode) and the Cloudflare Worker,
 * so both apply the same field allowlist and the same personal-data guard.
 */

export const NTA_ATTRIBUTION =
  '出典：国税庁法人番号公表サイト（国税庁）（https://www.houjin-bangou.nta.go.jp/download/sabun/）';
export const NTA_LICENSE =
  '公共データ利用規約(第1.0版) — commercial reuse and redistribution permitted with attribution; modifications must be declared.';
export const NTA_SOURCE_URL = 'https://www.houjin-bangou.nta.go.jp/download/sabun/';

export const NTA_CAVEATS_EN = [
  'This archive is built from the daily 差分 (change) files, which the publisher retains for only about 40 days. It therefore covers changes from the day collection started, not the whole register.',
  'A record appears here only if something about the corporation changed on that day. Absence means "no change published", never "does not exist".',
  'Diff files are not published on weekends, Japanese public holidays or 29 December - 3 January. A missing date is the publisher\'s calendar, not a collection failure.',
  'Addresses are the registered address of a 法人 or public body. 法人番号 are not issued to 個人事業主, so this dataset contains no sole-proprietor identities, and this server returns none.',
  'The data is used in modified form (normalised, deduplicated, re-encoded), as the licence requires to be declared.'
];

export const NTA_CAVEATS_JA = [
  'この履歴は日次の差分ファイルから作っています。公表側の保存期間は約40日なので、収集開始日以降の変更のみを含みます。',
  'ある日に何らかの変更があった法人だけが現れます。記録がないことは「変更の公表がない」であって「存在しない」ではありません。',
  '土日祝日と12/29〜1/3は差分ファイルが公表されません。日付の欠落は公表側の暦であり、収集漏れではありません。',
  '住所は法人・公共機関の登記上の所在地です。法人番号は個人事業主には指定されないため、個人の情報は含みません（本サーバも返しません）。',
  'ライセンスの要求どおり、加工（正規化・重複排除・再エンコード）して利用していることを明示します。'
];

/** 法人種別 codes as published by the NTA. */
export const KIND_CODES: Record<string, { ja: string; en: string; corporate: boolean }> = {
  '101': { ja: '国の機関', en: 'national government body', corporate: true },
  '201': { ja: '地方公共団体', en: 'local government body', corporate: true },
  '301': { ja: '株式会社', en: 'kabushiki kaisha (joint-stock company)', corporate: true },
  '302': { ja: '有限会社', en: 'yugen kaisha (limited company)', corporate: true },
  '303': { ja: '合名会社', en: 'gomei kaisha (general partnership company)', corporate: true },
  '304': { ja: '合資会社', en: 'goshi kaisha (limited partnership company)', corporate: true },
  '305': { ja: '合同会社', en: 'godo kaisha (LLC)', corporate: true },
  '399': { ja: 'その他の設立登記法人', en: 'other registered corporation', corporate: true },
  '401': { ja: '外国会社等・その他', en: 'foreign company or other', corporate: false },
  '499': { ja: 'その他', en: 'other (including unincorporated associations)', corporate: false }
};

/** 処理区分 (what kind of change this row records). */
export const PROCESS_CODES: Record<string, { ja: string; en: string }> = {
  '01': { ja: '新規設立等', en: 'newly assigned' },
  '11': { ja: '商号又は名称の変更', en: 'name changed' },
  '12': { ja: '国内所在地の変更', en: 'domestic address changed' },
  '13': { ja: '国外所在地の変更', en: 'overseas address changed' },
  '21': { ja: '登記記録の閉鎖等', en: 'registry record closed' },
  '22': { ja: '登記記録の復活等', en: 'registry record reinstated' },
  '71': { ja: '吸収合併', en: 'absorbed by merger' },
  '72': { ja: '吸収合併無効', en: 'merger invalidated' },
  '81': { ja: '商号の登記の抹消', en: 'trade-name registration removed' },
  '99': { ja: '削除', en: 'deleted' }
};

/** 閉鎖事由. */
export const CLOSE_CAUSES: Record<string, { ja: string; en: string }> = {
  '01': { ja: '清算の結了等', en: 'liquidation completed' },
  '11': { ja: '合併による解散等', en: 'dissolved by merger' },
  '21': { ja: '登記官による閉鎖', en: 'closed by the registrar' },
  '31': { ja: 'その他の関係による抹消', en: 'removed for another reason' }
};

export function labelKind(code: string): { code: string; ja: string; en: string } {
  const k = KIND_CODES[code];
  return { code, ja: k?.ja ?? '不明', en: k?.en ?? 'unknown' };
}

export function labelProcess(code: string): { code: string; ja: string; en: string } {
  const p = PROCESS_CODES[code];
  return { code, ja: p?.ja ?? '不明', en: p?.en ?? 'unknown' };
}

export function labelCloseCause(code: string): { code: string; ja: string; en: string } | null {
  if (!code) return null;
  const c = CLOSE_CAUSES[code];
  return { code, ja: c?.ja ?? '不明', en: c?.en ?? 'unknown' };
}

/**
 * NTA check digit: the first digit of a 13-digit 法人番号 is
 * 9 - ((sum of the other 12 digits weighted 1,2,1,2,... from the right) mod 9).
 */
export function isValidCorporateNumber(input: string): boolean {
  const s = normalizeCorporateNumber(input);
  if (!/^\d{13}$/.test(s)) return false;
  const check = Number(s[0]);
  const body = s.slice(1);
  let sum = 0;
  for (let n = 1; n <= 12; n++) {
    const digit = Number(body[12 - n]);
    sum += digit * (n % 2 === 1 ? 1 : 2);
  }
  return check === 9 - (sum % 9);
}

/** Strips spaces, hyphens and full-width digits. */
export function normalizeCorporateNumber(input: string): string {
  return input.normalize('NFKC').replace(/[\s\-‐－]/g, '');
}

/**
 * Field allowlist. Anything not named here never leaves this server, which is
 * how rule R3 (drop any 担当者/氏名/連絡先 field) is enforced by construction
 * rather than by a blocklist that a schema change could slip past.
 */
export const ALLOWED_RECORD_FIELDS = [
  'corporate_number',
  'corporate_number_valid',
  'name',
  'kind_code',
  'prefecture',
  'prefecture_code',
  'city',
  'process_code',
  'correct_flag',
  'update_date',
  'change_date',
  'sequence_number',
  'close_date',
  'close_cause',
  'successor_corporate_number',
  'assignment_date',
  'latest_flag',
  'file_date'
] as const;

/**
 * Fields whose mere presence in an input file means the layout has changed in a
 * way that could carry a natural person. Seeing one is a stop, not a filter:
 * rule R3 says such a column never reaches us, so if it does, we refuse the file
 * rather than quietly reading around it.
 *
 * Columns the publisher legitimately ships and we simply never emit
 * (street_number, post_code, furigana, change_cause) are not listed here - the
 * allowlist above is what keeps them out of responses.
 */
const PERSONAL_FIELD_PATTERN =
  /担当|氏名|代表者|代表取締役|役員|連絡先|電話|メール|個人|生年月日|e-?mail|phone|telephone|contact_|person_|birth/i;

export function assertNoPersonalFields(fieldNames: Iterable<string>): void {
  for (const f of fieldNames) {
    if (PERSONAL_FIELD_PATTERN.test(f)) {
      throw new Error(
        `refusing to read this file: column "${f}" matches the personal-data field pattern (rule R3). ` +
          'The 法人番号 layout must not carry a natural person; a layout change fails closed.'
      );
    }
  }
}

/** Every raw column `toChange` is allowed to read. Anything else is ignored by construction. */
export function assertAllowedOutputFields(fieldNames: Iterable<string>): void {
  const allowed = new Set<string>(ALLOWED_RECORD_FIELDS);
  for (const f of fieldNames) {
    if (!allowed.has(f)) throw new Error(`field "${f}" is not in the response allowlist`);
  }
}

const CORPORATE_TOKENS =
  /(株式会社|有限会社|合同会社|合名会社|合資会社|一般社団|一般財団|公益社団|公益財団|医療法人|社会福祉法人|学校法人|宗教法人|特定非営利活動法人|独立行政法人|国立大学法人|地方独立行政法人|協同組合|事業協同組合|信用金庫|信用組合|農業協同組合|漁業協同組合|商工会|組合|会社|法人|財団|社団|機構|協会|連合|会館|大学|学園|病院|クリニック|銀行|公社|公団|事務所|商店|工業|産業|建設|興業|運輸|製作所|inc\.?|corp\.?|ltd\.?|llc|k\.k\.|co\.)/i;

/**
 * Conservative guard for the two 法人種別 that are not registered corporations
 * (外国会社等・その他 and その他): if such a record carries a short name with no
 * corporate token at all, we mask it rather than risk publishing a person.
 * 法人番号 are not issued to 個人事業主, so this should never fire - it exists so
 * that a future layout or code change fails closed.
 */
export function shouldMaskName(kindCode: string, name: string): boolean {
  const kind = KIND_CODES[kindCode];
  if (kind?.corporate) return false;
  const trimmed = name.normalize('NFKC').trim();
  if (!trimmed) return false;
  if (CORPORATE_TOKENS.test(trimmed)) return false;
  return trimmed.replace(/[\s・]/g, '').length <= 6;
}

export interface NtaChange {
  change_date: string;
  update_date: string;
  file_date: string;
  sequence_number: number;
  correct_flag: boolean;
  kind: { code: string; ja: string; en: string };
  process: { code: string; ja: string; en: string };
  name: string | null;
  name_masked_reason?: string;
  prefecture: string | null;
  prefecture_code: string | null;
  city: string | null;
  close_date: string | null;
  close_cause: { code: string; ja: string; en: string } | null;
  successor_corporate_number: string | null;
  latest_flag: boolean;
}

export type RawRecord = Record<string, string>;

/** Shape one normalized CSV row into the public change object, applying the guards. */
export function toChange(raw: RawRecord): NtaChange {
  const kindCode = raw.kind_code ?? '';
  const rawName = raw.name ?? '';
  const mask = shouldMaskName(kindCode, rawName);
  return {
    change_date: raw.change_date ?? '',
    update_date: raw.update_date ?? '',
    file_date: raw.file_date ?? '',
    sequence_number: Number(raw.sequence_number ?? 0) || 0,
    correct_flag: raw.correct_flag === '1',
    kind: labelKind(kindCode),
    process: labelProcess(raw.process_code ?? ''),
    name: mask ? null : rawName || null,
    ...(mask
      ? {
          name_masked_reason:
            'masked: 法人種別 is not a registered corporation and the name carries no corporate token (fail-closed personal-data guard)'
        }
      : {}),
    prefecture: raw.prefecture || null,
    prefecture_code: raw.prefecture_code || null,
    city: raw.city || null,
    close_date: raw.close_date || null,
    close_cause: labelCloseCause(raw.close_cause ?? ''),
    successor_corporate_number: raw.successor_corporate_number || null,
    latest_flag: raw.latest_flag === '1'
  };
}

export interface CorporateLookupResult {
  corporate_number: string;
  found: boolean;
  checksum_valid: boolean;
  mode: 'local' | 'remote';
  name: string | null;
  kind: { code: string; ja: string; en: string } | null;
  address: { prefecture: string | null; prefecture_code: string | null; city: string | null };
  latest_change: NtaChange | null;
  change_count: number;
  history: NtaChange[];
  coverage: { from: string | null; to: string | null; days_with_data: number };
  license: string;
  attribution: string;
  source_url: string;
  caveats: string[];
  notes: string[];
}

/** Newest change first: by change_date, then sequence_number, corrections last-wins. */
export function sortChanges(changes: NtaChange[]): NtaChange[] {
  return [...changes].sort((a, b) => {
    if (a.change_date !== b.change_date) return a.change_date < b.change_date ? 1 : -1;
    if (a.sequence_number !== b.sequence_number) return b.sequence_number - a.sequence_number;
    return Number(b.correct_flag) - Number(a.correct_flag);
  });
}

export function buildLookupResult(
  corporateNumber: string,
  changes: NtaChange[],
  opts: {
    mode: 'local' | 'remote';
    coverage: { from: string | null; to: string | null; days_with_data: number };
    language?: 'en' | 'ja';
    historyLimit?: number;
  }
): CorporateLookupResult {
  const sorted = sortChanges(changes);
  const latest = sorted[0] ?? null;
  const notes: string[] = [];
  if (!sorted.length) {
    notes.push(
      'No change for this 法人番号 in the collected window. That means no change was published for it in ' +
        'that period - it says nothing about whether the corporation exists.'
    );
  }
  if (latest?.name === null && latest.name_masked_reason) notes.push(latest.name_masked_reason);
  return {
    corporate_number: corporateNumber,
    found: sorted.length > 0,
    checksum_valid: isValidCorporateNumber(corporateNumber),
    mode: opts.mode,
    name: latest?.name ?? null,
    kind: latest?.kind ?? null,
    address: {
      prefecture: latest?.prefecture ?? null,
      prefecture_code: latest?.prefecture_code ?? null,
      city: latest?.city ?? null
    },
    latest_change: latest,
    change_count: sorted.length,
    history: sorted.slice(0, opts.historyLimit ?? 20),
    coverage: opts.coverage,
    license: NTA_LICENSE,
    attribution: NTA_ATTRIBUTION,
    source_url: NTA_SOURCE_URL,
    caveats: opts.language === 'ja' ? NTA_CAVEATS_JA : NTA_CAVEATS_EN,
    notes
  };
}

export interface DiffSummaryDay {
  date: string;
  changes: number;
  by_change_kind?: Record<string, number>;
}

export interface DiffSummaryResult {
  mode: 'local' | 'remote';
  from: string;
  to: string;
  days_with_data: number;
  days_without_file: number;
  total_changes: number;
  mean_changes_per_published_day: number | null;
  days: DiffSummaryDay[];
  /** Present when days carry by_change_kind: what each 処理区分 code means. */
  change_kind_labels?: Record<string, { ja: string; en: string }>;
  coverage: { from: string | null; to: string | null; days_with_data: number };
  license: string;
  attribution: string;
  source_url: string;
  caveats: string[];
  notes: string[];
}

export function isIsoDate(s: string): boolean {
  return /^\d{4}-\d{2}-\d{2}$/.test(s) && !Number.isNaN(Date.parse(`${s}T00:00:00Z`));
}

export function eachDate(from: string, to: string): string[] {
  const out: string[] = [];
  const end = Date.parse(`${to}T00:00:00Z`);
  for (let t = Date.parse(`${from}T00:00:00Z`); t <= end; t += 86400000) {
    out.push(new Date(t).toISOString().slice(0, 10));
  }
  return out;
}

export function buildDiffSummary(
  from: string,
  to: string,
  counts: Map<string, number>,
  byKind: Map<string, Record<string, number>> | undefined,
  opts: { mode: 'local' | 'remote'; coverage: { from: string | null; to: string | null; days_with_data: number }; language?: 'en' | 'ja' }
): DiffSummaryResult {
  const days: DiffSummaryDay[] = [];
  let total = 0;
  let withData = 0;
  let withoutFile = 0;
  for (const date of eachDate(from, to)) {
    const n = counts.get(date);
    if (n === undefined) {
      withoutFile++;
      continue;
    }
    withData++;
    total += n;
    days.push({ date, changes: n, ...(byKind?.get(date) ? { by_change_kind: byKind.get(date) } : {}) });
  }
  const notes: string[] = [];
  if (withoutFile > 0) {
    notes.push(
      `${withoutFile} date(s) in the range have no diff file. The publisher issues none on weekends, ` +
        'Japanese public holidays or 29 Dec - 3 Jan, and our own collection window starts at ' +
        `${opts.coverage.from ?? 'an unknown date'}.`
    );
  }
  if (opts.coverage.from && from < opts.coverage.from) {
    notes.push(`Requested range starts before the collected window (${opts.coverage.from}); earlier days are simply absent.`);
  }
  return {
    mode: opts.mode,
    from,
    to,
    days_with_data: withData,
    days_without_file: withoutFile,
    total_changes: total,
    mean_changes_per_published_day: withData > 0 ? Math.round(total / withData) : null,
    days,
    ...(byKind ? { change_kind_labels: PROCESS_CODES } : {}),
    coverage: opts.coverage,
    license: NTA_LICENSE,
    attribution: NTA_ATTRIBUTION,
    source_url: NTA_SOURCE_URL,
    caveats: opts.language === 'ja' ? NTA_CAVEATS_JA : NTA_CAVEATS_EN,
    notes
  };
}
