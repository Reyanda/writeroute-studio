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

const DOCX_MIME = 'application/vnd.openxmlformats-officedocument.wordprocessingml.document';
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

function docxToRich(xml, commentsXml = '') {
  const doc = new DOMParser().parseFromString(xml, 'application/xml');
  if (doc.querySelector('parsererror')) throw new Error('the DOCX body XML did not parse');
  const body = doc.getElementsByTagNameNS(W_NS, 'body')[0];
  if (!body) throw new Error('the DOCX has no document body');

  const htmlBlocks = [];
  const textBlocks = [];

  const parseRunHtml = r => {
    const rPr = r.getElementsByTagNameNS(W_NS, 'rPr')[0];
    const isBold = rPr && rPr.getElementsByTagNameNS(W_NS, 'b').length > 0;
    const isItalic = rPr && rPr.getElementsByTagNameNS(W_NS, 'i').length > 0;
    const isStrike = rPr && rPr.getElementsByTagNameNS(W_NS, 'strike').length > 0;
    const vertAlign = rPr && rPr.getElementsByTagNameNS(W_NS, 'vertAlign')[0]?.getAttributeNS(W_NS, 'val');
    const isSub = vertAlign === 'subscript';
    const isSup = vertAlign === 'superscript';

    const tNodes = Array.from(r.getElementsByTagNameNS(W_NS, 't'));
    const str = tNodes.map(t => t.textContent || '').join('');
    if (!str) {
      if (r.getElementsByTagNameNS(W_NS, 'br').length > 0 || r.getElementsByTagNameNS(W_NS, 'cr').length > 0) return '<br>';
      return '';
    }
    let res = str.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');
    if (isBold) res = `<strong>${res}</strong>`;
    if (isItalic) res = `<em>${res}</em>`;
    if (isStrike) res = `<s>${res}</s>`;
    if (isSub) res = `<sub>${res}</sub>`;
    if (isSup) res = `<sup>${res}</sup>`;
    return res;
  };

  const parseParagraphHtml = p => {
    let html = '';
    for (const child of p.childNodes) {
      if (child.nodeType !== 1) continue;
      const name = child.localName;
      if (name === 'r') {
        html += parseRunHtml(child);
      } else if (name === 'sdt') {
        const sdtText = Array.from(child.getElementsByTagNameNS(W_NS, 't')).map(t => t.textContent || '').join('');
        const tag = child.getElementsByTagNameNS(W_NS, 'tag')[0]?.getAttributeNS(W_NS, 'val') || '';
        if (tag.includes('MENDELEY')) {
          html += `<span class="citation-tag" data-sdt="mendeley">${sdtText || '[Citation]'}</span>`;
        } else {
          html += `<span class="sdt-field">${sdtText}</span>`;
        }
      } else if (name === 'hyperlink') {
        const rNodes = Array.from(child.getElementsByTagNameNS(W_NS, 'r'));
        html += `<a href="#">${rNodes.map(parseRunHtml).join('')}</a>`;
      }
    }
    return html;
  };

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

  for (const child of body.children) {
    if (child.localName === 'p') {
      const pPr = child.getElementsByTagNameNS(W_NS, 'pPr')[0];
      const pStyle = pPr?.getElementsByTagNameNS(W_NS, 'pStyle')[0]?.getAttributeNS(W_NS, 'val') || '';
      const style = pStyle.toLowerCase();
      const pHtml = parseParagraphHtml(child);
      const pTxt = paragraphText(child);

      if (!pTxt && !pHtml) continue;
      textBlocks.push(pTxt);

      if (style.includes('heading1') || style.includes('title')) {
        htmlBlocks.push(`<h1>${pHtml}</h1>`);
      } else if (style.includes('heading2')) {
        htmlBlocks.push(`<h2>${pHtml}</h2>`);
      } else if (style.includes('heading3')) {
        htmlBlocks.push(`<h3>${pHtml}</h3>`);
      } else if (style.includes('heading4')) {
        htmlBlocks.push(`<h4>${pHtml}</h4>`);
      } else if (style.includes('quote') || style.includes('blockquote')) {
        htmlBlocks.push(`<blockquote>${pHtml}</blockquote>`);
      } else if (style.includes('list') || pPr?.getElementsByTagNameNS(W_NS, 'numPr').length) {
        htmlBlocks.push(`<li>${pHtml}</li>`);
      } else {
        htmlBlocks.push(`<p>${pHtml}</p>`);
      }
    } else if (child.localName === 'tbl') {
      let tblHtml = '<table class="scientific-table"><tbody>';
      const tblTxt = [];
      const rows = Array.from(child.getElementsByTagNameNS(W_NS, 'tr'));
      rows.forEach((row, rIdx) => {
        tblHtml += '<tr>';
        const rowTxt = [];
        const cells = Array.from(row.getElementsByTagNameNS(W_NS, 'tc'));
        cells.forEach(cell => {
          const ps = Array.from(cell.getElementsByTagNameNS(W_NS, 'p'));
          const cellHtml = ps.map(parseParagraphHtml).join(' ');
          const cellText = ps.map(paragraphText).join(' ');
          rowTxt.push(cellText);
          if (rIdx === 0) tblHtml += `<th>${cellHtml}</th>`;
          else tblHtml += `<td>${cellHtml}</td>`;
        });
        tblHtml += '</tr>';
        tblTxt.push(rowTxt.join('\t'));
      });
      tblHtml += '</tbody></table>';
      htmlBlocks.push(tblHtml);
      textBlocks.push(tblTxt.join('\n'));
    }
  }

  return { html: htmlBlocks.join('\n'), text: textBlocks.join('\n\n') };
}

