"""Character animation publication using the existing wiki session and R2."""

import json
import os
import re
import struct
import tempfile
import time
from io import BytesIO
from pathlib import Path

from animation_assets import discover, prepare
from animation_download import AssetDownloader

SHEET_DESCRIPTION = (
    "Character animation spritesheet.\n"
    "[[Category:Character Images]]\n"
    "[[Category:Character Spritesheets]]"
)
UPLOAD_COMMENT = "Uploaded by VyrnBot"


def register_script(asset, path):
    if not re.fullmatch(r"(?:npc|nsp|phit|ab)_[A-Za-z0-9_]+", asset):
        raise ValueError("Invalid animation script name")
    return (
        "SpriteAnimation.register("
        + json.dumps(asset)
        + ", function(lib, images, createjs, require) {\n"
        + "var play_flag1 = false, play_flag2 = false;\n"
        + Path(path).read_text(encoding="utf-8")
        + "\n});\n"
    ).encode("utf-8")


def ensure_sheet(owner, name, path, dry_run=False):
    """Keep real canonical files: redirect-only duplicates break direct image URLs."""
    content = Path(path).read_bytes()
    if len(content) < 24 or content[:8] != b"\x89PNG\r\n\x1a\n":
        raise ValueError("Invalid spritesheet PNG: " + name)
    width, height = struct.unpack(">II", content[16:24])
    response = owner._perform_wiki_action_with_retry(
        owner.wiki.api,
        "query",
        prop="imageinfo",
        iiprop="size|mime",
        titles="File:" + name,
        formatversion=2,
    )
    pages = response["query"]["pages"]
    page = next(iter(pages.values())) if isinstance(pages, dict) else pages[0]
    info = page.get("imageinfo") or []
    if info:
        actual = info[0]
        if (
            actual.get("mime") != "image/png"
            or actual.get("width") != width
            or actual.get("height") != height
        ):
            raise ValueError("Existing spritesheet dimensions/type differ: " + name)
        return "existing"
    if "missing" not in page:
        raise ValueError(
            "Spritesheet title exists without a real image; review manually: " + name
        )
    if dry_run:
        return "planned"
    # Reuse the bot's retry helper and authenticated mwclient session. The generic
    # check_image() may move/redirect canonicals, which this manifest format cannot use.
    result = owner._perform_wiki_action_with_retry(
        owner.wiki.upload,
        BytesIO(content),
        filename=name,
        comment=UPLOAD_COMMENT,
        description=SHEET_DESCRIPTION,
    )
    if result.get("result") == "Warning":
        warnings = set(result.get("warnings", {}))
        if (
            warnings
            and warnings <= {"duplicate", "duplicateversions", "no-change"}
            and result.get("filekey")
        ):
            # A duplicate under another filename still needs this physical path.
            # Never ignore an 'exists' warning or overwrite a canonical file.
            response = owner._perform_wiki_action_with_retry(
                owner.wiki.api,
                "upload",
                filename=name,
                filekey=result["filekey"],
                token=owner.wiki.get_token("csrf"),
                ignorewarnings=1,
                comment=UPLOAD_COMMENT,
                text=SHEET_DESCRIPTION,
            )
            result = response["upload"]
    if result.get("result") != "Success":
        raise ValueError("Spritesheet upload did not succeed: " + name)
    return "uploaded"


def publish(owner, npc, package, store, dry_run=False):
    counts = {
        kind + "_" + status: 0
        for kind in ("sheets", "scripts")
        for status in ("uploaded", "existing", "planned")
    }
    assets = sorted(
        {
            asset
            for record in package["records"].values()
            for asset in [record["asset"], *record.get("effects", {})]
        }
    )
    total = len(package["sheets"]) + len(assets) + 1
    processed = 0
    callback = getattr(owner, "_status_callback", lambda *args, **kwargs: None)

    def record_result(kind, result):
        nonlocal processed
        if kind == "manifest":
            counts[kind] = result
        else:
            counts[f"{kind}_{result}"] += 1
        processed += 1
        callback(
            "animation_uploading",
            animation_processed=processed,
            animation_total=total,
            animation=counts.copy(),
        )
        if kind != "manifest" and owner.delay > 0:
            time.sleep(owner.delay)

    for name, path in package["sheets"].items():
        result = ensure_sheet(owner, name, path, dry_run)
        record_result("sheets", result)
    for asset in assets:
        content = register_script(asset, package["scripts"][asset])
        result = (
            "planned"
            if dry_run
            else store.put(
                f"anim/{npc}/{asset}.js",
                content,
                "text/javascript; charset=utf-8",
            )
        )
        record_result("scripts", result)
    # Same serialization and filenames as the bulk uploader; publish only when
    # every image and script has succeeded. Existing differing objects are errors.
    manifest = {"version": 1, "id": npc, "variants": package["records"]}
    content = json.dumps(
        manifest, ensure_ascii=True, sort_keys=True, separators=(",", ":")
    ).encode()
    result = (
        "planned"
        if dry_run
        else store.put(
            f"anim/{npc}/manifest.json", content, "application/json; charset=utf-8"
        )
    )
    record_result("manifest", result)
    return counts


def upload_character_animations(owner, npc, style=1):
    if not re.fullmatch(r"\d{10}", npc):
        raise ValueError("Character animation upload requires a 10-digit NPC ID")
    dry_run = os.environ.get("DRY_RUN", "false").lower() in ("true", "1", "yes")
    callback = getattr(owner, "_status_callback", lambda *args, **kwargs: None)
    callback("animation_downloading", animation_id=npc)
    try:
        if dry_run:
            store = None
        else:
            from animation_r2 import R2Store

            store = R2Store()
        downloader = AssetDownloader(os.environ.get("PROXY_URL"), delay=owner.delay)
        try:
            cdn = "https://prd-game-a-granbluefantasy.akamaized.net/assets_en"
            downloader.preflight(cdn)
            with tempfile.TemporaryDirectory(prefix="gbf-animation-") as folder:
                folder = Path(folder)
                records = discover(npc, folder, cdn, downloader.fetch, style)
                package = prepare(records, folder, cdn, downloader.fetch)
                counts = publish(owner, npc, package, store, dry_run)
        finally:
            downloader.close()
    except ValueError:
        raise
    except Exception as error:  # noqa: BLE001 - redact SDK/request secrets from Discord output
        # SDK/request exception text can contain credentials; keep Discord logs safe.
        raise RuntimeError(
            "Character animation upload failed (" + type(error).__name__ + ")."
        ) from None
    prefix = "[DRY RUN] Animation plan" if dry_run else "Animation upload complete"
    print(prefix + " for " + npc + ": " + json.dumps(counts, sort_keys=True))
    return counts
