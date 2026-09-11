import React from "react";

// Minimal, dependency-free Markdown renderer sufficient for model answers:
// fenced/inline code, headings, bold/italic, lists, blockquotes, simple tables.
// All text is escaped before any formatting to prevent injection.

function escapeHtml(s: string): string {
  return s.replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");
}

function inline(text: string): React.ReactNode[] {
  const nodes: React.ReactNode[] = [];
  const re = /(\*\*[^*]+\*\*|`[^`]+`|\*[^*\n]+\*)/g;
  let last = 0;
  let m: RegExpExecArray | null;
  let key = 0;
  while ((m = re.exec(text))) {
    if (m.index > last) nodes.push(text.slice(last, m.index));
    const tok = m[0];
    if (tok.startsWith("**")) nodes.push(<strong key={key++}>{tok.slice(2, -2)}</strong>);
    else if (tok.startsWith("`")) nodes.push(<code key={key++}>{tok.slice(1, -1)}</code>);
    else nodes.push(<em key={key++}>{tok.slice(1, -1)}</em>);
    last = m.index + tok.length;
  }
  if (last < text.length) nodes.push(text.slice(last));
  return nodes;
}

function renderTable(rows: string[], startKey: number): React.ReactNode {
  const cells = (r: string) => r.trim().replace(/^\||\|$/g, "").split("|").map((c) => c.trim());
  const header = cells(rows[0]);
  const body = rows.slice(2).map(cells);
  return (
    <table key={startKey}>
      <thead><tr>{header.map((h, i) => <th key={i}>{inline(h)}</th>)}</tr></thead>
      <tbody>{body.map((r, i) => <tr key={i}>{r.map((c, j) => <td key={j}>{inline(c)}</td>)}</tr>)}</tbody>
    </table>
  );
}

export function Markdown({ text }: { text: string }) {
  const parts = text.split(/```(\w*)\n?/);
  // parts: [plain, lang, code, plain, lang, code, ...]
  const blocks: React.ReactNode[] = [];
  let key = 0;
  for (let i = 0; i < parts.length; i += 3) {
    const plain = parts[i];
    const lang = parts[i + 1];
    const code = parts[i + 2];
    if (plain) {
      const lines = plain.split("\n");
      let list: React.ReactNode[] = [];
      const flush = () => {
        if (list.length) { blocks.push(<ul key={key++}>{list}</ul>); list = []; }
      };
      let table: string[] = [];
      const flushTable = () => {
        if (table.length >= 2) blocks.push(renderTable(table, key++));
        table = [];
      };
      for (const rawLine of lines) {
        const line = rawLine;
        if (line.trim().startsWith("|") && line.includes("|", 1)) {
          flush();
          table.push(line);
          continue;
        } else flushTable();
        if (/^\s*[-*]\s+/.test(line)) {
          list.push(<li key={key++}>{inline(line.replace(/^\s*[-*]\s+/, ""))}</li>);
        } else if (/^\s*\d+\.\s+/.test(line)) {
          flush();
          blocks.push(<div key={key++} className="small" style={{ paddingLeft: 8 }}>{inline(line)}</div>);
        } else if (/^#{1,4}\s/.test(line)) {
          flush();
          const level = line.match(/^#+/)![0].length;
          const content = line.replace(/^#+\s+/, "");
          const Tag = (`h${Math.min(4, level)}`) as any;
          blocks.push(<Tag key={key++}>{inline(content)}</Tag>);
        } else if (/^>\s?/.test(line)) {
          flush();
          blocks.push(<blockquote key={key++} style={{ margin: "6px 0", color: "var(--text-dim)",
            borderLeft: "3px solid var(--border)", paddingLeft: 10 }}>{inline(line.replace(/^>\s?/, ""))}</blockquote>);
        } else if (line.trim() === "") {
          flush();
        } else {
          flush();
          blocks.push(<p key={key++} style={{ margin: "6px 0" }}>{inline(line)}</p>);
        }
      }
      flush();
      flushTable();
    }
    if (code !== undefined) {
      blocks.push(
        <pre key={key++}>
          <code>{code.replace(/\n$/, "")}</code>
          {lang && <div className="muted small" style={{ marginTop: 4 }}>{lang}</div>}
        </pre>
      );
    }
  }
  return <div>{blocks}</div>;
}
