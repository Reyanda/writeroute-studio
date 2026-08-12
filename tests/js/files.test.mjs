/* Tests for the client-side document layer.
 *
 * `static/files.js` decodes uploads and builds exports entirely in the browser, and it
 * had no automated coverage: two bugs in it were found only by hand-testing, and a third
 * would not have been caught at all. Both of those bugs are pinned below.
 *
 * Run with `node --test tests/js`. No package installs — the DOM pieces the module needs
 * (DOMParser, Blob, File, TextDecoder) are supplied here, and JSZip is loaded from the
 * local node_modules copy if present or stubbed with a minimal zip reader/writer.
 */
import { test, describe, before } from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { dirname, join } from 'node:path';
import zlib from 'node:zlib';

const HERE = dirname(fileURLToPath(import.meta.url));
const ROOT = join(HERE, '..', '..');

/* ------------------------------------------------------------------ minimal zip */
/* files.js only needs `loadAsync(...).file(name).async('string')` and
 * `new JSZip(); file(); folder(); generateAsync({type:'blob'})`. Implementing that over
 * node's zlib keeps the suite dependency-free, and it means the DOCX bytes under test are
 * parsed by something other than the code that wrote them. */

function crc32(buf) {
  let c, table = crc32.table;
  if (!table) {
    table = crc32.table = new Int32Array(256);
    for (let n = 0; n < 256; n += 1) {
      c = n;
      for (let k = 0; k < 8; k += 1) c = c & 1 ? 0xedb88320 ^ (c >>> 1) : c >>> 1;
      table[n] = c;
    }
  }
  let crc = -1;
  for (let i = 0; i < buf.length; i += 1) crc = (crc >>> 8) ^ table[(crc ^ buf[i]) & 0xff];
  return (crc ^ -1) >>> 0;
}

class MiniZip {
  constructor() { this.entries = new Map(); }
  file(name, content) {
    if (content === undefined) {
      const data = this.entries.get(name);
      return data ? { async: () => Promise.resolve(data.toString('utf8')) } : null;
    }
    this.entries.set(name, Buffer.from(content));
    return this;
  }
  folder(prefix) {
    const parent = this;
    return { file: (name, content) => parent.file(`${prefix}/${name}`, content) };
  }
  async generateAsync() {
    const locals = [];
    const central = [];
    let offset = 0;
    for (const [name, raw] of this.entries) {
      const nameBuf = Buffer.from(name, 'utf8');
      const deflated = zlib.deflateRawSync(raw);
      const head = Buffer.alloc(30);
      head.writeUInt32LE(0x04034b50, 0); head.writeUInt16LE(20, 4);
      head.writeUInt16LE(8, 8); head.writeUInt32LE(crc32(raw), 14);
      head.writeUInt32LE(deflated.length, 18); head.writeUInt32LE(raw.length, 22);
      head.writeUInt16LE(nameBuf.length, 26);
      const local = Buffer.concat([head, nameBuf, deflated]);
      locals.push(local);
      const cen = Buffer.alloc(46);
      cen.writeUInt32LE(0x02014b50, 0); cen.writeUInt16LE(20, 4); cen.writeUInt16LE(20, 6);
      cen.writeUInt16LE(8, 10); cen.writeUInt32LE(crc32(raw), 16);
      cen.writeUInt32LE(deflated.length, 20); cen.writeUInt32LE(raw.length, 24);
      cen.writeUInt16LE(nameBuf.length, 28); cen.writeUInt32LE(offset, 42);
      central.push(Buffer.concat([cen, nameBuf]));
      offset += local.length;
    }
    const cd = Buffer.concat(central);
    const end = Buffer.alloc(22);
    end.writeUInt32LE(0x06054b50, 0);
    end.writeUInt16LE(this.entries.size, 8); end.writeUInt16LE(this.entries.size, 10);
    end.writeUInt32LE(cd.length, 12); end.writeUInt32LE(offset, 16);
    const bytes = Buffer.concat([...locals, cd, end]);
    return new Blob([bytes], { type: 'application/zip' });
  }
  static async loadAsync(buffer) {
    const buf = Buffer.from(buffer);
    const zip = new MiniZip();
    let i = 0;
    while (i < buf.length - 4) {
      if (buf.readUInt32LE(i) !== 0x04034b50) { i += 1; continue; }
      const method = buf.readUInt16LE(i + 8);
      const compressed = buf.readUInt32LE(i + 18);
      const nameLen = buf.readUInt16LE(i + 26);
      const extraLen = buf.readUInt16LE(i + 28);
      const name = buf.subarray(i + 30, i + 30 + nameLen).toString('utf8');
      const start = i + 30 + nameLen + extraLen;
      const raw = buf.subarray(start, start + compressed);
      zip.entries.set(name, method === 8 ? zlib.inflateRawSync(raw) : Buffer.from(raw));
      i = start + compressed;
    }
    return zip;
  }
}

