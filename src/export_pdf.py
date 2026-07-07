"""
PDF export for reviewed comments.

Renders a small HTML report (page number + audio timestamp per comment)
and converts it to PDF via WeasyPrint, which shapes/reorders Arabic text
correctly out of the box -- unlike reportlab, which needs manual
reshaping (arabic_reshaper) and bidi reordering (python-bidi) to render
Arabic legibly at all.
"""

import html

from weasyprint import HTML

CLUB_NAME_AR = "نادي القراء العرب"

# Sessions are numbered 1-10 in practice; anything beyond that falls back to
# a plain "المجلس رقم N" rather than guessing compound Arabic ordinal grammar.
ORDINALS_AR = {
    1: "الأول", 2: "الثاني", 3: "الثالث", 4: "الرابع", 5: "الخامس",
    6: "السادس", 7: "السابع", 8: "الثامن", 9: "التاسع", 10: "العاشر",
}


def format_ts(seconds: float) -> str:
    m, s = divmod(int(seconds), 60)
    return f"{m}:{s:02d}"


def session_label_ar(session_number: int | None) -> str:
    if session_number is None:
        return ""
    ordinal = ORDINALS_AR.get(session_number)
    return f"المجلس {ordinal}" if ordinal else f"المجلس رقم {session_number}"


def build_pdf(
    comments: list[dict],
    book_title_ar: str = "",
    author_ar: str = "",
    session_number: int | None = None,
) -> bytes:
    """
    comments: list of {text, page, start, end}, optionally with
    position_in_page/page_word_count (word offset within the page) and
    context_before (book text read right before the comment) -- both
    rendered when present, silently omitted otherwise. (n_words ignored
    if present.)
    Returns PDF file bytes. Report is Arabic-only, RTL throughout.
    """

    def _entry_html(c: dict) -> str:
        page_meta = f'الصفحة {c["page"]}'
        if c.get("position_in_page") and c.get("page_word_count"):
            page_meta += f' (الكلمة {c["position_in_page"]} من {c["page_word_count"]})'
        context_html = ""
        if c.get("context_before"):
            context_html = f'<p class="context">…{html.escape(c["context_before"])}</p>'
        return f"""
        <div class="entry">
          <div class="meta">{page_meta} &middot; {format_ts(c["start"])}–{format_ts(c["end"])}</div>
          {context_html}
          <p class="text">{html.escape(c["text"])}</p>
        </div>
        """

    rows = "\n".join(_entry_html(c) for c in comments)

    meta_lines = []
    if book_title_ar:
        meta_lines.append(f'<div class="meta-line">الكتاب: {html.escape(book_title_ar)}</div>')
    if author_ar:
        meta_lines.append(f'<div class="meta-line">المؤلف: {html.escape(author_ar)}</div>')
    session_label = session_label_ar(session_number)
    if session_label:
        meta_lines.append(f'<div class="meta-line">{session_label}</div>')
    meta_block = "\n".join(meta_lines)

    html_doc = f"""
    <html dir="rtl" lang="ar">
    <head>
    <meta charset="utf-8">
    <style>
      @page {{ size: A4; margin: 2.2cm; }}
      body {{
        font-family: "Noto Naskh Arabic", "Traditional Arabic", serif;
        direction: rtl; text-align: right; color: #1c231f;
      }}
      h1 {{ font-size: 20pt; margin: 0 0 10pt; }}
      .meta-line {{ font-size: 12pt; color: #444; margin-bottom: 2pt; }}
      .subtitle {{ font-size: 10pt; color: #666; margin: 10pt 0 24pt; }}
      .entry {{ margin-bottom: 18pt; padding-bottom: 14pt; border-bottom: 0.5pt solid #ccc; }}
      .meta {{ font-size: 9pt; color: #888; margin-bottom: 4pt; }}
      .context {{ font-size: 10pt; color: #999; font-style: italic; margin: 0 0 4pt; }}
      .text {{ font-size: 14pt; line-height: 1.9; margin: 0; }}
    </style>
    </head>
    <body>
      <h1>{CLUB_NAME_AR}</h1>
      {meta_block}
      <div class="subtitle">عدد التعليقات: {len(comments)}</div>
      {rows}
    </body>
    </html>
    """
    return HTML(string=html_doc).write_pdf()
