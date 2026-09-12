"""Manifest-driven discovery for original GBF character and effect assets."""

import re


class AssetNotFound(ValueError):
    """A confirmed HTTP 404, rather than a proxy/network failure."""


def discover(npc, folder, cdn, fetch, style=1):
    """Probe game-owned manifests; cache responses for the subsequent preparation."""
    if not re.fullmatch(r"\d{10}", npc):
        raise ValueError("Invalid NPC ID")
    folder.mkdir(parents=True, exist_ok=True)
    seen = {}
    references = set()

    def probe(asset):
        if asset in seen:
            return seen[asset]
        if not re.fullmatch(r"(?:npc|nsp|phit|ab)_[A-Za-z0-9_]+", asset):
            raise ValueError("Invalid game asset name")
        try:
            manifest = fetch(cdn + "/js/model/manifest/" + asset + ".js")
        except AssetNotFound:
            seen[asset] = False
            return False
        # A manifest without its matching script is a broken dependency, not a miss.
        script = fetch(cdn + "/js/cjs/" + asset + ".js")
        (folder / (asset + ".manifest.js")).write_bytes(manifest)
        (folder / (asset + ".js")).write_bytes(script)
        for content in (manifest, script):
            references.update(
                re.findall(
                    r'["\']((?:(?:npc|nsp)_[0-9]{10}|ab_(?:all_)?[0-9]{10}|phit_[A-Za-z0-9_]*[0-9])[A-Za-z0-9_]*)\.js["\']',
                    content.decode("utf-8"),
                )
            )
        seen[asset] = True
        return True

    records = {}
    style_suffixes = [""] + (["_st" + str(style)] if style > 1 else [])
    for appearance in range(1, 5):
        for suffix in style_suffixes:
            for form in ("", "_f1", "_f2", "_s2", "_0", "_1"):
                variant = f"{appearance:02d}" + suffix + form
                asset = "npc_" + npc + "_" + variant
                if probe(asset):
                    records[variant] = {"mode": "original", "asset": asset}
    if not records:
        raise ValueError("No character animation manifests found on the game CDN")

    abilities = []
    # Check all slots independently: a gap does not terminate the scan.
    for prefix in ("ab_", "ab_all_"):
        for slot in range(1, 21):
            asset = prefix + npc + f"_{slot:02d}"
            if probe(asset):
                abilities.append(asset)
    for variant, record in records.items():
        base = "nsp_" + npc + "_" + variant
        specials = []
        # Standard exported charge-attack naming: s2 is the battle effect;
        # letter suffixes supply additional mortal motions. All found exports
        # are kept as dependencies even when not chosen as a default.
        for suffix in ("_s2", "", "_s3"):
            candidate = base + suffix
            if probe(candidate):
                specials.append(candidate)
        for letter in "bcdefghijk":
            for suffix in ("_s2_", "_"):
                candidate = base + suffix + letter
                if probe(candidate):
                    specials.append(candidate)
                    break
        hit_candidates = ["phit_" + npc + "_" + variant]
        if variant.startswith("01_"):
            hit_candidates.append("phit_" + npc + variant[2:])
        hit_candidates.append("phit_" + npc)
        hit = next((name for name in hit_candidates if probe(name)), "")
        record.update(
            specials=specials,
            hit=hit,
            abilities=list(abilities),
            defaultMortal="mortal_A",
        )

    # Follow only explicit references in game scripts/manifests, including
    # shared effects. Never consult a third-party index or guess weapon effects.
    followed = set()
    while references - followed:
        if len(followed) > 256:
            raise ValueError("Game animation dependency limit exceeded")
        name = min(references - followed)
        followed.add(name)
        if not probe(name):
            raise ValueError("Missing referenced game animation: " + name)
    shared = sorted(
        name
        for name in followed
        if seen.get(name) and name.startswith(("phit_", "ab_", "nsp_"))
    )
    shared_hits = [name for name in shared if name.startswith("phit_")]
    for record in records.values():
        record["dependencies"] = list(shared)
        if not record["hit"]:
            if len(shared_hits) == 1:
                record["hit"] = shared_hits[0]
            else:
                print("No unambiguous game hit effect found for " + record["asset"])
    return records


def sheet_entries(manifest):
    entries = re.findall(
        r'"/sp/cjs/([A-Za-z0-9_]+\.png)"\s*,\s*id:"([A-Za-z0-9_]+)"', manifest
    )
    if not entries:
        raise ValueError("No spritesheets in manifest")
    for filename, image_id in entries:
        if filename != image_id + ".png":
            raise ValueError("Manifest image identity mismatch")
    return entries


def canonical(filename):
    family, rest = filename.split("_", 1)
    return "Npc " + ("cjs" if family == "npc" else family) + " " + rest


def prepare(records, folder, cdn, fetch):
    """Resolve sheets and scripts into the package consumed by the publisher."""

    def download(relative, destination):
        # Discovery has already cached most scripts and manifests for this run.
        if not destination.exists():
            temporary = destination.with_name(destination.name + ".part")
            temporary.write_bytes(fetch(cdn.rstrip("/") + "/" + relative))
            temporary.replace(destination)
        return destination.read_bytes()

    assets = sorted(
        {
            asset
            for record in records.values()
            for asset in [
                record["asset"],
                record["hit"],
                *record["specials"],
                *record["abilities"],
                *record.get("dependencies", []),
            ]
            if asset
        }
    )
    descriptors, sheets, scripts = {}, {}, {}
    for asset in assets:
        if not re.fullmatch(r"(npc|nsp|phit|ab)_[A-Za-z0-9_]+", asset):
            raise ValueError("Unsupported animation asset " + asset)

        manifest = download(
            "js/model/manifest/" + asset + ".js", folder / (asset + ".manifest.js")
        ).decode("utf-8")
        download("js/cjs/" + asset + ".js", folder / (asset + ".js")).decode("utf-8")
        descriptor = {"asset": asset, "sheets": [], "imageIds": []}
        for filename, image_id in sheet_entries(manifest):
            name = canonical(filename)
            path = folder / name
            png = download("img/sp/cjs/" + filename, path)
            if not png.startswith(b"\x89PNG\r\n\x1a\n"):
                raise ValueError("Invalid PNG " + filename)
            sheets[name] = path
            descriptor["sheets"].append(name)
            descriptor["imageIds"].append(image_id)
        descriptors[asset] = descriptor
        scripts[asset] = str((folder / (asset + ".js")).resolve())
    for record in records.values():
        descriptor = descriptors[record["asset"]]
        record.update(descriptor)
        record["sheet"] = descriptor["sheets"][0]
        record["effects"] = {
            asset: descriptors[asset]
            for asset in [
                record["hit"],
                *record["specials"],
                *record["abilities"],
                *record.pop("dependencies", []),
            ]
            if asset
        }
    return {
        "records": records,
        "scripts": scripts,
        "sheets": {name: str(path.resolve()) for name, path in sheets.items()},
    }
