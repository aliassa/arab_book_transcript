"""
PDF export for reviewed comments.

Renders a small HTML report (page number + audio timestamp per comment)
and converts it to PDF via WeasyPrint, which shapes/reorders Arabic text
correctly out of the box -- unlike reportlab, which needs manual
reshaping (arabic_reshaper) and bidi reordering (python-bidi) to render
Arabic legibly at all.
"""

import base64
import html

from weasyprint import HTML

CLUB_NAME_AR = "نادي القراء العرب"
CLUB_TELEGRAM_URL = "https://t.me/NadiAlQuraaAlArab"
CLUB_FACEBOOK_URL = "https://www.facebook.com/groups/1752014606211247/"
TELEGRAM_LABEL_AR = "رابط تيليغرام"
FACEBOOK_LABEL_AR = "رابط الفايسبوك"
COMMENTATOR_LABEL_AR = "علّق عليه"

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


def _display_url(url: str) -> str:
    """Short print-friendly form of a URL (no scheme/www/trailing slash) --
    what a person would type after reading it off a printed cover."""
    url = url.removeprefix("https://").removeprefix("http://")
    return url.removeprefix("www.").rstrip("/")


def _social_icon_link(url: str, icon_bytes: bytes, label_ar: str) -> str:
    encoded = base64.b64encode(icon_bytes).decode("ascii")
    return (
        f'<a class="cover-social-link" href="{html.escape(url)}">'
        f'<img class="cover-social-icon" src="data:image/png;base64,{encoded}" alt="">'
        f'<span class="cover-social-label">{html.escape(label_ar)}</span>'
        f"</a>"
    )


def cover_page_html(
    book_title_ar: str = "",
    author_ar: str = "",
    commentator_ar: str = "",
    club_image_bytes: bytes | None = None,
    telegram_icon_bytes: bytes | None = None,
    facebook_icon_bytes: bytes | None = None,
) -> str:
    """
    Club-branded front-matter fragment (logo, book/commentator metadata,
    Telegram/Facebook links) shared as the very first page of both
    build_pdf's report and export_annotated_pdf.build_annotated_pdf's book
    copy -- same content either way, each caller just embeds it into a
    differently-sized/styled page (a full A4 report page vs. a page sized
    to match the book's own PDF). No session/majlis number here: this page
    fronts the whole book's worth of comments, not one session's, so a
    single session number wouldn't describe it. The Telegram/Facebook links
    are icon + a short Arabic label ("رابط تيليغرام"/"رابط الفايسبوك");
    the full URL lives in the `<a href>` for anyone reading digitally, and
    for *printed* copies a small light-gray footer line carries the tidied
    short forms (`t.me/...` · `facebook.com/groups/...`) -- deliberately a
    quiet footer, not text next to the icons, so the numeric Facebook
    group URL doesn't sit ugly in the middle of the cover (chosen over QR
    codes / a separate closing page when the options were offered).
    """
    image_html = ""
    if club_image_bytes:
        encoded = base64.b64encode(club_image_bytes).decode("ascii")
        image_html = f'<img class="cover-image" src="data:image/jpeg;base64,{encoded}" alt="">'

    meta_lines = []
    if book_title_ar:
        meta_lines.append(f'<div class="cover-meta-line">الكتاب: {html.escape(book_title_ar)}</div>')
    if author_ar:
        meta_lines.append(f'<div class="cover-meta-line">المؤلف: {html.escape(author_ar)}</div>')
    if commentator_ar:
        meta_lines.append(
            f'<div class="cover-meta-line">{COMMENTATOR_LABEL_AR}: {html.escape(commentator_ar)}</div>'
        )
    meta_html = "\n".join(meta_lines)

    social_links = []
    if telegram_icon_bytes:
        social_links.append(_social_icon_link(CLUB_TELEGRAM_URL, telegram_icon_bytes, TELEGRAM_LABEL_AR))
    if facebook_icon_bytes:
        social_links.append(_social_icon_link(CLUB_FACEBOOK_URL, facebook_icon_bytes, FACEBOOK_LABEL_AR))
    social_html = f'<div class="cover-social">{"".join(social_links)}</div>' if social_links else ""

    footer_html = ""
    if social_links:
        footer_html = (
            '<div class="cover-footer">'
            f'<a class="cover-footer-link" href="{html.escape(CLUB_TELEGRAM_URL)}">'
            f"{html.escape(_display_url(CLUB_TELEGRAM_URL))}</a>"
            " &middot; "
            f'<a class="cover-footer-link" href="{html.escape(CLUB_FACEBOOK_URL)}">'
            f"{html.escape(_display_url(CLUB_FACEBOOK_URL))}</a>"
            "</div>"
        )

    return f"""
    <div class="cover-page">
      <h1 class="cover-title">{CLUB_NAME_AR}</h1>
      <div class="cover-title-rule"></div>
      {image_html}
      <div class="cover-meta">{meta_html}</div>
      {social_html}
      {footer_html}
    </div>
    """


