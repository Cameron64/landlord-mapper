import urllib.parse
from lm.chrome import dates_strip, footer, legend_strip, lookup_form, scope_note, shell, topline
from lm.config import MAX_HITS, PAGE_SIZE
from lm.fmt import e, num
from lm.store import STORE
from lm.widgets import HIT_HEAD, hit_rows

# ---------------------------------------------------------------------------
# page: home
# ---------------------------------------------------------------------------
# The five questions, as links with a one-line gloss. Each page describes itself
# at the top; a landing page only has to name the door. Labels match the nav so
# the same page is not called two things.
DOORS = (
    ("#lookup", "Who owns my building",
     "an address, to the LLC, to the people who signed"),
    ("/rankings", "Biggest landlords",
     "ranked by parcels, units and roll value"),
    ("/explore", "Where I organize",
     "filter by county, ZIP, building size and roll value"),
    ("/explore", "Take the list",
     "any view, ranking or portfolio downloads as a CSV"),
    ("/method", "Can I trust this",
     "every number, and the rule behind it"),
)

def doors_band():
    items = "".join(
        "<li><a href=\"%s\">%s</a> &middot; %s</li>" % (e(href), e(title), e(gloss))
        for href, title, gloss in DOORS)
    return (
        "<section class=\"wrap band\" id=\"main\" aria-labelledby=\"doors-h\" "
        "style=\"padding-top:0\">"
        "<h2 class=\"eyebrow\" id=\"doors-h\">What this tool answers</h2>"
        "<ul class=\"doorlist\">%s</ul>"
        "</section>" % items)

def page_home():
    body = [
        "<header class=\"wrap masthead\">", topline("/"),
        "<h1>Who owns<br />the building<br /><em>you rent</em></h1>",
        "<p class=\"deck\">The county appraisal roll names the LLC on the property. The state "
        "franchise tax registry names the people who signed for it. This page joins the two, "
        "with the source and the date on every step</p>",
        "<div id=\"lookup\" style=\"margin-top:1.6rem\">", lookup_form(), "</div>",
        dates_strip(),
        "</header>",
        doors_band(),
        legend_strip(),
        footer(),
    ]
    # The lookup is the whole point of this page, so that is where the skip link
    # goes. Every other page keeps the default #main.
    return shell("Landlord Mapper - who owns your building", "".join(body), "#lookup")

def page_search(q, page):
    hits = STORE.search(q)
    if not hits:
        return page_no_hits(q)
    if len(hits) == 1:
        return None, hits[0]
    total = len(hits)
    pages = (total + PAGE_SIZE - 1) // PAGE_SIZE
    page = max(1, min(page, pages))
    window = hits[(page - 1) * PAGE_SIZE: page * PAGE_SIZE]
    rows = hit_rows(window)
    prev_cls = "btn btn-quiet" + ("" if page > 1 else " btn-off")
    next_cls = "btn btn-quiet" + ("" if page < pages else " btn-off")
    qq = urllib.parse.quote(q)
    pager = (
        "<div class=\"pager\">"
        "<a class=\"%s\" href=\"/search?q=%s&amp;page=%d\">Previous</a>"
        "<span>Page %d of %d &middot; %s parcels matched%s</span>"
        "<a class=\"%s\" href=\"/search?q=%s&amp;page=%d\">Next</a>"
        "</div>"
        % (prev_cls, qq, max(1, page - 1), page, pages, num(total),
           " (capped at %s)" % num(MAX_HITS) if total >= MAX_HITS else "",
           next_cls, qq, min(pages, page + 1)))
    body = [
        "<header class=\"wrap masthead\">", topline(),
        "<h1 style=\"font-size:clamp(1.6rem,6vw,2.9rem)\">%s parcels<br />match <em>%s</em></h1>"
        % (num(total), e(q.upper())),
        "<div style=\"margin-top:1.8rem\">", lookup_form(q), "</div>",
        scope_note(),
        "</header>",
        "<section class=\"wrap band\" id=\"main\" aria-labelledby=\"res-h\" style=\"padding-top:0\">",
        "<h3 class=\"subhead\" id=\"res-h\">Pick the parcel you meant</h3>",
        "<div class=\"tablescroll\"><table>", HIT_HEAD,
        "<tbody>", rows, "</tbody></table></div>",
        "<p class=\"tblnote\">Market value is the county value on the roll, not a sale price "
        "&middot; unit counts are estimates and are not shown in this list</p>",
        pager,
        "</section>",
        footer(),
    ]
    return shell("%s parcels match %s - Landlord Mapper" % (total, q), "".join(body)), None

def page_no_hits(q):
    body = [
        "<header class=\"wrap masthead\">", topline(),
        "<h1 style=\"font-size:clamp(1.6rem,6vw,2.9rem)\">Nothing matched<br /><em>that address</em></h1>",
        "<div style=\"margin-top:1.8rem\">", lookup_form(q), "</div>",
        "</header>",
        "<section class=\"wrap band\" id=\"main\" style=\"padding-top:0\">",
        "<div class=\"empty\"><h3>No parcel on the rolls contains %s</h3>"
        "<p>The likely cause is that the address sits outside the county rolls loaded here. "
        "Those are %s. Every parcel on them is searchable, in scope for the registry lookup or "
        "not, so being owner-occupied or small is not what keeps an address out of this list</p>"
        "<p>Try the street number on its own, or the street name on its own. The match is a "
        "plain substring on the address as the county wrote it, so BLVD and BOULEVARD are "
        "not the same string</p></div>"
        % (e(q.upper()),
           e(", ".join(sorted(STORE.stats.get("counties", {}))) or "none loaded")),
        scope_note(),
        "</section>",
        footer(),
    ]
    return shell("Nothing matched - Landlord Mapper", "".join(body)), None
