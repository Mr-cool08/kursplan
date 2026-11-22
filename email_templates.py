"""Gemensam mejlpresentation som även fungerar utanför en Flask-förfrågan."""

from pathlib import Path

from jinja2 import Environment, FileSystemLoader, select_autoescape


_environment = Environment(
    loader=FileSystemLoader(Path(__file__).parent / "templates" / "emails"),
    autoescape=select_autoescape(("html", "xml"), default_for_string=True),
    trim_blocks=True,
    lstrip_blocks=True,
)

_STATUS_COLORS = {
    "info": ("#eaf0fc", "#2458b8"),
    "success": ("#e8f3ed", "#236043"),
    "neutral": ("#edf0f4", "#52617a"),
}


def render_email(
    *, title, preheader, introduction, details=(), explanation="", actions=(),
    notice="", eyebrow="Din utbildning", status="", tone="info",
):
    """Rendera innehåll med automatisk HTML-escaping och fasta färgteman."""
    status_background, status_color = _STATUS_COLORS.get(tone, _STATUS_COLORS["info"])
    return _environment.get_template("message.html").render(
        title=title,
        preheader=preheader,
        introduction=introduction,
        details=details,
        explanation=explanation,
        actions=tuple(actions),
        notice=notice,
        eyebrow=eyebrow,
        status=status,
        status_background=status_background,
        status_color=status_color,
    )