# Sampled from assets/club_image.jpeg's own calligraphy/ornamentation (deep
# green letterforms, gold-bronze filigree) so the plain-text title reads as
# part of the same design instead of clashing default-black text sitting
# above an already-ornate logo.
COVER_GREEN = "#12492b"
COVER_GOLD = "#b8892f"

# Shared by both callers' own <style> blocks (build_pdf's A4 report page and
# export_annotated_pdf._insert_cover_page's book-sized page) so the title
# treatment can't drift between the two -- same reasoning as cover_page_html
# itself being a single shared function rather than two copies.
COVER_TITLE_CSS = f"""
      .cover-title {{
        font-family: "Noto Kufi Arabic", "Noto Naskh Arabic", serif;
        font-weight: bold;
        color: {COVER_GREEN};
        text-shadow: 0 1pt 0 rgba(184, 137, 47, 0.35);
      }}
      .cover-title-rule {{
        display: inline-block;
        width: 120pt;
        height: 2pt;
        margin: 0 0 20pt;
        background: linear-gradient(to right, transparent, {COVER_GOLD}, transparent);
        position: relative;
      }}
      .cover-title-rule::after {{
        content: "";
        position: absolute;
        left: 50%; top: 50%;
        width: 6pt; height: 6pt;
        margin: -3pt 0 0 -3pt;
        background: {COVER_GOLD};
        transform: rotate(45deg);
      }}
"""


def build_pdf(
    comments: list[dict],
    book_title_ar: str = "",
    author_ar: str = "",
    commentator_ar: str = "",
    club_image_bytes: bytes | None = None,
    telegram_icon_bytes: bytes | None = None,
    facebook_icon_bytes: bytes | None = None,
) -> bytes:
    """
    comments: list of {text, page, start, end}, optionally with
    position_in_page/page_word_count (word offset within the page) and
    context_before (book text read right before the comment) -- both
    rendered when present, silently omitted otherwise. (n_words ignored
    if present.)
    Returns PDF file bytes. Report is Arabic-only, RTL throughout.

    A club-branded cover page (see cover_page_html) is prepended whenever
    there's anything to put on it (book title/author/commentator or a club
    image) -- omitted entirely rather than shown blank when none of that is
    given. It flows straight into the comment count + entries below rather
    than forcing its own page: this report covers the whole book's
    comments, not one session, so there's no separate per-session header to
    repeat afterward -- repeating the club name/book metadata a second time
    right below the cover was pure redundancy.
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

    cover_html = ""
    if book_title_ar or author_ar or commentator_ar or club_image_bytes:
        cover_html = (
            '<div class="cover-page-wrap">'
            + cover_page_html(
                book_title_ar=book_title_ar,
                author_ar=author_ar,
                commentator_ar=commentator_ar,
                club_image_bytes=club_image_bytes,
                telegram_icon_bytes=telegram_icon_bytes,
                facebook_icon_bytes=facebook_icon_bytes,
            )
            + "</div>"
        )

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
      .subtitle {{ font-size: 10pt; color: #666; margin: 10pt 0 24pt; }}
      .entry {{ margin-bottom: 18pt; padding-bottom: 14pt; border-bottom: 0.5pt solid #ccc; }}
      .meta {{ font-size: 9pt; color: #888; margin-bottom: 4pt; }}
      .context {{ font-size: 10pt; color: #999; font-style: italic; margin: 0 0 4pt; }}
      .text {{ font-size: 14pt; line-height: 1.9; margin: 0; }}
      .cover-page-wrap {{ text-align: center; padding: 50pt 0 40pt; }}
      .cover-title {{ font-size: 32pt; margin: 0 0 14pt; }}
      {COVER_TITLE_CSS}
      .cover-image {{ max-width: 70%; max-height: 320pt; margin: 0 auto 26pt; display: block; }}
      .cover-meta {{ margin-bottom: 22pt; }}
      .cover-meta-line {{ font-size: 17pt; color: #333; margin-bottom: 8pt; }}
      .cover-social {{ display: flex; justify-content: center; gap: 26pt; margin-top: 12pt; }}
      .cover-social-link {{
        display: flex; align-items: center; gap: 7pt;
        text-decoration: none; color: {COVER_GREEN};
      }}
      .cover-social-icon {{ width: 28pt; height: 28pt; display: block; }}
      .cover-social-label {{ font-size: 13pt; font-weight: bold; white-space: nowrap; }}
      /* In-flow (not bottom-pinned like the annotated export's): the report's
         cover shares page 1 with the first entries, so there is no "bottom of
         the cover" to pin to without overlapping them. */
      .cover-footer {{
        direction: ltr; font-size: 8pt; color: #999;
        border-top: 0.5pt solid #ddd; padding-top: 6pt; margin-top: 18pt;
      }}
      .cover-footer-link {{ color: #999; text-decoration: none; }}
    </style>
    </head>
    <body>
      {cover_html}
      <div class="subtitle">عدد التعليقات: {len(comments)}</div>
      {rows}
    </body>
    </html>
    """
    return HTML(string=html_doc).write_pdf()
