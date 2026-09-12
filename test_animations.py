import json
import struct
import tempfile
import types
import unittest
from io import BytesIO
from pathlib import Path
from unittest.mock import Mock, patch

from botocore.exceptions import ClientError

import animations
from animation_assets import AssetNotFound, canonical, discover, prepare, sheet_entries
from animation_download import AssetDownloader
from animation_r2 import R2Store, r2_user_agent
from http_settings import BROWSER_USER_AGENT
from images import WikiImages


class AnimationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.folder = Path(self.temp.name)
        self.png = self.folder / "sheet.png"
        self.png.write_bytes(
            b"\x89PNG\r\n\x1a\n" + b"\0" * 8 + struct.pack(">II", 12, 20)
        )
        self.script = self.folder / "npc_3040120000_01.js"
        self.script.write_text("var exported = true;")
        self.owner = types.SimpleNamespace(
            wiki=Mock(), delay=0, _status_callback=Mock()
        )
        self.owner._perform_wiki_action_with_retry = lambda action, *a, **kw: action(
            *a, **kw
        )
        self.owner.wiki.api.return_value = {"query": {"pages": [{"missing": True}]}}
        self.owner.wiki.upload.return_value = {"result": "Success"}
        self.package = {
            "sheets": {"Npc cjs 3040120000_01.png": str(self.png)},
            "scripts": {"npc_3040120000_01": str(self.script)},
            "records": {
                "01": {
                    "asset": "npc_3040120000_01",
                    "effects": {},
                    "sheets": ["Npc cjs 3040120000_01.png"],
                    "imageIds": ["npc_3040120000_01"],
                }
            },
        }

    def test_manifest_last_and_canonical_upload(self):
        events = []
        self.owner.wiki.upload.side_effect = lambda *a, **kw: (
            events.append(("wiki", kw)) or {"result": "Success"}
        )
        store = Mock()
        store.put.side_effect = lambda key, *args: (
            events.append(("r2", key)) or "uploaded"
        )
        result = animations.publish(self.owner, "3040120000", self.package, store)
        self.assertEqual(events[0][1]["filename"], "Npc cjs 3040120000_01.png")
        self.assertIn("description", events[0][1])
        self.assertEqual(events[-1], ("r2", "anim/3040120000/manifest.json"))
        self.assertEqual(result["sheets_uploaded"], 1)

    def test_failure_never_publishes_manifest(self):
        self.owner.wiki.upload.side_effect = RuntimeError("failed")
        store = Mock()
        with self.assertRaises(RuntimeError):
            animations.publish(self.owner, "3040120000", self.package, store)
        store.put.assert_not_called()
        self.owner.wiki.upload.side_effect = None
        store.put.side_effect = ValueError("conflicting script")
        with self.assertRaises(ValueError):
            animations.publish(self.owner, "3040120000", self.package, store)
        self.assertEqual(store.put.call_count, 1)

    def test_dry_run_performs_no_writes(self):
        store = Mock()
        result = animations.publish(
            self.owner, "3040120000", self.package, store, dry_run=True
        )
        self.owner.wiki.upload.assert_not_called()
        store.put.assert_not_called()
        self.assertEqual(result["manifest"], "planned")

    def test_publication_progress_and_pacing(self):
        self.owner.delay = 5
        store = Mock()
        store.put.return_value = "uploaded"
        with patch("animations.time.sleep") as sleep:
            counts = animations.publish(self.owner, "3040120000", self.package, store)
        self.assertEqual([call.args for call in sleep.call_args_list], [(5,), (5,)])
        progress = [call.kwargs for call in self.owner._status_callback.call_args_list]
        self.assertEqual([item["animation_processed"] for item in progress], [1, 2, 3])
        self.assertEqual([item["animation_total"] for item in progress], [3, 3, 3])
        self.assertNotIn("manifest", progress[0]["animation"])
        self.assertEqual(progress[-1]["animation"], counts)
        manifest = json.loads(store.put.call_args.args[1])
        self.assertEqual(
            manifest,
            {"version": 1, "id": "3040120000", "variants": self.package["records"]},
        )

    def test_preparation_reuses_cached_exports_and_includes_effect_sheets(self):
        character = "npc_3040120000_01"
        effect = "phit_3040120000"
        records = {
            "01": {
                "asset": character,
                "hit": effect,
                "specials": [],
                "abilities": [],
                "dependencies": [effect],
            }
        }
        for asset in (character, effect):
            (self.folder / (asset + ".manifest.js")).write_text(
                f'manifest:[{{src:"/sp/cjs/{asset}.png",id:"{asset}"}}]',
                encoding="utf-8",
            )
            (self.folder / (asset + ".js")).write_text(
                "var original = true;", encoding="utf-8"
            )
        fetch = Mock(return_value=self.png.read_bytes())
        package = prepare(records, self.folder, "https://game.example/", fetch)
        self.assertEqual(
            [call.args[0] for call in fetch.call_args_list],
            [
                f"https://game.example/img/sp/cjs/{asset}.png"
                for asset in (character, effect)
            ],
        )
        self.assertEqual(set(package["scripts"]), {character, effect})
        self.assertEqual(
            set(package["sheets"]),
            {"Npc cjs 3040120000_01.png", "Npc phit 3040120000.png"},
        )
        variant = package["records"]["01"]
        self.assertEqual(variant["sheet"], "Npc cjs 3040120000_01.png")
        self.assertEqual(variant["effects"][effect]["imageIds"], [effect])
        self.assertNotIn("dependencies", variant)

    def test_existing_optimized_png_is_skipped(self):
        self.owner.wiki.api.return_value = {
            "query": {
                "pages": [
                    {"imageinfo": [{"mime": "image/png", "width": 12, "height": 20}]}
                ]
            }
        }
        self.assertEqual(
            animations.ensure_sheet(self.owner, "Npc cjs x.png", self.png), "existing"
        )
        self.owner.wiki.upload.assert_not_called()

    def test_redirect_and_wrong_dimensions_are_not_overwritten(self):
        for page in [
            {"redirect": True},
            {"imageinfo": [{"mime": "image/png", "width": 13, "height": 20}]},
        ]:
            self.owner.wiki.api.return_value = {"query": {"pages": [page]}}
            with self.assertRaises(ValueError):
                animations.ensure_sheet(self.owner, "Npc cjs x.png", self.png)
        self.owner.wiki.upload.assert_not_called()

    def test_duplicate_warning_keeps_physical_canonical(self):
        self.owner.wiki.upload.return_value = {
            "result": "Warning",
            "warnings": {"duplicate": ["Other.png"]},
            "filekey": "abc",
        }
        self.owner.wiki.api.side_effect = [
            {"query": {"pages": [{"missing": True}]}},
            {"upload": {"result": "Success"}},
        ]
        self.assertEqual(
            animations.ensure_sheet(self.owner, "Npc cjs x.png", self.png), "uploaded"
        )
        self.assertEqual(self.owner.wiki.api.call_args.args, ("upload",))
        self.assertEqual(
            self.owner.wiki.api.call_args.kwargs["filename"], "Npc cjs x.png"
        )

    def test_dry_run_does_not_construct_r2(self):
        with (
            patch.dict("os.environ", {"DRY_RUN": "true"}),
            patch("animations.discover", return_value={}),
            patch("animations.AssetDownloader") as download,
            patch("animations.prepare", return_value=self.package),
            patch("animation_r2.R2Store") as store,
        ):
            animations.upload_character_animations(self.owner, "3040120000")
            store.assert_not_called()
            download.return_value.close.assert_called_once()
            self.owner.wiki.upload.assert_not_called()

    def test_discovery_uses_only_game_paths_and_does_not_stop_at_gaps(self):
        npc = "3040120000"
        assets = {
            "npc_" + npc + "_01",
            "npc_" + npc + "_01_st2",
            "nsp_" + npc + "_01_s2",
            "phit_" + npc,
            "ab_all_" + npc + "_03",
        }
        requested = []

        def fetch(url):
            requested.append(url)
            asset = url.rsplit("/", 1)[1][:-3]
            if asset not in assets:
                raise AssetNotFound("missing")
            return (
                b"manifest:[]"
                if "/manifest/" in url
                else b"var labels = {ab_motion: 0};"
            )

        records = discover(
            npc, self.folder, "https://game.example/assets_en", fetch, style=2
        )
        self.assertEqual(set(records), {"01", "01_st2"})
        self.assertIn("ab_all_" + npc + "_03", records["01"]["abilities"])
        self.assertEqual(records["01"]["hit"], "phit_" + npc)
        self.assertTrue(
            all(url.startswith("https://game.example/assets_en/") for url in requested)
        )
        self.assertEqual(
            canonical("npc_3040120000_01.png"), "Npc cjs 3040120000_01.png"
        )
        with self.assertRaises(ValueError):
            sheet_entries("[]")

    def test_discovery_does_not_treat_proxy_error_as_missing(self):
        with self.assertRaisesRegex(ValueError, "HTTP 403"):
            discover(
                "3040120000",
                self.folder,
                "https://game.example",
                Mock(side_effect=ValueError("HTTP 403")),
            )

    def test_download_identity_is_independent_of_wiki_credentials(self):
        with patch.dict("os.environ", {"USER_AGENT": "wiki-whitelisted-agent"}):
            downloader = AssetDownloader("http://user:pass@localhost:8888")
            self.addCleanup(downloader.close)
            self.assertEqual(
                downloader.session.headers["User-Agent"], BROWSER_USER_AGENT
            )
            self.assertIn("Chrome/152.0.0.0", BROWSER_USER_AGENT)
            self.assertEqual(r2_user_agent(), "wiki-whitelisted-agent")

    def test_r2_default_identity_is_preserved(self):
        with patch.dict("os.environ", {}, clear=True):
            self.assertIn("Chrome/141.0.0.0", r2_user_agent())

    def test_proxy_cannot_fall_back_to_environment(self):
        downloader = AssetDownloader("http://user:pass@localhost:8888")
        self.addCleanup(downloader.close)
        self.assertFalse(downloader.session.trust_env)
        self.assertEqual(
            downloader.session.proxies["https"], "http://user:pass@localhost:8888"
        )

    def test_r2_skip_conflict_and_conditional_create(self):
        store = object.__new__(R2Store)
        store.bucket = "test"
        store.client = Mock()
        store.client.get_object.return_value = {"Body": BytesIO(b"same")}
        self.assertEqual(
            store.put("anim/3040120000/test.js", b"same", "text/javascript"), "existing"
        )
        store.client.put_object.assert_not_called()
        store.client.get_object.return_value = {"Body": BytesIO(b"other")}
        with self.assertRaises(ValueError):
            store.put("anim/3040120000/test.js", b"same", "text/javascript")
        store.client.get_object.side_effect = ClientError(
            {
                "Error": {"Code": "NoSuchKey"},
                "ResponseMetadata": {"HTTPStatusCode": 404},
            },
            "GetObject",
        )
        self.assertEqual(
            store.put("anim/3040120000/test.js", b"same", "text/javascript"), "uploaded"
        )
        self.assertEqual(store.client.put_object.call_args.kwargs["IfNoneMatch"], "*")
        with self.assertRaises(ValueError):
            store.put("other/test.js", b"x", "text/javascript")

    def test_character_integration_excludes_profile_subset(self):
        owner = types.SimpleNamespace(
            check_sp_asset=Mock(), check_character_animations=Mock()
        )
        WikiImages.check_character(owner, "page")
        owner.check_character_animations.assert_called_once_with("page")
        owner.check_character_animations.reset_mock()
        WikiImages.check_character(
            owner, "page", asset_sections=("profile",), include_character_extras=False
        )
        owner.check_character_animations.assert_not_called()


if __name__ == "__main__":
    unittest.main()
