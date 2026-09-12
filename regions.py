"""Bucketing a free-text posting location into a region code.

Used by the census, not by the daily radar. Kept separate from the search profile
because the question is different: a profile decides whether a location is in scope,
a region map decides which bucket a count belongs to.

The map lives in JSON (see profiles/regions-eu.json) so a census of a different
market needs no code change.
"""
from __future__ import annotations

import json

from filters import normal, ProfileError


class Regions:
    def __init__(self, spec: dict, path: str = "") -> None:
        self.path = path
        self.name = spec.get("name", "regions")
        self.remote = tuple(normal(t) for t in spec.get("remote", ()))
        self.remote_code = spec.get("remote_code", "eu-remote")
        self.exclude = tuple(normal(t) for t in spec.get("exclude", ()))
        self.countries = {code: tuple(normal(t) for t in tokens)
                          for code, tokens in (spec.get("countries") or {}).items()}

    @classmethod
    def load(cls, path: str) -> "Regions":
        try:
            with open(path, encoding="utf-8") as handle:
                return cls(json.load(handle), path)
        except FileNotFoundError:
            raise ProfileError("no region map at %s" % path) from None
        except json.JSONDecodeError as exc:
            raise ProfileError("%s is not valid JSON: %s" % (path, exc)) from None

    def country_of(self, location: str) -> str | None:
        """Region code, the remote code, or None when the posting is out of scope.

        A posting listing several offices resolves to the *first* in-scope region it
        names, so "Berlin; Paris" counts once for Germany rather than once for each
        — otherwise a multi-office listing quietly inflates every market it touches.
        """
        text = normal(location)
        if any(marker in text for marker in self.remote):
            return self.remote_code

        hits = []
        for code, tokens in self.countries.items():
            for token in tokens:
                index = text.find(token)
                if index >= 0:
                    hits.append((index, code))
                    break
        if not hits:
            return None

        # An excluded place named before any in-scope place means the posting is
        # elsewhere: "London, UK (also Berlin)" is a London job.
        first_excluded = min((text.find(x) for x in self.exclude if x in text), default=-1)
        best_index, best_code = min(hits)
        if 0 <= first_excluded < best_index:
            return None
        return best_code