function markdownToRichHtml(md) {
  const lines = md.split('\n');
  const html = [];
  let inCode = false;
  let codeBuffer = [];

  for (let line of lines) {
    const trimmed = line.trim();
    if (trimmed.startsWith('```')) {
      if (inCode) {
        html.push(`<pre><code>${codeBuffer.join('\n')}</code></pre>`);
        codeBuffer = [];
        inCode = false;
      } else {
        inCode = true;
      }
      continue;
    }
    if (inCode) {
      codeBuffer.push(line.replace(/&/g, '&amp;').replace(/</g, '&lt;'));
      continue;
    }

    if (trimmed.startsWith('#### ')) {
      html.push(`<h4>${trimmed.slice(5)}</h4>`);
    } else if (trimmed.startsWith('### ')) {
      html.push(`<h3>${trimmed.slice(4)}</h3>`);
    } else if (trimmed.startsWith('## ')) {
      html.push(`<h2>${trimmed.slice(3)}</h2>`);
    } else if (trimmed.startsWith('# ')) {
      html.push(`<h1>${trimmed.slice(2)}</h1>`);
    } else if (trimmed.startsWith('> ')) {
      html.push(`<blockquote>${trimmed.slice(2)}</blockquote>`);
    } else if (trimmed.startsWith('- ') || trimmed.startsWith('* ')) {
      html.push(`<li>${trimmed.slice(2)}</li>`);
    } else if (trimmed.startsWith('$$') && trimmed.endsWith('$$') && trimmed.length > 4) {
      const eq = trimmed.slice(2, -2).trim();
      html.push(`<div class="math-equation" data-tex="${eq}">$$ ${eq} $$</div>`);
    } else if (trimmed) {
      let p = line.replace(/&/g, '&amp;').replace(/</g, '&lt;');
      p = p.replace(/\*\*(.*?)\*\*/g, '<strong>$1</strong>');
      p = p.replace(/\*(.*?)\*/g, '<em>$1</em>');
      p = p.replace(/`([^`]+)`/g, '<code>$1</code>');
      p = p.replace(/\(([A-Z][a-z]+(?:\s+et\s+al\.)?,?\s*\d{4})\)/g, '<span class="citation-tag">$1</span>');
      html.push(`<p>${p}</p>`);
    }
  }
  return html.join('\n');
}

export async function extract(file) {
  if (file.size > MAX_UPLOAD) throw new Error('file exceeds the 15 MB limit');
  const suffix = (file.name.split('.').pop() || '').toLowerCase();
  const buffer = await file.arrayBuffer();

  if (TEXT_SUFFIXES.has(suffix)) {
    const raw = decodeText(buffer);
    const html = suffix === 'md' || suffix === 'markdown' ? markdownToRichHtml(raw) : `<p>${raw.replace(/\n\n+/g, '</p><p>').replace(/\n/g, '<br>')}</p>`;
    return { text: raw, html: html, sourceFormat: 'plain-text' };
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
    const docXml = await entry.async('string');
    const commentsEntry = zip.file('word/comments.xml');
    const commentsXml = commentsEntry ? await commentsEntry.async('string') : '';
    const res = docxToRich(docXml, commentsXml);
    if (!res.text.length && !res.html.length) throw new Error('the DOCX contains no extractable text');
    return { text: res.text, html: res.html, sourceFormat: 'docx' };
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
    const html = pages.filter(Boolean).map(p => `<p>${p}</p>`).join('\n');
    return { text, html, sourceFormat: 'pdf' };
  }

  if (suffix === 'rtf') {
    let raw = decodeText(buffer);
    raw = raw.replace(/\\'[0-9a-fA-F]{2}/g, '');
    raw = raw.replace(/\\[a-zA-Z]+-?\d* ?/g, '');
    raw = raw.replace(/[{}]/g, '');
    return { text: raw, html: `<p>${raw.replace(/\n\n+/g, '</p><p>')}</p>`, sourceFormat: 'rtf' };
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
  const packed = await zip.generateAsync({ type: 'blob' });
  // Set the media type here rather than relying on JSZip's mimeType option. A test
  // caught a build where the blob came back as application/zip, which is the wrong type
  // for a download and depends on a library detail this module should not lean on.
  return new Blob([packed], { type: DOCX_MIME });
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
  if (fmt === 'tex' || fmt === 'latex') {
    // Generate basic clean LaTeX wrapper
    const escaped = text.replace(/\\/g, '\\textbackslash{}').replace(/&/g, '\\&').replace(/%/g, '\\%').replace(/\$/g, '\\$').replace(/#/g, '\\#').replace(/_/g, '\\_');
    const doc = `\\documentclass[11pt,a4paper]{article}\n\\usepackage[utf8]{inputenc}\n\\usepackage[margin=1in]{geometry}\n\\usepackage{amsmath,amssymb}\n\\title{${safe}}\n\\author{WriteRoute Author}\n\\date{\\today}\n\\begin{document}\n\\maketitle\n\n${escaped}\n\n\\end{document}`;
    return { blob: new Blob([doc], { type: 'text/x-tex;charset=utf-8' }), name: `${safe}.tex` };
  }
  if (fmt === 'html') {
    const content = html || `<p>${htmlEscape(text).replace(/\n/g, '<br>')}</p>`;
    const doc = `<!doctype html><meta charset='utf-8'><title>${xmlEscape(safe)}</title><body>${content}</body>`;
    return { blob: new Blob([doc], { type: 'text/html;charset=utf-8' }), name: `${safe}.html` };
  }
  if (fmt === 'docx') {
    return { blob: await docxBlob(text), name: `${safe}.docx` };
  }
  throw new Error('format must be txt, md, html, docx, or tex');
}

export function download({ blob, name }) {
  const url = URL.createObjectURL(blob);
  const a = document.createElement('a');
  a.href = url;
  a.download = name;
  a.click();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}
