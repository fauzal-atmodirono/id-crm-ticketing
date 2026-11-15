"""Fork patch 0100 (16 Nov 2026): the sidebar "Set your availability" list
scrolls. Since 0065 it holds the operator's whole status catalogue plus
Offline, and upstream DropdownBody (components-next/dropdown-menu/base) is a
`div.absolute` around an unbounded `<ul>` -- the user could not scroll down to
the lower statuses. Context lines come from the v4.15.1 source in the proton
Chatwoot image with 0065 applied (this sandbox cannot reach github.com).
"""
from pathlib import Path

FORK = Path(__file__).resolve().parent.parent
P0100 = FORK / "patches" / "0100-availability-menu-scroll.patch"
FILE = "app/javascript/dashboard/components-next/sidebar/SidebarProfileMenuStatus.vue"


def _lines(prefix: str) -> list[str]:
    return [
        l[1:]
        for l in P0100.read_text().splitlines()
        if l.startswith(prefix) and not l.startswith(prefix * 3)
    ]


def test_touches_only_the_status_menu():
    files = [l.split(" b/", 1)[1] for l in P0100.read_text().splitlines() if l.startswith("diff --git ")]
    assert files == [FILE]


def test_caps_and_scrolls_the_status_list_and_nothing_else():
    assert _lines("-") == ['          <DropdownBody class="min-w-32 z-20">']
    added = "\n".join(_lines("+"))
    assert 'class="min-w-32 z-20 [&>ul]:max-h-[min(20rem,50vh)] [&>ul]:overflow-y-auto"' in added
    # the items and their click handler (0065's selectStatus) are untouched context
    assert "@click=\"selectStatus(status)\"" not in added


def test_sorts_after_0065_which_it_builds_on():
    names = sorted(p.name for p in (FORK / "patches").glob("*.patch"))
    assert names.index("0065-unify-availability-selector.patch") < names.index(P0100.name)