/* ------------------------------------------------------------------- environment */
let files;

before(async () => {
  const { JSDOM } = await import('jsdom').catch(() => ({ JSDOM: null }));
  if (!JSDOM) {
    // DOMParser is the only DOM API the DOCX reader needs.
    const { DOMParser } = await import('@xmldom/xmldom').catch(() => ({ DOMParser: null }));
    if (!DOMParser) {
      throw new Error('install jsdom or @xmldom/xmldom to run the JS suite: npm install --no-save jsdom');
    }
    globalThis.DOMParser = DOMParser;
  } else {
    const dom = new JSDOM('<!doctype html><html><body></body></html>');
    globalThis.DOMParser = dom.window.DOMParser;
    globalThis.document = dom.window.document;
  }

  globalThis.window = globalThis.window || {};
  globalThis.window.JSZip = MiniZip;
  // files.js loads JSZip by injecting a <script>; short-circuit that by pre-seeding it.
  globalThis.document = globalThis.document || { head: { appendChild() {} }, createElement: () => ({}) };

  const source = readFileSync(join(ROOT, 'static', 'files.js'), 'utf8')
    // The CDN loader is the one thing that cannot run offline. Everything it guards is
    // exercised; only the fetch of the library itself is replaced.
    .replace(/let jszipPromise = null;/, 'let jszipPromise = Promise.resolve(globalThis.window.JSZip);');
  const url = 'data:text/javascript;base64,' + Buffer.from(source).toString('base64');
  files = await import(url);
});

/* ------------------------------------------------------------------------ tests */

function textFile(name, content) {
  return new File([content], name, { type: 'text/plain' });
}

describe('extract', () => {
  test('plain text decodes as utf-8', async () => {
    const { text, sourceFormat } = await files.extract(textFile('notes.md', '# Title\n\nBody — with an em dash.'));
    assert.equal(sourceFormat, 'plain-text');
    assert.match(text, /em dash/);
  });

  test('an unknown extension is refused by name, not guessed', async () => {
    await assert.rejects(() => files.extract(textFile('archive.zip', 'x')), /supported uploads/i);
  });

  test('a file over the limit is refused before it is read', async () => {
    const big = { name: 'big.txt', size: 16 * 1024 * 1024, arrayBuffer: () => { throw new Error('must not read'); } };
    await assert.rejects(() => files.extract(big), /15 MB/);
  });
});

describe('DOCX round trip', () => {
  const SOURCE = 'Methods\n\nWe enrolled 240 children.\nOfficials must report within 30 days.\n\nResults\n\nThe estimate was 0.84 (95% CI 0.65-1.09).';

  test('a paragraph-internal line break survives', async () => {
    // The bug this pins: the reader collected every <w:t> and then every <w:br>
    // separately, so the break lost its position and the two sentences fused into
    // "children.Officials", which changed the sentence count the audit measures.
    const out = await files.exportDocument({ text: SOURCE, filename: 'rt', format: 'docx' });
    const back = await files.extract(new File([out.blob], 'rt.docx'));
    assert.equal(back.sourceFormat, 'docx');
    assert.equal(back.text.trim(), SOURCE.trim());
    assert.match(back.text, /children\.\nOfficials/);
  });

  test('numbers, modals and intervals survive the round trip', async () => {
    const out = await files.exportDocument({ text: SOURCE, filename: 'rt', format: 'docx' });
    const back = await files.extract(new File([out.blob], 'rt.docx'));
    for (const anchor of ['240', '30', '0.84', '95% CI', '0.65-1.09', 'must']) {
      assert.ok(back.text.includes(anchor), `lost ${anchor}`);
    }
  });

  test('tab delimiters survive, because the tabular guard reads them', async () => {
    const table = 'Exposure\tEstimate\tCI\nImproved WASH\t0.39\t0.15';
    const out = await files.exportDocument({ text: table, filename: 't', format: 'docx' });
    const back = await files.extract(new File([out.blob], 't.docx'));
    assert.ok(back.text.includes('\t'), 'tab delimiters were lost');
  });

  test('XML-special characters are escaped, not injected', async () => {
    const nasty = 'A < B & C > D and "quoted" text with <w:p/> inside.';
    const out = await files.exportDocument({ text: nasty, filename: 'x', format: 'docx' });
    const back = await files.extract(new File([out.blob], 'x.docx'));
    assert.equal(back.text.trim(), nasty);
  });

  test('a DOCX with no document part is refused clearly', async () => {
    const zip = new MiniZip();
    zip.file('not-word/thing.xml', '<a/>');
    const blob = await zip.generateAsync();
    await assert.rejects(() => files.extract(new File([blob], 'broken.docx')), /word\/document\.xml/);
  });
});

