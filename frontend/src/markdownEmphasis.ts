// marked follows CommonMark delimiter rules, so Markdown such as
// `**Label:**text` can remain literal when the closing delimiter is followed
// immediately by a non-whitespace character. Repair that specific parser
// output before sanitizing it for display or the rich-text editor.
export const fixMarkedBoldHtml = (html: string): string =>
  html.replace(/\*\*([^*<]+?)\*\*([^<\s])/g, "<strong>$1</strong>$2");

export const normalizeMarkdownEmphasis = (md: string): string => {
  const cleanLine = (line: string, delimiter: string) => {
    const positions: number[] = [];
    let idx = line.indexOf(delimiter);
    while (idx !== -1) {
      positions.push(idx);
      idx = line.indexOf(delimiter, idx + delimiter.length);
    }
    if (positions.length % 2 === 0) return line;
    const leadingWhitespace = line.match(/^\s*/)?.[0] || "";
    const stripped = line.slice(leadingWhitespace.length);
    if (stripped.startsWith(delimiter)) return `${line}${delimiter}`;
    if (line.trimEnd().endsWith(delimiter)) {
      return `${leadingWhitespace}${delimiter}${line.slice(leadingWhitespace.length)}`;
    }
    const removeAt = positions[positions.length - 1];
    return line.slice(0, removeAt) + line.slice(removeAt + delimiter.length);
  };
  return (md || "")
    .split("\n")
    .map((line) => cleanLine(cleanLine(line, "**"), "__"))
    .join("\n");
};
