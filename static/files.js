/* Client-side document decoding and export.
 *
 * The static build has no server to send a file to, and sending one would be the wrong
 * default anyway: the whole point of the browser build is that a manuscript never
 * leaves the tab. Behaviour deliberately matches app.py's _extract_upload and
 * api_export so the two builds produce the same text from the same file.
 *
 * DOCX is read paragraph by paragraph from the package XML. A lazy regex spanning <w:p>
 * boundaries silently merges paragraphs, which changes every structural statistic the
 * audit then measures — so the XML is parsed, not pattern-matched.
 */
const JSZIP_CDN = 'https://cdn.jsdelivr.net/npm/jszip@3.10.1/dist/jszip.min.js';
const PDFJS_CDN = 'https://cdn.jsdelivr.net/npm/pdfjs-dist@4.6.82/build/pdf.min.mjs';
const PDFJS_WORKER = 'https://cdn.jsdelivr.net/npm/pdfjs-dist@4.6.82/build/pdf.worker.min.mjs';

const MAX_UPLOAD = 15 * 1024 * 1024;
const TEXT_SUFFIXES = new Set(['txt', 'md', 'markdown', 'rst', 'csv', 'log']);

let jszipPromise = null;
let pdfjsPromise = null;

function loadScript(src) {
  return new Promise((resolve, reject) => {
    const el = document.createElement('script');
    el.src = src;
    el.onload = resolve;
    el.onerror = () => reject(new Error(`could not load ${src}`));
    document.head.appendChild(el);
  });
}

async function jszip() {
  if (!jszipPromise) jszipPromise = loadScript(JSZIP_CDN).then(() => window.JSZip);
  return jszipPromise;
}

async function pdfjs() {
  if (!pdfjsPromise) {
    pdfjsPromise = import(PDFJS_CDN).then(mod => {
      mod.GlobalWorkerOptions.workerSrc = PDFJS_WORKER;
      return mod;
    });
  }
  return pdfjsPromise;
}

function decodeText(buffer) {
  // utf-8 first, then the two encodings Word and Excel actually emit.
  for (const enc of ['utf-8', 'windows-1252', 'iso-8859-1']) {
    try {
      return new TextDecoder(enc, { fatal: enc === 'utf-8' }).decode(buffer);
    } catch (_) { /* try the next one */ }
  }
  return new TextDecoder('utf-8').decode(buffer);
}

const W_NS = 'http://schemas.openxmlformats.org/wordprocessingml/2006/main';

function docxBlocks(xml) {
  const doc = new DOMParser().parseFromString(xml, 'application/xml');
  if (doc.querySelector('parsererror')) throw new Error('the DOCX body XML did not parse');
  const body = doc.getElementsByTagNameNS(W_NS, 'body')[0];
  if (!body) throw new Error('the DOCX has no document body');

  const out = [];
  // In document order, not by element type. Collecting every <w:t> and then every
  // <w:br> separately loses the break's position: a paragraph holding two sentences
  // separated by a line break came back with them fused into one, which changed the
  // sentence count and therefore every structural finding the audit produces.
  const paragraphText = p => {
    let s = '';
    const walk = node => {
      for (const child of node.childNodes) {
        if (child.nodeType !== 1) continue;
        const name = child.localName;
        if (name === 't') s += child.textContent || '';
        else if (name === 'br' || name === 'cr') s += '\n';
        else if (name === 'tab') s += '\t';
        else walk(child);
      }
    };
    walk(p);
    return s.trim();
  };

  // Document order matters: a table between two paragraphs must stay between them.
  for (const child of body.children) {
    if (child.localName === 'p') {
      const t = paragraphText(child);
      if (t) out.push(t);
    } else if (child.localName === 'tbl') {
      for (const row of child.getElementsByTagNameNS(W_NS, 'tr')) {
        const cells = [];
        for (const cell of row.getElementsByTagNameNS(W_NS, 'tc')) {
          const parts = [];
          for (const p of cell.getElementsByTagNameNS(W_NS, 'p')) {
            const t = paragraphText(p);
            if (t) parts.push(t);
          }
          cells.push(parts.join(' '));
        }
        // Tab-delimited, as the server does, which is also what the tabular guard reads.
        const line = cells.join('\t');
        if (line.replace(/\t/g, '').trim()) out.push(line);
      }
    }
  }
  return out;
}

