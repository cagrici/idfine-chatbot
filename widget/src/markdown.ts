/**
 * Lightweight markdown renderer for chat messages.
 * Supports: headings (##, ###, ####), bold, italic, bold+italic,
 * unordered lists (- or *), ordered lists, paragraphs, line breaks.
 */
export function renderMarkdown(text: string): string {
  if (!text) return "";

  let html = text
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;");

  // Headings
  html = html.replace(/^#### (.+)$/gm, "<h4>$1</h4>");
  html = html.replace(/^### (.+)$/gm, "<h4>$1</h4>");
  html = html.replace(/^## (.+)$/gm, "<h3>$1</h3>");

  // Bold + italic combined, then bold, then italic
  html = html.replace(/\*\*\*(.+?)\*\*\*/g, "<strong><em>$1</em></strong>");
  html = html.replace(/\*\*(.+?)\*\*/g, "<strong>$1</strong>");
  html = html.replace(/\*(.+?)\*/g, "<em>$1</em>");

  // Unordered lists
  html = html.replace(/^[-*] (.+)$/gm, "<li>$1</li>");
  html = html.replace(/((?:<li>.*<\/li>\n?)+)/g, "<ul>$1</ul>");

  // Ordered lists
  html = html.replace(/^\d+\.\s+(.+)$/gm, "<oli>$1</oli>");
  html = html.replace(/((?:<oli>.*<\/oli>\n?)+)/g, (m: string) =>
    "<ol>" + m.replace(/<\/?oli>/g, (t: string) => t.replace("oli", "li")) + "</ol>"
  );

  // Paragraphs
  html = html.replace(/\n{2,}/g, "</p><p>");
  html = html.replace(/\n/g, "<br>");
  html = "<p>" + html + "</p>";

  // Clean empty paragraphs around block elements
  html = html.replace(/<p>\s*(<(?:ul|ol|h[34]))/g, "$1");
  html = html.replace(/(<\/(?:ul|ol|h[34])>)\s*<\/p>/g, "$1");
  html = html.replace(/<p>\s*<\/p>/g, "");

  // Remove <br> artifacts inside lists and around block elements
  html = html.replace(/<\/li>\s*<br>\s*/g, "</li>");
  html = html.replace(/<ul>\s*<br>/g, "<ul>");
  html = html.replace(/<ol>\s*<br>/g, "<ol>");
  html = html.replace(/<br>\s*<\/ul>/g, "</ul>");
  html = html.replace(/<br>\s*<\/ol>/g, "</ol>");
  html = html.replace(/<\/h[34]>\s*<br>/g, (m: string) => m.replace(/<br>/, ""));
  html = html.replace(/<\/ul>\s*<br>/g, "</ul>");
  html = html.replace(/<\/ol>\s*<br>/g, "</ol>");

  return html;
}
