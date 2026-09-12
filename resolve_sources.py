"""Hand-run discovery helper.  It prints candidates; it never edits sources.json."""
from __future__ import annotations
import json
import re
from urllib.parse import urlparse
import fetchers

def candidates(source):
    name = source["name"].lower()
    host = urlparse(source["careers_url"]).hostname or ""
    second = host.split(".")[-2] if "." in host else host
    return list(dict.fromkeys([re.sub(r"[^a-z0-9]", "", name), re.sub(r"[^a-z0-9]+", "-", name).strip("-"), second]))

def main():
    sources = json.load(open("sources.json"))["sources"]
    adapters = ("greenhouse", "lever", "ashby", "smartrecruiters", "recruitee", "softgarden", "personio_xml", "join_com")
    print("source\tadapter\tslug\tcount\tfirst job")
    for source in sources:
        if source.get("adapter") is not None: continue
        found = []
        for adapter in adapters:
            for slug in candidates(source):
                try:
                    rows = getattr(fetchers, adapter)(slug)
                    if rows:
                        first = rows[0]
                        print(f"{source['id']}\t{adapter}\t{slug}\t{len(rows)}\t{first['title']} — {first['location']}")
                        found.append((adapter, slug)); break
                except fetchers.FetchError:
                    pass
        if found:
            adapter, slug = found[0]
            key = {"greenhouse":"board", "lever":"company", "ashby":"board", "smartrecruiters":"company", "recruitee":"company", "softgarden":"slug", "personio_xml":"subdomain", "join_com":"slug"}[adapter]
            print("paste after human confirmation:", json.dumps({"id": source["id"], "adapter": adapter, "params": {key: slug}}))

if __name__ == "__main__": main()
