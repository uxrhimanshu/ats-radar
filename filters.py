"""Title and location matching, driven entirely by a JSON search profile.

Nothing about any particular job search lives in this file. A profile supplies the
categories, their matching terms, the exclusion lists and the locations; this module
only decides how those are applied. Swapping `profiles/uxr-germany.json` for a
profile about backend roles in the Netherlands changes what the radar reports
without touching Python.

Run the self-check with:

    python3 filters.py profiles/uxr-germany.json
"""
from __future__ import annotations

import json
import os
import re
import sys
import unicodedata


def normal(value: str) -> str:
    """Fold case, strip accents and normalise ß, so 'Müller' and 'Mueller' compare.

    Job boards are inconsistent about all three, and a missed match here looks
    exactly like an absent posting.
    """
    value = unicodedata.normalize("NFKD", value or "")
    value = "".join(c for c in value if not unicodedata.combining(c))
    return value.casefold().replace("ß", "ss")


class ProfileError(Exception):
    pass


class Category:
    def __init__(self, spec: dict) -> None:
        self.id = spec["id"]
        self.label = spec.get("label", spec["id"])
        self.rank = int(spec.get("rank", 99))
        self.terms = tuple(normal(t) for t in spec.get("terms", ()))
        self.patterns = tuple(re.compile(p) for p in spec.get("patterns", ()))
        gate = spec.get("gate")
        self.gate_terms = tuple(normal(t) for t in (gate or {}).get("terms", ()))
        self.gate_patterns = tuple(re.compile(p) for p in (gate or {}).get("patterns", ()))
        self.has_gate = bool(self.gate_terms or self.gate_patterns)

    def gate_hit(self, text: str) -> bool:
        return (any(t in text for t in self.gate_terms)
                or any(p.search(text) for p in self.gate_patterns))

    def hit(self, text: str) -> bool:
        return (any(t in text for t in self.terms)
                or any(p.search(text) for p in self.patterns))


class Profile:
    """A search: what counts as a relevant role, and where."""

    def __init__(self, spec: dict, path: str = "") -> None:
        self.path = path
        self.name = spec.get("name", os.path.basename(path) or "unnamed")
        if not spec.get("categories"):
            raise ProfileError("profile %s defines no categories" % (path or self.name))
        self.categories = [Category(c) for c in spec["categories"]]
        self.exclusions = [
            (e.get("name", "excluded"), tuple(normal(t) for t in e.get("terms", ())))
            for e in spec.get("exclusions", ())
        ]
        loc = spec.get("locations", {})
        self.loc_include = tuple(normal(t) for t in loc.get("include", ()))
        self.loc_remote = tuple(normal(t) for t in loc.get("remote", ()))
        self.loc_exclude = tuple(normal(t) for t in loc.get("exclude", ()))
        self.seniority_pattern = (re.compile(spec["seniority_pattern"])
                                  if spec.get("seniority_pattern") else None)

    # -- loading ---------------------------------------------------------

    @classmethod
    def load(cls, path: str) -> "Profile":
        try:
            with open(path, encoding="utf-8") as handle:
                return cls(json.load(handle), path)
        except FileNotFoundError:
            raise ProfileError(
                "no search profile at %s.\nCopy one of the examples in profiles/ and "
                "edit it — see the README for the schema." % path) from None
        except json.JSONDecodeError as exc:
            raise ProfileError("%s is not valid JSON: %s" % (path, exc)) from None

    # -- matching --------------------------------------------------------

    def excluded_by(self, title: str) -> str | None:
        """Which exclusion list rejected this title, if any. Named, for diagnosis."""
        text = normal(title)
        for name, terms in self.exclusions:
            if any(t in text for t in terms):
                return name
        return None

    def category(self, title: str) -> str | None:
        """The best-fitting category id, or None when the title is not relevant.

        Categories are tried in profile order, so the profile author controls
        precedence. A gated category short-circuits: if the title trips its gate it
        can only be that category, and failing the gate's own terms rejects the
        title outright rather than letting it fall through to a looser rule.
        """
        if self.excluded_by(title):
            return None
        text = normal(title)
        for category in self.categories:
            if category.has_gate:
                if category.gate_hit(text):
                    return category.id if category.hit(text) else None
                continue
            if category.hit(text):
                return category.id
        return None

    def matches(self, title: str) -> bool:
        return self.category(title) is not None

    def label(self, category_id: str) -> str:
        for c in self.categories:
            if c.id == category_id:
                return c.label
        return category_id

    def rank(self, category_id: str | None) -> int:
        for c in self.categories:
            if c.id == category_id:
                return c.rank
        return 99

    def location_matches(self, location: str, extra_ok: list[str] | None = None) -> bool:
        """Whether a posting's location is in scope.

        `extra_ok` is a per-source escape hatch: some boards report a location the
        profile cannot anticipate (a campus name, an internal site code), and it is
        better to whitelist it on that one source than to widen the profile for
        everybody.
        """
        text = normal(location)
        if any(t in text for t in self.loc_exclude):
            return False
        if any(t in text for t in self.loc_include + self.loc_remote):
            return True
        return any(normal(x) in text for x in (extra_ok or []))

    def seniority(self, title: str) -> str:
        if not self.seniority_pattern:
            return "level unstated"
        return "Senior+" if self.seniority_pattern.search(normal(title)) else "level unstated"


# ---------------------------------------------------------------------------
# self-check
# ---------------------------------------------------------------------------

def run_checks(profile_path: str, cases_path: str | None = None) -> int:
    profile = Profile.load(profile_path)
    if cases_path is None:
        base = os.path.splitext(os.path.basename(profile_path))[0]
        cases_path = os.path.join("tests", "cases.%s.json" % base)
    try:
        with open(cases_path, encoding="utf-8") as handle:
            data = json.load(handle)
    except FileNotFoundError:
        print("no test cases at %s" % cases_path, file=sys.stderr)
        return 2

    failed = 0
    for case in data["cases"]:
        actual = profile.category(case["title"])
        expected = case["expect"]
        ok = actual == expected
        failed += not ok
        print("%s: %s  ->  %s (expected %s)"
              % ("PASS" if ok else "FAIL", case["title"], actual, expected))
    total = len(data["cases"])
    print("\n%d/%d passed  [%s]" % (total - failed, total, profile.name))
    return 1 if failed else 0


if __name__ == "__main__":
    args = sys.argv[1:]
    if not args:
        print(__doc__.strip().splitlines()[-1].strip(), file=sys.stderr)
        print("usage: python3 filters.py <profile.json> [cases.json]", file=sys.stderr)
        raise SystemExit(2)
    try:
        raise SystemExit(run_checks(args[0], args[1] if len(args) > 1 else None))
    except ProfileError as exc:
        print(exc, file=sys.stderr)
        raise SystemExit(2)
