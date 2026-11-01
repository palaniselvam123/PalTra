"use client";

import type { ReactNode } from "react";

/**
 * A small Markdown renderer for the assistant's replies: paragraphs, `-` / `*` / numbered lists, `###` headings,
 * fenced code, pipe tables and the inline marks **bold**, *italic* and `code`. Plain text in, React out; no HTML
 * from the model is ever rendered, so a reply cannot inject markup into the page.
 */
export function ChatMarkdown({ text }: { text: string }) {
  return <div className="chat-md space-y-2">{render(text)}</div>;
}

function inline(text: string, key = 0): ReactNode[] {
  const out: ReactNode[] = [];
  // Code first (so marks inside code are left alone), then bold, then italic.
  const re = /(`[^`]+`|\*\*[^*]+\*\*|\*[^*\s][^*]*\*)/g;
  let last = 0;
  let m: RegExpExecArray | null;
  let i = 0;
  while ((m = re.exec(text)) !== null) {
    if (m.index > last) out.push(text.slice(last, m.index));
    const tok = m[0];
    if (tok.startsWith("`")) {
      out.push(
        <code key={`${key}-${i}`} className="rounded bg-white/10 px-1 py-px font-mono text-[11px] text-slate-100">
          {tok.slice(1, -1)}
        </code>
      );
    } else if (tok.startsWith("**")) {
      out.push(
        <strong key={`${key}-${i}`} className="font-semibold text-slate-100">
          {tok.slice(2, -2)}
        </strong>
      );
    } else {
      out.push(<em key={`${key}-${i}`}>{tok.slice(1, -1)}</em>);
    }
    last = m.index + tok.length;
    i += 1;
  }
  if (last < text.length) out.push(text.slice(last));
  return out;
}

function isTableRow(line: string): boolean {
  return /^\s*\|.*\|\s*$/.test(line);
}

function isSeparatorRow(line: string): boolean {
  return /^\s*\|?\s*:?-{2,}:?\s*(\|\s*:?-{2,}:?\s*)*\|?\s*$/.test(line);
}

function cells(line: string): string[] {
  return line
    .trim()
    .replace(/^\|/, "")
    .replace(/\|$/, "")
    .split("|")
    .map((c) => c.trim());
}

function render(text: string): ReactNode[] {
  const lines = text.replace(/\r\n/g, "\n").split("\n");
  const out: ReactNode[] = [];
  let i = 0;
  let key = 0;
  const para: string[] = [];
  const flush = () => {
    if (!para.length) return;
    out.push(
      <p key={`p${key++}`} className="leading-relaxed">
        {inline(para.join(" "), key)}
      </p>
    );
    para.length = 0;
  };
  while (i < lines.length) {
    const line = lines[i];
    if (line.trim().startsWith("```")) {
      flush();
      const code: string[] = [];
      i += 1;
      while (i < lines.length && !lines[i].trim().startsWith("```")) {
        code.push(lines[i]);
        i += 1;
      }
      i += 1;
      out.push(
        <pre key={`c${key++}`} className="overflow-x-auto rounded-md bg-black/40 p-2 font-mono text-[11px] text-slate-200">
          {code.join("\n")}
        </pre>
      );
      continue;
    }
    const heading = /^\s*(#{1,6})\s+(.*)$/.exec(line);
    if (heading) {
      flush();
      out.push(
        <p key={`h${key++}`} className="pt-1 text-[11px] font-semibold uppercase tracking-wider text-slate-400">
          {inline(heading[2], key)}
        </p>
      );
      i += 1;
      continue;
    }
    if (isTableRow(line) && i + 1 < lines.length && isSeparatorRow(lines[i + 1])) {
      flush();
      const head = cells(line);
      i += 2;
      const rows: string[][] = [];
      while (i < lines.length && isTableRow(lines[i])) {
        rows.push(cells(lines[i]));
        i += 1;
      }
      out.push(
        <div key={`t${key++}`} className="overflow-x-auto">
          <table className="w-full text-left text-[11px]">
            <thead>
              <tr>
                {head.map((h, j) => (
                  <th key={j} className="border-b border-white/10 py-1 pr-2 font-semibold text-slate-300">
                    {inline(h, key)}
                  </th>
                ))}
              </tr>
            </thead>
            <tbody>
              {rows.map((r, ri) => (
                <tr key={ri} className="border-b border-white/5">
                  {r.map((c, ci) => (
                    <td key={ci} className="py-1 pr-2 align-top text-slate-300">
                      {inline(c, key)}
                    </td>
                  ))}
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      );
      continue;
    }
    const bullet = /^\s*[-*•]\s+(.*)$/.exec(line);
    const numbered = /^\s*(\d+)[.)]\s+(.*)$/.exec(line);
    if (bullet || numbered) {
      flush();
      const ordered = Boolean(numbered);
      const items: string[] = [];
      while (i < lines.length) {
        const b = /^\s*[-*•]\s+(.*)$/.exec(lines[i]);
        const n = /^\s*(\d+)[.)]\s+(.*)$/.exec(lines[i]);
        const hit = ordered ? n : b;
        if (hit) {
          items.push(ordered ? n![2] : b![1]);
          i += 1;
        } else if (lines[i].trim() && /^\s{2,}/.test(lines[i]) && items.length) {
          // A wrapped continuation line of the item before it.
          items[items.length - 1] += ` ${lines[i].trim()}`;
          i += 1;
        } else {
          break;
        }
      }
      const List = ordered ? "ol" : "ul";
      out.push(
        <List key={`l${key++}`} className={ordered ? "list-decimal space-y-1 pl-4" : "list-disc space-y-1 pl-4"}>
          {items.map((it, j) => (
            <li key={j} className="leading-relaxed">
              {inline(it, key * 100 + j)}
            </li>
          ))}
        </List>
      );
      continue;
    }
    if (!line.trim()) {
      flush();
      i += 1;
      continue;
    }
    para.push(line.trim());
    i += 1;
  }
  flush();
  return out;
}