describe('exportDocument', () => {
  test('filenames are sanitised without becoming empty', async () => {
    for (const [given, expected] of [['My Doc: v2', 'My-Doc-v2'], ['///', 'writeroute-document'], ['', 'writeroute-document']]) {
      const out = await files.exportDocument({ text: 'x', filename: given, format: 'txt' });
      assert.equal(out.name, `${expected}.txt`);
    }
  });

  test('each format carries the right media type', async () => {
    const expected = {
      txt: 'text/plain;charset=utf-8',
      md: 'text/markdown;charset=utf-8',
      html: 'text/html;charset=utf-8',
      docx: 'application/vnd.openxmlformats-officedocument.wordprocessingml.document',
    };
    for (const [fmt, type] of Object.entries(expected)) {
      const out = await files.exportDocument({ text: 'x', filename: 'f', format: fmt });
      assert.equal(out.blob.type, type, fmt);
    }
  });

  test('an unknown format is refused', async () => {
    await assert.rejects(() => files.exportDocument({ text: 'x', filename: 'f', format: 'pdf' }), /txt, md, html or docx/);
  });

  test('html export escapes text when no editor html is supplied', async () => {
    const out = await files.exportDocument({ text: 'a < b & c', filename: 'f', format: 'html' });
    const body = await out.blob.text();
    assert.ok(body.includes('a &lt; b &amp; c'));
    assert.ok(!body.includes('a < b'));
  });
});

describe('multilingual documents', () => {
  const SAMPLES = {
    Chinese: '该队列纳入了240名儿童。\n\n随访持续了十二个月。死亡率为8.7%。',
    Japanese: 'このコホートには240人の子供が含まれた。\n\n追跡は12か月続いた。',
    Arabic: 'شملت المجموعة 240 طفلاً.\n\nاستمرت المتابعة اثني عشر شهراً. ما هي نسبة الوفيات؟',
    Hindi: 'इस समूह में 240 बच्चे शामिल थे।\n\nअनुवर्ती बारह महीने तक चला।',
    Russian: 'В когорту вошли 240 детей.\n\nНаблюдение длилось двенадцать месяцев.',
    Greek: 'Η κοόρτη περιλάμβανε 240 παιδιά.\n\nΗ παρακολούθηση διήρκεσε δώδεκα μήνες.',
    Chichewa: 'Gululi linali ndi ana 240.\n\nKutsatira kunapitilira miyezi khumi ndi iwiri.',
  };

  test('every script survives a DOCX round trip intact', async () => {
    for (const [language, source] of Object.entries(SAMPLES)) {
      const out = await files.exportDocument({ text: source, filename: language, format: 'docx' });
      const back = await files.extract(new File([out.blob], `${language}.docx`));
      assert.equal(back.text.trim(), source.trim(), `${language} was altered by the round trip`);
    }
  });

  test('UTF-8 text files decode without mojibake', async () => {
    for (const [language, source] of Object.entries(SAMPLES)) {
      const { text } = await files.extract(new File([source], `${language}.txt`, { type: 'text/plain' }));
      assert.equal(text, source, `${language} decoded incorrectly`);
      assert.ok(!text.includes('�'), `${language} produced replacement characters`);
    }
  });

  test('a UTF-8 BOM is not left in the text', async () => {
    const { text } = await files.extract(
      new File(['﻿Привет мир'], 'bom.txt', { type: 'text/plain' }));
    assert.ok(!text.startsWith('﻿'), 'the byte-order mark leaked into the document');
  });

  test('a non-Latin filename is still exported safely', async () => {
    const out = await files.exportDocument({ text: 'x', filename: '论文草稿', format: 'txt' });
    assert.match(out.name, /\.txt$/);
    assert.ok(out.name.length > 4, 'sanitising must not empty a non-Latin filename');
  });
});
