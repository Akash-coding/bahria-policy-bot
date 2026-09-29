import { Fragment, ReactNode } from "react";

function renderInline(text: string): ReactNode[] {
  const parts = text.split(/(\*\*[^*]+?\*\*)/g);
  return parts.map((part, index) => {
    if (part.startsWith("**") && part.endsWith("**") && part.length > 4) {
      return <strong key={index}>{part.slice(2, -2)}</strong>;
    }
    return <Fragment key={index}>{part}</Fragment>;
  });
}

type Block =
  | { type: "heading"; level: number; text: string }
  | { type: "olist"; items: string[] }
  | { type: "ulist"; items: string[] }
  | { type: "paragraph"; text: string };

function parseBlocks(markdown: string): Block[] {
  const lines = markdown.replace(/\r\n/g, "\n").split("\n");
  const blocks: Block[] = [];
  let index = 0;

  const pushContinuation = (target: string[]): boolean => {
    if (!target.length || index >= lines.length) return false;
    const line = lines[index];
    if (!line.trim()) return false;
    if (/^\s{2,}\S/.test(line) && !/^\s*(#{1,3}\s|\d+\.\s|[-*]\s)/.test(line)) {
      target[target.length - 1] += ` ${line.trim()}`;
      index += 1;
      return true;
    }
    return false;
  };

  while (index < lines.length) {
    const raw = lines[index];
    const line = raw.trim();
    if (!line) {
      index += 1;
      continue;
    }

    const heading = line.match(/^(#{1,3})\s+(.*)$/);
    if (heading) {
      blocks.push({ type: "heading", level: heading[1].length, text: heading[2].trim() });
      index += 1;
      continue;
    }

    const numbered = line.match(/^\d+\.\s+(.*)$/);
    if (numbered) {
      const items = [numbered[1]];
      index += 1;
      while (index < lines.length) {
        const next = lines[index].trim();
        const match = next.match(/^\d+\.\s+(.*)$/);
        if (match) {
          items.push(match[1]);
          index += 1;
          continue;
        }
        if (pushContinuation(items)) continue;
        break;
      }
      blocks.push({ type: "olist", items });
      continue;
    }

    const bullet = line.match(/^[-*]\s+(.*)$/);
    if (bullet) {
      const items = [bullet[1]];
      index += 1;
      while (index < lines.length) {
        const next = lines[index].trim();
        const match = next.match(/^[-*]\s+(.*)$/);
        if (match) {
          items.push(match[1]);
          index += 1;
          continue;
        }
        if (pushContinuation(items)) continue;
        break;
      }
      blocks.push({ type: "ulist", items });
      continue;
    }

    const paragraph = [line];
    index += 1;
    while (index < lines.length) {
      const next = lines[index].trim();
      if (!next || /^(#{1,3}\s|\d+\.\s|[-*]\s)/.test(next)) break;
      paragraph.push(next);
      index += 1;
    }
    blocks.push({ type: "paragraph", text: paragraph.join(" ") });
  }
  return blocks;
}

export function MarkdownMessage({ text }: { text: string }) {
  const blocks = parseBlocks(text || "");
  return (
    <div className="md">
      {blocks.map((block, index) => {
        if (block.type === "heading") {
          const Tag = block.level === 1 ? "h2" : block.level === 2 ? "h3" : "h4";
          return <Tag key={index}>{renderInline(block.text)}</Tag>;
        }
        if (block.type === "olist") {
          return (
            <ol key={index}>
              {block.items.map((item, itemIndex) => (
                <li key={itemIndex}>{renderInline(item)}</li>
              ))}
            </ol>
          );
        }
        if (block.type === "ulist") {
          return (
            <ul key={index}>
              {block.items.map((item, itemIndex) => (
                <li key={itemIndex}>{renderInline(item)}</li>
              ))}
            </ul>
          );
        }
        return <p key={index}>{renderInline(block.text)}</p>;
      })}
    </div>
  );
}
