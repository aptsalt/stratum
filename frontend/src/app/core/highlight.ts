import { Pipe, PipeTransform } from '@angular/core';

const KEYWORDS = new Set(
  'def async await return yield if elif else for while in not and or is None True False import from as with try except finally raise class lambda break continue pass'.split(' '),
);
const TOKEN = /("""[\s\S]*?"""|'(?:\\.|[^'\\])*'|"(?:\\.|[^"\\])*")|(#[^\n]*)|(@[\w.]+)|\b(\d+(?:\.\d+)?)\b|\b([A-Za-z_]\w*)\b(\s*\()?/g;

const esc = (s: string) => s.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');

/** Tiny Python highlighter for the export view — escapes first, emits only <span class>. */
@Pipe({ name: 'pyHighlight' })
export class PyHighlightPipe implements PipeTransform {
  transform(code: string | null | undefined): string {
    if (!code) return '';
    let out = '';
    let last = 0;
    for (const m of code.matchAll(TOKEN)) {
      out += esc(code.slice(last, m.index));
      last = m.index! + m[0].length;
      const [whole, str, comment, deco, num, ident, call] = m;
      if (str) out += `<span class="t-str">${esc(str)}</span>`;
      else if (comment) out += `<span class="t-com">${esc(comment)}</span>`;
      else if (deco) out += `<span class="t-deco">${esc(deco)}</span>`;
      else if (num) out += `<span class="t-num">${num}</span>`;
      else if (ident && KEYWORDS.has(ident)) out += `<span class="t-kw">${ident}</span>${call ? esc(call) : ''}`;
      else if (ident && /^[A-Z]/.test(ident)) out += `<span class="t-type">${ident}</span>${call ? esc(call) : ''}`;
      else if (ident && call) out += `<span class="t-fn">${ident}</span>${esc(call)}`;
      else out += esc(whole);
    }
    return out + esc(code.slice(last));
  }
}
