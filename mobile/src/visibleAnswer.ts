const THINK_CLOSE = /<\/think>/i;
const THINK_OPEN = /<think>/i;
const THINK_HINT =
  /i'?ll say something like|the safest approach|in roman urdu style|perfect\.?\s+i'?ll respond|no extra words|let me think|chain of thought/i;

export function stripThinking(text: string): string {
  let cleaned = text || "";
  const close = cleaned.search(THINK_CLOSE);
  if (close >= 0) {
    cleaned = cleaned.slice(close).replace(THINK_CLOSE, "");
  } else if (THINK_OPEN.test(cleaned) || THINK_HINT.test(cleaned)) {
    return "";
  }
  cleaned = cleaned.replace(/<\/?think>/gi, "").replace(/<\/?unused\d+>/gi, "");
  return cleaned.trim();
}