export async function extract(file) {
  if (file.size > MAX_UPLOAD) throw new Error('file exceeds the 15 MB limit');
  const suffix = (file.name.split('.').pop() || '').toLowerCase();
  const buffer = await file.arrayBuffer();

  if (TEXT_SUFFIXES.has(suffix)) {
    return { text: decodeText(buffer), sourceFormat: 'plain-text' };
  }

  if (suffix === 'docx') {
    const JSZip = await jszip();
    let zip;
    try {
      zip = await JSZip.loadAsync(buffer);
    } catch (exc) {
      throw new Error(`could not read DOCX: ${exc.message}`);
    }
    const entry = zip.file('word/document.xml');
    if (!entry) throw new Error('could not read DOCX: no word/document.xml');
    const blocks = docxBlocks(await entry.async('string'));
    if (!blocks.length) throw new Error('the DOCX contains no extractable text');
    return { text: blocks.join('\n\n'), sourceFormat: 'docx' };
  }

  if (suffix === 'pdf') {
    const mod = await pdfjs();
    const pdf = await mod.getDocument({ data: new Uint8Array(buffer) }).promise;
    const pages = [];
    for (let n = 1; n <= pdf.numPages; n += 1) {
      const content = await (await pdf.getPage(n)).getTextContent();
      pages.push(content.items.map(i => i.str).join(' ').replace(/\s+/g, ' ').trim());
    }
    const text = pages.filter(Boolean).join('\n\n');
    if (!text.trim()) throw new Error('the PDF has no text layer to extract');
    return { text, sourceFormat: 'pdf' };
  }

  if (suffix === 'rtf') {
    let raw = decodeText(buffer);
    raw = raw.replace(/\\'[0-9a-fA-F]{2}/g, '');
    raw = raw.replace(/\\[a-zA-Z]+-?\d* ?/g, '');
    raw = raw.replace(/[{}]/g, '');
    return { text: raw, sourceFormat: 'rtf' };
  }

  throw new Error('supported uploads: TXT, Markdown, DOCX, PDF with a text layer, RTF, CSV');
}

/* ------------------------------------------------------------------- export */

function xmlEscape(s) {
  return String(s).replace(/[&<>"']/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&apos;' }[c]));
}

const CONTENT_TYPES = `<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">
<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>
<Default Extension="xml" ContentType="application/xml"/>
<Override PartName="/word/document.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/>
</Types>`;

const ROOT_RELS = `<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="word/document.xml"/>
</Relationships>`;

async function docxBlob(text) {
  const JSZip = await jszip();
  const zip = new JSZip();
  const paragraphs = text.split(/\n\s*\n/).map(b => b.trim()).filter(Boolean);
  const body = paragraphs.map(p => {
    // A single newline inside a block becomes a line break, not a new paragraph.
    const runs = p.split('\n').map((line, i) =>
      `${i ? '<w:r><w:br/></w:r>' : ''}<w:r><w:t xml:space="preserve">${xmlEscape(line)}</w:t></w:r>`).join('');
    return `<w:p>${runs}</w:p>`;
  }).join('');
  const document = `<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<w:document xmlns:w="${W_NS}"><w:body>${body}<w:sectPr/></w:body></w:document>`;
  zip.file('[Content_Types].xml', CONTENT_TYPES);
  zip.folder('_rels').file('.rels', ROOT_RELS);
  zip.folder('word').file('document.xml', document);
  return zip.generateAsync({
    type: 'blob',
    mimeType: 'application/vnd.openxmlformats-officedocument.wordprocessingml.document',
  });
}

function htmlEscape(s) {
  return String(s).replace(/&/g, '&amp;').replace(/</g, '&lt;');
}

export async function exportDocument({ text, html, filename, format }) {
  const safe = (filename || 'writeroute-document').replace(/[^A-Za-z0-9._-]+/g, '-').replace(/^[-.]+|[-.]+$/g, '') || 'writeroute-document';
  const fmt = (format || 'txt').toLowerCase();

  if (fmt === 'txt' || fmt === 'md') {
    const type = fmt === 'md' ? 'text/markdown;charset=utf-8' : 'text/plain;charset=utf-8';
    return { blob: new Blob([text], { type }), name: `${safe}.${fmt}` };
  }
  if (fmt === 'html') {
    const content = html || `<p>${htmlEscape(text).replace(/\n/g, '<br>')}</p>`;
    const doc = `<!doctype html><meta charset='utf-8'><title>${xmlEscape(safe)}</title><body>${content}</body>`;
    return { blob: new Blob([doc], { type: 'text/html;charset=utf-8' }), name: `${safe}.html` };
  }
  if (fmt === 'docx') {
    return { blob: await docxBlob(text), name: `${safe}.docx` };
  }
  throw new Error('format must be txt, md, html or docx');
}

export function download({ blob, name }) {
  const url = URL.createObjectURL(blob);
  const a = document.createElement('a');
  a.href = url;
  a.download = name;
  a.click();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}
