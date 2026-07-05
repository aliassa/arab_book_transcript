"""
PDF export for reviewed comments.

Renders a small HTML report (page number + audio timestamp per comment)
and converts it to PDF via WeasyPrint, which shapes/reorders Arabic text
correctly out of the box -- unlike reportlab, which needs manual
reshaping (arabic_reshaper) and bidi reordering (python-bidi) to render
Arabic legibly at all.
"""

from weasyprint import HTML


def format_ts(seconds: float) -> str:
    m, s = divmod(int(seconds), 60)
    return f"{m}:{s:02d}"


def build_pdf(comments: list[dict], book_title: str = "") -> bytes:
    """
    comments: list of {text, page, start, end} (n_words ignored if present).
    Returns PDF file bytes.
    """
    rows = "\n".join(
        f"""
        <div class="entry">
          <div class="meta">page {c["page"]} &middot; {format_ts(c["start"])}–{format_ts(c["end"])}</div>
          <p class="text">{c["text"]}</p>
        </div>
        """
        for c in comments
    )
    book_line = f'<div class="book-title">{book_title}</div>' if book_title else ""

    html = f"""
    <html>
    <head>
    <meta charset="utf-8">
    <style>
      @page {{ size: A4; margin: 2.2cm; }}
      body {{ font-family: sans-serif; color: #1c231f; }}
      h1 {{ font-size: 18pt; margin: 0 0 4pt; }}
      .book-title {{
        font-family: "Noto Naskh Arabic", "Traditional Arabic", serif;
        direction: rtl; text-align: right; font-size: 12pt; color: #444; margin-bottom: 2pt;
      }}
      .subtitle {{ font-size: 10pt; color: #666; margin-bottom: 24pt; }}
      .entry {{ margin-bottom: 18pt; padding-bottom: 14pt; border-bottom: 0.5pt solid #ccc; }}
      .meta {{
        font-size: 9pt; color: #888; margin-bottom: 4pt;
        direction: ltr; text-align: left; font-variant-numeric: tabular-nums;
      }}
      .text {{
        font-family: "Noto Naskh Arabic", "Traditional Arabic", serif;
        direction: rtl; text-align: right; font-size: 14pt; line-height: 1.9; margin: 0;
      }}
    </style>
    </head>
    <body>
      <h1>Extracted Comments</h1>
      {book_line}
      <div class="subtitle">{len(comments)} comment(s)</div>
      {rows}
    </body>
    </html>
    """
    return HTML(string=html).write_pdf()
