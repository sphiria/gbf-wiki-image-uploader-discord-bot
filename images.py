import os
import sys
import requests
import mwclient
from mwclient.errors import APIError
import mwparserfromhell
import urllib.request
import re
import time
from datetime import datetime
import hashlib
import asyncio
import aiohttp
from io import BytesIO
from gbfwiki import GBFWiki, GBFDB
from dataclasses import dataclass
from typing import Callable, Optional, Sequence

UPLOAD_COMMENT = 'Uploaded by VyrnBot'

# optional for local development only
try:
    from dotenv import load_dotenv
    load_dotenv()
except Exception:
    # python-dotenv not installed
    pass

# read credentials from env (gbfwiki.login() will use these if present)
WIKI_USERNAME = os.environ.get("WIKI_USERNAME")
WIKI_PASSWORD = os.environ.get("WIKI_PASSWORD")
MITM_ROOT = os.environ.get("MITM_ROOT")


def _read_optional_float_env(var_name):
    raw_value = os.environ.get(var_name)
    if raw_value is None or raw_value == "":
        return None

    normalized = raw_value.strip().lower()
    try:
        if normalized.endswith("ms"):
            return float(normalized[:-2]) / 1000.0
        if normalized.endswith("s"):
            return float(normalized[:-1])

        parsed_value = float(normalized)
        # Bare large integers are usually supplied as milliseconds in local env files.
        if "." not in normalized and parsed_value >= 1000:
            return parsed_value / 1000.0
        return parsed_value
    except ValueError as exc:
        raise RuntimeError(f"{var_name} must be a number, got {raw_value!r}") from exc


IMAGE_PROBE_DELAY = _read_optional_float_env("IMAGE_PROBE_DELAY")
LOCAL_IMAGE_PROBE_DELAY = _read_optional_float_env("LOCAL_IMAGE_PROBE_DELAY")

def normalize_banner_id_input(raw_value):
    """
    Accept a bare banner id, `banner_<id>`, `banner_<id>.png`, or a full CDN URL
    and normalize it to the id portion used by gacha banner uploads.
    """
    cleaned = (raw_value or '').strip()
    if not cleaned:
        return ''

    cleaned = cleaned.rstrip('/')
    cleaned = cleaned.rsplit('/', 1)[-1]

    lowered = cleaned.lower()
    if lowered.endswith('.png') or lowered.endswith('.jpg'):
        cleaned = cleaned[:-4]
    elif lowered.endswith('.jpeg'):
        cleaned = cleaned[:-5]

    if cleaned.lower().startswith('banner_'):
        cleaned = cleaned[7:]

    return cleaned


@dataclass(frozen=True)
class AssetVariant:
    suffix: str
    label: str = ''


@dataclass(frozen=True)
class AssetSpec:
    section: str
    extension: str
    filename_suffix: str
    variants: Sequence[AssetVariant]
    categories: Sequence[str]
    section_label: Optional[str] = None
    url_builder: Optional[Callable[[str, str, AssetVariant, 'AssetSpec'], str]] = None
    canonical_name_builder: Optional[Callable[[str, str, AssetVariant, 'AssetSpec'], str]] = None
    other_names_builder: Optional[Callable[[str, str, str, AssetVariant, int, 'AssetSpec'], Sequence[str]]] = None

    def build_url(self, asset_type: str, asset_id: str, variant: AssetVariant) -> str:
        if self.url_builder:
            return self.url_builder(asset_type, asset_id, variant, self)
        return (
            'http://prd-game-a-granbluefantasy.akamaized.net/assets_en/'
            f'img/sp/assets/{asset_type}/{self.section}/{asset_id}{variant.suffix}.{self.extension}'
        )

    def canonical_section_label(self) -> str:
        return self.section_label or self.section

    def build_canonical_name(self, asset_type: str, asset_id: str, variant: AssetVariant) -> str:
        if self.canonical_name_builder:
            return self.canonical_name_builder(asset_type, asset_id, variant, self)
        return "{0} {1} {2}{3}.{4}".format(
            asset_type.capitalize(),
            self.canonical_section_label(),
            asset_id,
            variant.suffix,
            self.extension
        )

    def build_other_names(
        self,
        asset_name: str,
        asset_type: str,
        canonical_name: str,
        variant: AssetVariant,
        variant_count: int,
    ):
        if self.other_names_builder:
            names = self.other_names_builder(
                asset_name,
                asset_type,
                canonical_name,
                variant,
                variant_count,
                self
            )
            return list(names) if names else []

        other_names = []
        base_name = f"{asset_name}{self.filename_suffix}.{self.extension}"
        if (variant_count < 2) or (variant.label in ('', 'A')):
            other_names.append(base_name)

        if variant_count > 1 and variant.label:
            spacer = ' ' if (self.filename_suffix == '' and variant.label) else ''
            other_names.append(
                f"{asset_name}{self.filename_suffix}{spacer}{variant.label}.{self.extension}"
            )

        return other_names


@dataclass(frozen=True)
class DuplicateFamilyRule:
    name: str
    pattern: re.Pattern
    id_parts: Sequence[str]
    signature_parts: Sequence[str]
    validation_url_builder: Optional[Callable[[re.Match], str]] = None


@dataclass(frozen=True)
class DuplicateFamilyMatch:
    rule: DuplicateFamilyRule
    file_name: str
    id_token: str
    family_signature: tuple[str, ...]
    page_name: str
    match_obj: re.Match

class WikiImages(object):
    # Map of item upload types to CDN path segments
    ITEM_SINGLE_TYPE_PATHS = {
        "article": "article",
        "normal": "normal",
        "recycling": "recycling",
        "skillplus": "skillplus",
        "evolution": "evolution",
        "lottery": "lottery",
        "ticket": "ticket",
        "campaign": "campaign",
        "npcarousal": "npcarousal",
        "memorial": "memorial",
        "npcaugment": "npcaugment",
        "set": "set",
    }

    # (variant, redirect_suffix, cdn_variant_segment)
    ITEM_VARIANT_CONFIG = {
        "__default__": [
            ("s", "square", None),
            ("m", "icon", None),
        ],
        # Ticket squares live at /ticket/{id}.jpg (no /s/), but icons are still under /m/.
        "ticket": [
            ("s", "square", ""),
            ("m", "icon", "m"),
        ],
        "campaign": [
            ("s", "square", ""),
        ],
    }

    EVENT_BANNER_MAX_INDEX = 20
    EVENT_TEASER_MAX_INDEX = 20
    DUPLICATE_FAMILY_RULES = (
        DuplicateFamilyRule(
            name='weapon_sp',
            pattern=re.compile(
                r'^File:Weapon sp (?P<id>[A-Za-z0-9]+)(?: (?P<slot>[12]))?\.(?P<ext>[A-Za-z0-9]+)$'
            ),
            id_parts=('id',),
            signature_parts=('slot', 'ext'),
        ),
        DuplicateFamilyRule(
            name='space_asset',
            pattern=re.compile(
                r'^File:(?P<kind>Weapon|Summon|Npc|Artifact) (?P<section>[A-Za-z0-9_]+) '
                r'(?P<id>[A-Za-z0-9]+)(?P<suffix>(?:_[A-Za-z0-9]+)*)\.(?P<ext>[A-Za-z0-9]+)$'
            ),
            id_parts=('id',),
            signature_parts=('kind', 'section', 'suffix', 'ext'),
        ),
        DuplicateFamilyRule(
            name='npc_special',
            pattern=re.compile(
                r'^File:(?P<kind>Npc)_(?P<section>my|result_lvup)_(?P<id>[A-Za-z0-9]+)'
                r'(?P<suffix>(?:_[A-Za-z0-9]+)*)\.(?P<ext>[A-Za-z0-9]+)$'
            ),
            id_parts=('id',),
            signature_parts=('kind', 'section', 'suffix', 'ext'),
        ),
        DuplicateFamilyRule(
            name='npc_f_skin',
            pattern=re.compile(
                r'^File:(?P<prefix>npc_f_skin)_(?P<id>[A-Za-z0-9]+)'
                r'(?P<suffix>(?:_[A-Za-z0-9]+)*)\.(?P<ext>[A-Za-z0-9]+)$'
            ),
            id_parts=('id',),
            signature_parts=('prefix', 'suffix', 'ext'),
        ),
        DuplicateFamilyRule(
            name='npc_s_skin',
            pattern=re.compile(
                r'^File:(?P<prefix>npc_s_skin)_(?P<id>[A-Za-z0-9]+)'
                r'(?P<suffix>(?:_[A-Za-z0-9]+)*)\.(?P<ext>[A-Za-z0-9]+)$'
            ),
            id_parts=('id',),
            signature_parts=('prefix', 'suffix', 'ext'),
        ),
        DuplicateFamilyRule(
            name='skycompass_character',
            pattern=re.compile(
                r'^File:characters_1138x1138_(?P<id>[A-Za-z0-9]+)(?P<suffix>(?:_[A-Za-z0-9]+)*)'
                r'\.(?P<ext>[A-Za-z0-9]+)$'
            ),
            id_parts=('id',),
            signature_parts=('suffix', 'ext'),
        ),
        DuplicateFamilyRule(
            name='item',
            pattern=re.compile(
                r'^File:item_(?P<item_type>[A-Za-z0-9]+)_(?P<variant>[A-Za-z0-9]+)_(?P<id>[A-Za-z0-9]+)'
                r'\.(?P<ext>[A-Za-z0-9]+)$'
            ),
            id_parts=('id',),
            signature_parts=('item_type', 'variant', 'ext'),
        ),
        DuplicateFamilyRule(
            name='sized_asset',
            pattern=re.compile(
                r'^File:(?P<prefix>familiar_[ms]|shield_[ms]|Bullet_[ms]|cosmetic_[ms])_'
                r'(?P<id>[A-Za-z0-9]+)\.(?P<ext>[A-Za-z0-9]+)$'
            ),
            id_parts=('id',),
            signature_parts=('prefix', 'ext'),
        ),
        DuplicateFamilyRule(
            name='vyrnsampo_asset',
            pattern=re.compile(
                r'^File:(?P<prefix>vyrnsampo_character_thumb|vyrnsampo_character_detail|'
                r'vyrnsampo_character_special_skill_label)_(?P<id>[A-Za-z0-9]+)'
                r'(?P<suffix>(?:_[A-Za-z0-9]+)*)\.(?P<ext>[A-Za-z0-9]+)$'
            ),
            id_parts=('id',),
            signature_parts=('prefix', 'suffix', 'ext'),
        ),
        DuplicateFamilyRule(
            name='event_banner',
            pattern=re.compile(
                r'^File:(?P<id>[A-Za-z0-9_]+)_banner_event_(?P<banner_kind>notice|start)_'
                r'(?P<index>\d+)\.(?P<ext>[A-Za-z0-9]+)$'
            ),
            id_parts=('id',),
            signature_parts=('banner_kind', 'index', 'ext'),
        ),
        DuplicateFamilyRule(
            name='event_summon_qm',
            pattern=re.compile(
                r'^File:summon_qm_(?P<id>[A-Za-z0-9_]+)_'
                r'(?P<variant>vhard|vhard_1|vhard_2|ex|ex_1|ex_2|high_1|high_2)\.(?P<ext>[A-Za-z0-9]+)$'
            ),
            id_parts=('id',),
            signature_parts=('variant', 'ext'),
        ),
        DuplicateFamilyRule(
            name='event_summon_high',
            pattern=re.compile(
                r'^File:summon_(?P<id>[A-Za-z0-9_]+)_(?P<variant>high)\.(?P<ext>[A-Za-z0-9]+)$'
            ),
            id_parts=('id',),
            signature_parts=('variant', 'ext'),
        ),
        DuplicateFamilyRule(
            name='event_qm_hell',
            pattern=re.compile(
                r'^File:qm_(?P<id>[A-Za-z0-9_]+)_(?P<variant>hell)\.(?P<ext>[A-Za-z0-9]+)$'
            ),
            id_parts=('id',),
            signature_parts=('variant', 'ext'),
        ),
        DuplicateFamilyRule(
            name='event_quest_assets',
            pattern=re.compile(
                r'^File:quest_assets_(?P<id>[A-Za-z0-9_]+)_'
                r'(?P<variant>free_proud|free_proud_1|free_proud_2)\.(?P<ext>[A-Za-z0-9]+)$'
            ),
            id_parts=('id',),
            signature_parts=('variant', 'ext'),
        ),
        DuplicateFamilyRule(
            name='class_gendered_asset',
            pattern=re.compile(
                r'^File:(?P<prefix>leader_sd|leader_s|leader_job_change|leader_jobm|leader_p|leader_jobon_z|'
                r'leader_jlon|leader_result_ml|leader_result|leader_pm|leader_raid_log|'
                r'leader_raid_normal|leader_talk|leader_quest|leader_coop|leader_btn|'
                r'leader_my|leader_zenith|leader_t)_(?P<id_num>[A-Za-z0-9]+)_(?P<abbr>[A-Za-z0-9]+)'
                r'(?P<suffix>_[01]_01)\.(?P<ext>[A-Za-z0-9]+)$'
            ),
            id_parts=('id_num', 'abbr'),
            signature_parts=('prefix', 'suffix', 'ext'),
        ),
        DuplicateFamilyRule(
            name='summon_archive_asset',
            pattern=re.compile(
                r'^File:archives_summons_(?P<id>[A-Za-z0-9]+)(?P<suffix>_detail_l)\.(?P<ext>[A-Za-z0-9]+)$'
            ),
            id_parts=('id',),
            signature_parts=('suffix', 'ext'),
        ),
        DuplicateFamilyRule(
            name='class_simple_asset',
            pattern=re.compile(
                r'^File:(?P<prefix>leader_sd_m|leader_m|leader_s|icon_job|leader_jobtree|'
                r'job_name_tree_l|job_name|job_list|Assets_skin_name)_(?P<id>[A-Za-z0-9]+)'
                r'(?P<suffix>(?:_[A-Za-z0-9]+)*)\.(?P<ext>[A-Za-z0-9]+)$'
            ),
            id_parts=('id',),
            signature_parts=('prefix', 'suffix', 'ext'),
        ),
        DuplicateFamilyRule(
            name='jobs_skycompass',
            pattern=re.compile(
                r'^File:jobs_1138x1138_(?P<id>[A-Za-z0-9]+)(?P<suffix>_[01])\.(?P<ext>[A-Za-z0-9]+)$'
            ),
            id_parts=('id',),
            signature_parts=('suffix', 'ext'),
        ),
    )

    def __init__(self):
        """
        Initialize wiki connection and DB.
        GBFWiki.login() is responsible for reading environment variables
        or falling back to the local config file when necessary.
        """
        try:
            self.wiki = GBFWiki.login()
        except Exception as exc:
            # If login fails, re-raise with more context so logs are clearer.
            raise RuntimeError(f"Failed to initialize GBFWiki login: {exc}") from exc

        # DB object unchanged
        self.db = GBFDB()

        # MITM root: prefer MITM_ROOT env var, otherwise fall back to GBFWiki.mitmpath()
        self.mitm_root = MITM_ROOT or GBFWiki.mitmpath()

        # Other settings
        if IMAGE_PROBE_DELAY is not None:
            self.delay = IMAGE_PROBE_DELAY
        elif not os.environ.get("PROXY_URL") and LOCAL_IMAGE_PROBE_DELAY is not None:
            self.delay = LOCAL_IMAGE_PROBE_DELAY
        else:
            self.delay = 25

        self._proxy_url = os.environ.get("PROXY_URL")
        self._use_paced_local_downloads = (
            not self._proxy_url
            and (
                IMAGE_PROBE_DELAY is not None
                or LOCAL_IMAGE_PROBE_DELAY is not None
            )
        )

    def _sleep_after_failed_probe(self):
        if self.delay and self.delay > 0:
            time.sleep(self.delay)

    async def _async_sleep_after_failed_probe(self):
        if self.delay and self.delay > 0:
            await asyncio.sleep(self.delay)

    def _item_variant_specs(self, item_type: str):
        """
        Return variant configuration tuples for the given item type.
        Falls back to the default config when no specific mapping exists.
        """
        key = (item_type or "").lower()
        return self.ITEM_VARIANT_CONFIG.get(key, self.ITEM_VARIANT_CONFIG["__default__"])

    def _perform_wiki_action_with_retry(self, action, *args, max_attempts=5, **kwargs):
        """
        Execute a wiki action with basic retry handling for rate limits.

        Args:
            action (callable): Bound mwclient method to execute.
            max_attempts (int): Maximum number of attempts before giving up.
        """
        attempt = 0
        while True:
            try:
                return action(*args, **kwargs)
            except APIError as api_error:
                if api_error.code in ("ratelimited", "maxlag") and attempt < max_attempts:
                    attempt += 1
                    wait_time = max(5, min(60, self.delay * attempt))
                    print(f'API limit "{api_error.code}" encountered. Retrying in {wait_time}s (attempt {attempt}/{max_attempts})...')
                    time.sleep(wait_time)
                    continue
                raise

    def _normalize_duplicate_family_part(self, value):
        return (value or '').strip().lower()

    def _is_npc_duplicate_family(self, rule, match):
        if rule.name in ('npc_special', 'npc_f_skin', 'npc_s_skin'):
            return True
        return (
            rule.name == 'space_asset'
            and self._normalize_duplicate_family_part(match.groupdict().get('kind')) == 'npc'
        )

    def _normalize_npc_duplicate_suffix(self, suffix):
        normalized = self._normalize_duplicate_family_part(suffix)
        if not normalized:
            return normalized

        normalized = re.sub(
            r'^(?P<base>_(?:01|81|91))_0(?P<rest>(?:_[a-z0-9]+)*)$',
            lambda match: match.group('base') + (match.group('rest') or ''),
            normalized,
        )
        return normalized

    def _normalize_duplicate_signature_value(self, rule, match, part):
        value = self._normalize_duplicate_family_part(match.group(part))
        if part == 'suffix' and self._is_npc_duplicate_family(rule, match):
            return self._normalize_npc_duplicate_suffix(value)
        return value

    def _duplicate_canonical_preference_key(self, match):
        if not self._is_npc_duplicate_family(match.rule, match.match_obj):
            return (0, self._normalize_duplicate_family_part(match.page_name))

        raw_suffix = self._normalize_duplicate_family_part(match.match_obj.groupdict().get('suffix'))
        normalized_suffix = self._normalize_npc_duplicate_suffix(raw_suffix)
        if raw_suffix and raw_suffix != normalized_suffix:
            return (1, self._normalize_duplicate_family_part(match.page_name))
        return (0, self._normalize_duplicate_family_part(match.page_name))

    def _build_duplicate_family_id_token(self, match, id_parts):
        values = [str(match.group(part)).strip() for part in id_parts if match.group(part)]
        return '_'.join(values)

    def _build_duplicate_family_signature(self, rule, match):
        signature = [rule.name]
        for part in rule.signature_parts:
            signature.append(self._normalize_duplicate_signature_value(rule, match, part))
        return tuple(signature)

    def _match_duplicate_family(self, file_name):
        for rule in self.DUPLICATE_FAMILY_RULES:
            matched = rule.pattern.match(file_name)
            if not matched:
                continue

            id_token = self._build_duplicate_family_id_token(matched, rule.id_parts)
            if not id_token:
                continue

            return DuplicateFamilyMatch(
                rule=rule,
                file_name=file_name,
                id_token=id_token,
                family_signature=self._build_duplicate_family_signature(rule, matched),
                page_name=file_name[5:] if file_name.startswith('File:') else file_name,
                match_obj=matched,
            )

        return None

    def _duplicate_id_sort_key(self, id_token):
        normalized = self._normalize_duplicate_family_part(id_token)
        if normalized.isdigit():
            return (0, int(normalized), normalized)
        return (1, normalized)

    def _build_duplicate_validation_url(self, file_name):
        family_match = self._match_duplicate_family(file_name)
        if family_match and family_match.rule.name == 'weapon_sp':
            slot = family_match.family_signature[1]
            variant_suffix = f'_{slot}' if slot else ''
            return (
                'http://prd-game-a-granbluefantasy.akamaized.net/assets_en/'
                f'img/sp/cjs/{family_match.id_token}{variant_suffix}.{family_match.family_signature[2]}'
            )

        dupe_match = re.match(r'^File:(Summon|Weapon) ([a-z]+) (\d+)\.([a-z]+)$', file_name)
        if dupe_match is None:
            return None

        return (
            'http://prd-game-a-granbluefantasy.akamaized.net/assets_en/'
            'img/sp/assets/{0}/{1}/{2}.{3}'
        ).format(
            'summon' if dupe_match.group(1) == 'Summon' else 'weapon',
            dupe_match.group(2),
            dupe_match.group(3),
            dupe_match.group(4)
        )

    def _select_canonical_duplicate_by_family(self, requested_file_name, duplicates):
        requested_match = self._match_duplicate_family(requested_file_name)
        if requested_match is None:
            return None

        candidate_matches = []
        for duplicate in duplicates:
            duplicate_match = self._match_duplicate_family(duplicate.name)
            if duplicate_match is None:
                continue
            if duplicate_match.rule.name != requested_match.rule.name:
                continue
            if duplicate_match.family_signature != requested_match.family_signature:
                continue
            candidate_matches.append((duplicate_match, duplicate))

        if not candidate_matches:
            return None

        candidate_matches.sort(
            key=lambda entry: (
                self._duplicate_id_sort_key(entry[0].id_token),
                self._duplicate_canonical_preference_key(entry[0]),
            )
        )
        best_existing_match, best_existing_page = candidate_matches[0]
        requested_key = (
            self._duplicate_id_sort_key(requested_match.id_token),
            self._duplicate_canonical_preference_key(requested_match),
        )
        best_existing_key = (
            self._duplicate_id_sort_key(best_existing_match.id_token),
            self._duplicate_canonical_preference_key(best_existing_match),
        )
        if requested_key < best_existing_key:
            return requested_match, best_existing_page, True

        return best_existing_match, best_existing_page, False

    def get_image(self, url, max_retries=3):
        print('Downloading {0}...'.format(url))
        headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/141.0.0.0 Safari/537.36'
        }
        proxies = {}
        if self._proxy_url:
            proxies = {"http": self._proxy_url, "https": self._proxy_url}
            
        for attempt in range(max_retries + 1):
            try:
                req = requests.get(url, headers=headers, proxies=proxies, stream=True, timeout=30)
                
                if req.status_code == 200:
                    io = BytesIO(req.content)
                    io.seek(0)

                    sha1 = hashlib.sha1()
                    while True:
                        data = io.read(1024)
                        if not data:
                            break
                        sha1.update(data)
                    sha1 = sha1.hexdigest()
                    size = len(req.content)

                    io.seek(0)
                    return True, sha1, size, io
                    
                elif req.status_code == 404:
                    print(f'Download failed (404 Not Found): {url}')
                    self._sleep_after_failed_probe()
                    return False, "", 0, False
                    
                elif req.status_code in [407, 429, 500, 502, 503, 504]:
                    if attempt < max_retries:
                        wait_time = (2 ** attempt)  # Exponential backoff: 1s, 2s, 4s
                        print(f'Download failed ({req.status_code}), retrying in {wait_time}s... (attempt {attempt + 1}/{max_retries})')
                        time.sleep(wait_time)
                        continue
                    else:
                        print(f'Download failed ({req.status_code}) after {max_retries} retries: {url}')
                        self._sleep_after_failed_probe()
                        return False, "", 0, False
                else:
                    print(f'Download failed ({req.status_code}): {url}')
                    self._sleep_after_failed_probe()
                    return False, "", 0, False
                    
            except requests.exceptions.RequestException as e:
                if attempt < max_retries:
                    wait_time = (2 ** attempt)
                    print(f'Download error, retrying in {wait_time}s... (attempt {attempt + 1}/{max_retries}): [network error]')
                    time.sleep(wait_time)
                    continue
                else:
                    print(f'Download failed after {max_retries} retries: [network error]')
                    self._sleep_after_failed_probe()
                    return False, "", 0, False
                    
        return False, "", 0, False

    async def get_images_concurrent(self, urls, timeout_seconds=None, progress_interval=25):
        """Download multiple images concurrently from GBF CDN using proxy with optional timeout handling."""
        headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/141.0.0.0 Safari/537.36'
        }
        
        async def download_single(session, url, max_retries=3):
            for attempt in range(max_retries + 1):
                try:
                    print(f'Downloading {url}...' + (f' (retry {attempt})' if attempt > 0 else ''))
                    kwargs = {'headers': headers}
                    if self._proxy_url:
                        kwargs['proxy'] = self._proxy_url
                    
                    async with session.get(url, **kwargs) as response:
                        if response.status == 200:
                            content = await response.read()
                            io_obj = BytesIO(content)
                            io_obj.seek(0)
                            
                            sha1 = hashlib.sha1()
                            sha1.update(content)
                            sha1_hex = sha1.hexdigest()
                            size = len(content)
                            
                            io_obj.seek(0)
                            return url, True, sha1_hex, size, io_obj
                            
                        elif response.status == 404:
                            # Don't retry 404s - file genuinely doesn't exist
                            print(f'Download failed (404 Not Found): {url}')
                            await self._async_sleep_after_failed_probe()
                            return url, False, "", 0, False
                            
                        elif response.status in [407, 429, 500, 502, 503, 504]:
                            # Retry these errors: proxy auth, rate limit, server errors
                            if attempt < max_retries:
                                wait_time = (2 ** attempt)  # Exponential backoff: 1s, 2s, 4s
                                print(f'Download failed ({response.status}), retrying in {wait_time}s... (attempt {attempt + 1}/{max_retries})')
                                await asyncio.sleep(wait_time)
                                continue
                            else:
                                print(f'Download failed ({response.status}) after {max_retries} retries: {url}')
                                await self._async_sleep_after_failed_probe()
                                return url, False, "", 0, False
                        else:
                            # Other HTTP errors - don't retry
                            print(f'Download failed ({response.status}): {url}')
                            await self._async_sleep_after_failed_probe()
                            return url, False, "", 0, False
                            
                except Exception as e:
                    # Network/timeout errors - retry
                    if attempt < max_retries:
                        wait_time = (2 ** attempt)
                        print(f'Download error, retrying in {wait_time}s... (attempt {attempt + 1}/{max_retries}): [network error]')
                        await asyncio.sleep(wait_time)
                        continue
                    else:
                        print(f'Download failed after {max_retries} retries: [network error]')
                        await self._async_sleep_after_failed_probe()
                        return url, False, "", 0, False
                        
            return url, False, "", 0, False
        
        timeout = aiohttp.ClientTimeout(total=30)
        connector = aiohttp.TCPConnector()
        async with aiohttp.ClientSession(connector=connector, timeout=timeout) as session:
            task_map = {asyncio.create_task(download_single(session, url)): url for url in urls}
            completed_results = []
            completed_count = 0
            loop = asyncio.get_running_loop()
            deadline = loop.time() + timeout_seconds if timeout_seconds else None
            
            while task_map:
                wait_timeout = None
                if deadline is not None:
                    remaining = deadline - loop.time()
                    if remaining <= 0:
                        break
                    wait_timeout = remaining
                
                done, _ = await asyncio.wait(
                    task_map.keys(),
                    timeout=wait_timeout,
                    return_when=asyncio.FIRST_COMPLETED
                )
                
                if not done:
                    # Timed out without any completed tasks
                    break
                
                for finished in done:
                    url = task_map.pop(finished, None)
                    if url is None:
                        continue
                    
                    try:
                        result = finished.result()
                    except Exception as exc:
                        print(f"Concurrent download task error for {url}: {exc}")
                        result = (url, False, "", 0, False)
                    
                    completed_results.append(result)
                    completed_count += 1
                    if (
                        progress_interval
                        and completed_count % progress_interval == 0
                    ):
                        print(f"Progress: {completed_count}/{len(urls)} downloads completed...")
            
            pending_urls = list(task_map.values())
            if pending_urls and timeout_seconds:
                print(
                    f"Concurrent download timeout reached; "
                    f"{len(pending_urls)} URLs remain for sequential fallback."
                )
            
            # Cancel any pending tasks and allow them to clean up
            for pending_task in list(task_map.keys()):
                pending_task.cancel()
                try:
                    await pending_task
                except asyncio.CancelledError:
                    pass
            
            return completed_results, pending_urls

    def check_image(self, name, sha1, size, io, other_names, description_text=None):
        print(f"[WIKI] Starting check_image for: {name}")
        true_name = name.capitalize()
        file_name = 'File:' + true_name
        
        print(f"[WIKI] Checking for duplicates: {true_name}")
        try:
            wiki_duplicates = list(self.wiki.allimages(minsize=size, maxsize=size, sha1=sha1))
            print(f"Found {len(wiki_duplicates)} potential duplicates")
        except Exception as e:
            print(f"Wiki API call failed: {e}")
            wiki_duplicates = []

        # filter out archived images...
        duplicates = []
        for wiki_duplicate in wiki_duplicates:
            imageinfo = getattr(wiki_duplicate, 'imageinfo', {}) or {}
            image_url = imageinfo.get('url')
            if image_url and ('/archive/' in image_url):
                continue
            duplicates.append(wiki_duplicate)

        canonical_duplicate = self._select_canonical_duplicate_by_family(file_name, duplicates)
        if canonical_duplicate is not None:
            canonical_duplicate_match, canonical_duplicate_page, prefer_requested_title = canonical_duplicate
            canonical_name = canonical_duplicate_match.page_name
            if canonical_duplicate_page.page_title.strip().lower() == true_name.replace("_", " ").lower():
                return file_name[5:]

            if prefer_requested_title:
                backlinks = canonical_duplicate_page.backlinks(filterredir='redirects')
                print(
                    'Page "{0}" is duplicate of "{1}" within family "{2}", '
                    'moving to preferred canonical title...'.format(
                        canonical_duplicate_page.name,
                        file_name,
                        canonical_duplicate_match.rule.name
                    )
                )
                self._perform_wiki_action_with_retry(
                    canonical_duplicate_page.move,
                    file_name,
                    reason='Batch upload file name',
                )
                self.investigate_backlinks(backlinks, canonical_duplicate_page.name, file_name)
                return file_name[5:]

            print(
                'Page "{0}" is duplicate of "{1}" within family "{2}", using stable canonical...'.format(
                    file_name,
                    canonical_duplicate_page.name,
                    canonical_duplicate_match.rule.name
                )
            )
            self.check_redirect(canonical_duplicate_page.name, file_name)
            return canonical_name

        if len(duplicates) > 1:
            # just don't handle too many duplicates
            print('Too many duplicates for: {0}'.format(true_name))
            return False
        elif len(duplicates) == 1:
            dupe = duplicates[0]
            # have we uploaded it already?
            if dupe.page_title.strip().lower() == true_name.replace("_", " ").lower():
                return file_name[5:]

            # check if this is a weapon image duplicate
            # for weapons we store images on the lowest ID as dupes are common
            #if file_name.startswith('File:Summon ') or file_name.startswith('File:Weapon '):
            dupe_match = re.match(r'^File:(Summon|Weapon) ([a-z]+) (\d+)\.([a-z]+)$', dupe.name)
            if dupe_match != None:
                dupe_number = int(dupe_match.group(3))
                file_number = int(re.search(r'\d+', file_name).group(0))
                if dupe_number < file_number:
                    url = self._build_duplicate_validation_url(dupe.name)

                    success, dupe_sha1, dupe_size, dupe_io = self.get_image(url) if url else (False, "", 0, False)
                    if success and (dupe_sha1 == sha1) and (dupe_size == size):
                        print('Page "{0}" is dupe of "{1}", using lower number...'.format(dupe.name, file_name))
                        self.check_redirect(dupe.name, file_name)
                        return dupe.name[5:]

            # move if single duplicate
            backlinks = dupe.backlinks(filterredir='redirects')
            print('Moving page "{0}" to "{1}" with redirect...'.format(dupe.name, file_name))
            self._perform_wiki_action_with_retry(dupe.move, file_name, reason='Batch upload file name')
            self.investigate_backlinks(backlinks, dupe.name, file_name)
            return file_name[5:]
        else:
            # check related names and if any is a file move it to intended name
            if len(other_names) > 0:
                for other_name in other_names:
                    page = self.wiki.pages["File:"+other_name]
                    if page.exists and not page.redirect:
                        backlinks = page.backlinks(filterredir='redirects')
                        print('Moving page "{0}" to "{1}" with redirect before upload...'.format(page.name, file_name))
                        self._perform_wiki_action_with_retry(page.move, file_name, 'Batch upload file name (sha1 not found)')

                        self.investigate_backlinks(backlinks, page.name, file_name)

            # upload image
            print('Uploading "{0}"...'.format(file_name))
            io.seek(0)
            try:
                upload_kwargs = {"filename": true_name, "ignore": True}
                if description_text:
                    upload_kwargs["description"] = description_text
                if description_text and description_text.startswith('[[Category:'):
                    upload_kwargs["description"] = UPLOAD_COMMENT
                response = self.wiki.upload(io, **upload_kwargs)
                print(response['result'] + ': ' + name)
            except Exception as e:
                print(f'Upload failed for {file_name}: {e}')
                return False
            if response['result'] == 'Warning':
                return False
            return True

    def _find_image_duplicates_by_hash(self, sha1, size):
        """Return non-archived File: pages that match the given sha1/size."""
        try:
            wiki_duplicates = list(self.wiki.allimages(minsize=size, maxsize=size, sha1=sha1))
            print(f"Found {len(wiki_duplicates)} potential duplicates (raw) for hash {sha1}")
        except Exception as e:
            print(f"Wiki API call failed while checking duplicates: {e}")
            return []

        duplicates = []
        for wiki_duplicate in wiki_duplicates:
            imageinfo = getattr(wiki_duplicate, 'imageinfo', {}) or {}
            image_url = imageinfo.get('url')
            if image_url and ('/archive/' in image_url):
                continue
            duplicates.append(wiki_duplicate)

        print(f"{len(duplicates)} non-archived duplicates remain after filtering")
        return duplicates

    @staticmethod
    def _parse_image_timestamp(imageinfo):
        """Parse an imageinfo timestamp into a sortable value; fallback to name ordering."""
        timestamp = (imageinfo or {}).get('timestamp')
        if not timestamp:
            return None
        try:
            # MediaWiki returns ISO 8601 with Z; normalize for datetime parsing.
            normalized = timestamp.replace('Z', '+00:00')
            return datetime.fromisoformat(normalized)
        except Exception:
            return None

    def _redirect_banner_to_earliest_duplicate(self, requested_name, duplicates):
        """
        When multiple duplicates already exist, pick the earliest upload and
        create a redirect from the requested banner name to that canonical file.
        """
        if not duplicates:
            return None, []

        def sort_key(dup):
            info = getattr(dup, 'imageinfo', {}) or {}
            parsed_ts = self._parse_image_timestamp(info)
            # If timestamp is missing, place it at end but keep deterministic ordering by name.
            return (parsed_ts or datetime.max, dup.name.lower())

        sorted_dupes = sorted(duplicates, key=sort_key)
        canonical_page = sorted_dupes[0]
        canonical_name = canonical_page.name
        if canonical_name.lower().startswith('file:'):
            canonical_name = canonical_name[5:]

        requested_clean = requested_name.replace("_", " ").strip()
        canonical_clean = canonical_name.replace("_", " ").strip()

        if requested_clean.lower() != canonical_clean.lower():
            print(
                f"Multiple duplicates found for {requested_name}. "
                f"Redirecting to earliest upload: {canonical_name}"
            )
            try:
                self.check_file_redirect(canonical_name, requested_name)
            except Exception as exc:
                print(f"Failed to create redirect {requested_name} -> {canonical_name}: {exc}")
                return None, []
        else:
            print(
                f"Multiple duplicates found for {requested_name}, "
                f"requested name already matches earliest upload."
            )

        all_duplicate_names = []
        for dup in sorted_dupes:
            dup_name = dup.name
            if dup_name.lower().startswith('file:'):
                dup_name = dup_name[5:]
            all_duplicate_names.append(dup_name)

        return canonical_name, all_duplicate_names

    def investigate_backlinks(self, backlinks, source, target):
        print('Investigating "{0}" backlinks...'.format(source))
        source = source.replace("_", " ")
        target = target.replace("_", " ")

        for backlink in backlinks:
            print('Found backlink "{0}"...'.format(backlink.name))
            depths = backlink.backlinks(filterredir='redirects')
            backlink_text = self.db.pagetext(self.wiki, backlink.name, backlink.revision)
            if backlink_text.startswith('#REDIRECT [[File:'):
                new_text = '#REDIRECT [[{0}]]'.format(target)
                if (new_text != backlink_text):
                    print('Updating backlink "{0}" to point directly to "{1}"...'.format(backlink.name, target))
                    backlink.save(new_text, summary='Resolving double redirects.')
            self.investigate_backlinks(depths, backlink.name, target)

    def check_image_categories(self, name, categories):
        """
        Ensure the uploaded file page includes the requested categories.

        Older code tried to edit via self.wiki.images[], but mwclient only
        supports saves through the page object. This uses the File: page to
        append any missing category tags.
        """
        page_name = f'File:{name}'
        page = self.wiki.pages[page_name]
        if not page.exists or page.redirect:
            return

        pagetext = page.text()
        new_text = pagetext
        for category in categories:
            category_text = f'[[Category:{category}]]'
            if category_text not in new_text:
                separator = '\n' if new_text and not new_text.endswith('\n') else ''
                new_text = f'{new_text}{separator}{category_text}'

        if pagetext != new_text:
            print(f'Updating categories for {name}...')
            self._perform_wiki_action_with_retry(
                page.save,
                new_text,
                summary='Batch image categories',
            )

    def check_file_redirect(self, redirect_to, redirect_from):
        redirect_to = redirect_to[0].upper() + redirect_to[1:]
        redirect_from = redirect_from[0].upper() + redirect_from[1:]
        return self.check_redirect('File:'+redirect_to, 'File:'+redirect_from)

    def check_redirect(self, redirect_to, redirect_from):
        redirect_from = redirect_from.replace("_", " ")
        redirect_to = redirect_to.replace("_", " ")
        page = self.wiki.pages[redirect_from]
        if page.exists:
            page_text = self.db.pagetext(self.wiki, page.name, page.revision)
        else:
            page_text = ''

        new_text = '#REDIRECT [[{0}]]'.format(redirect_to)

        #image = self.wiki.images[redirect_from[5:]]
        #if image.exists and not (redirect_to[5:].replace(" ", "_") in image.imageinfo['url']):
        #    print('Deleting image at "{0}" to redirect to "{1}"...'.format(redirect_from, redirect_to))
        #    image.delete(reason='Duplicate file to be replaced by redirect.')
        #    page.save(new_text, summary='', minor=False, bot=True)
        #el
        if page_text != new_text:
            print('Updating "{0}" to redirect to "{1}"...'.format(redirect_from, redirect_to))
            page.save(new_text, summary='', minor=False, bot=True)

    def check_file_double_redirect(self, true_name):
        self.check_double_redirect("File:" + true_name[0].upper() + true_name[1:])

    def check_double_redirect(self, true_name):
        true_name = true_name.replace("_", " ")
        page = self.wiki.pages[true_name]
        backlinks = page.backlinks(filterredir='redirects')
        for backlink in backlinks:
            depths = backlink.backlinks(filterredir='redirects')
            for depth in depths:
                depth_text = self.db.pagetext(self.wiki, depth.name, depth.revision)
                if depth_text.startswith('#REDIRECT [[File:'):
                    new_text = '#REDIRECT [[{0}]]'.format(page.name)
                    if (new_text != depth_text):
                        print('Updating double redirect "{0}" to point directly to "{1}"...'.format(depth.name, page.name))
                        depth.save(new_text, summary='Resolving double redirects.')

    def upload_status_icons(self, status_identifier, max_index=None):
        """
        Upload status icons from the GBF CDN to the wiki.

        Args:
            status_identifier (str): Base identifier for the status icon.
                Examples: "1438", "status_1438", "1438_#", "1438#".
            max_index (int|None): Optional maximum index for ranged uploads.
                Defaults to 10 when status_identifier ends with "#".
        """
        if not status_identifier:
            print('Status identifier is required.')
            return

        def ensure_status_prefix(raw_value):
            return raw_value if raw_value.startswith('status_') else f'status_{raw_value}'

        url_template = (
            'https://prd-game-a-granbluefantasy.akamaized.net/assets_en/'
            'img/sp/ui/icon/status/x64/{0}.png'
        )

        ranged = False
        base_identifier = status_identifier

        if status_identifier.endswith('#'):
            ranged = True
            base_identifier = status_identifier[:-1]
            if base_identifier.endswith('_'):
                base_identifier = base_identifier[:-1]

        base_identifier = ensure_status_prefix(base_identifier)

        if ranged:
            if max_index is None:
                max_index = 10

            try:
                max_index = int(max_index)
            except (TypeError, ValueError):
                print(f'Invalid maximum index "{max_index}" provided.')
                return

            if max_index < 1:
                print('Maximum index must be at least 1.')
                return

            attempt_indices = [None]
            attempt_indices.extend(range(1, max_index + 1))
        else:
            if max_index is not None:
                print('Ignoring extra max index argument for single status upload.')
            attempt_indices = [None]

        def emit_status(stage, **kwargs):
            if hasattr(self, '_status_callback'):
                self._status_callback(stage, **kwargs)

        def status_download_and_upload(identifier, report_missing=True):
            url = url_template.format(identifier)
            success, sha1, size, io = self.get_image(url)
            if not success:
                if report_missing:
                    print(f'Skipping {identifier}.png (download failed).')
                return False, None

            true_name = f'{identifier}.png'
            other_names = []
            check_image_result = self.check_image(
                true_name,
                sha1,
                size,
                io,
                other_names,
            )

            if check_image_result is False:
                print(f'Checking image {true_name} failed! Skipping...')
                return False, None
            elif check_image_result is not True:
                true_name = check_image_result

            self.check_image_categories(true_name, ['Status Icons'])

            for other_name in other_names:
                self.check_file_redirect(true_name, other_name)

            time.sleep(self.delay)
            self.check_file_double_redirect(true_name)
            return True, true_name

        total = len(attempt_indices)
        processed = 0
        uploaded = 0
        failed = 0

        emit_status(
            "processing",
            processed=processed,
            uploaded=uploaded,
            failed=failed,
            total=total,
            current_identifier=None,
        )

        for index in attempt_indices:
            current_identifier = base_identifier
            success = False
            final_name = None

            if index is None:
                success, final_name = status_download_and_upload(base_identifier)
            else:
                primary_identifier = f'{base_identifier}_{index}'
                success, final_name = status_download_and_upload(primary_identifier, report_missing=False)
                if success:
                    current_identifier = primary_identifier
                else:
                    secondary_identifier = f'{base_identifier}{index}'
                    current_identifier = secondary_identifier
                    success, final_name = status_download_and_upload(secondary_identifier, report_missing=False)
                    if not success:
                        print(
                            f'No status icon found for index {index} '
                            f'({primary_identifier}.png or {secondary_identifier}.png).'
                        )

            processed += 1
            if success:
                uploaded += 1
            else:
                failed += 1

            emit_kwargs = {
                "processed": processed,
                "uploaded": uploaded,
                "failed": failed,
                "total": total,
                "current_identifier": current_identifier,
            }
            if success and final_name:
                emit_kwargs["downloaded_file"] = final_name

            emit_status("processing", **emit_kwargs)

        emit_status(
            "completed",
            processed=processed,
            uploaded=uploaded,
            failed=failed,
            total=total,
            current_identifier=None,
        )

    def upload_gacha_banners(self, banner_identifier, max_index=12):
        """
        Upload gacha banner images from the GBF CDN to the wiki.

        Args:
            banner_identifier (str): Text between `banner_` and the trailing index in the CDN file name.
            max_index (int): Highest sequential index to attempt. Defaults to 12.
        """
        banner_identifier = normalize_banner_id_input(banner_identifier)

        if not banner_identifier:
            print('Banner identifier is required.')
            return

        if max_index is None:
            max_index = 12

        try:
            max_index = int(max_index)
        except (TypeError, ValueError):
            print(f'Invalid maximum index "{max_index}" provided.')
            return

        if max_index < 1:
            print('Maximum index must be at least 1.')
            return

        def emit_status(stage, **kwargs):
            if hasattr(self, '_status_callback'):
                self._status_callback(stage, **kwargs)

        base_url = (
            'https://prd-game-a-granbluefantasy.akamaized.net/assets_en/'
            'img/sp/banner/gacha/{0}'
        )

        total = max_index
        processed = 0
        uploaded = 0
        failed = 0
        banner_duplicates = []

        emit_status(
            "processing",
            processed=processed,
            uploaded=uploaded,
            failed=failed,
            total=total,
            current_identifier=None,
            banner_duplicates=banner_duplicates,
        )

        for index in range(1, max_index + 1):
            file_name = f'banner_{banner_identifier}_{index}.png'
            url = base_url.format(file_name)
            print(f'Downloading {url}...')
            success, sha1, size, io_obj = self.get_image(url)

            current_identifier = file_name
            final_name = None
            if not success:
                print(f'No gacha banner found for index {index} ({file_name}).')
                failed += 1
            else:
                try:
                    duplicates = self._find_image_duplicates_by_hash(sha1, size)
                    if len(duplicates) > 1:
                        final_name, duplicate_names = self._redirect_banner_to_earliest_duplicate(
                            file_name, duplicates
                        )
                        if final_name:
                            banner_duplicates.append(
                                {
                                    "requested": file_name,
                                    "canonical": final_name,
                                    "duplicates": duplicate_names,
                                }
                            )
                            uploaded += 1
                            time.sleep(self.delay)
                            self.check_file_double_redirect(final_name)
                        else:
                            print(f'Failed to resolve duplicates for {file_name}.')
                            failed += 1
                    else:
                        other_names = []
                        check_result = self.check_image(file_name, sha1, size, io_obj, other_names)
                        if check_result is False:
                            print(f'Checking image {file_name} failed! Skipping...')
                            failed += 1
                        else:
                            final_name = file_name if check_result is True else check_result
                            for other_name in other_names:
                                self.check_file_redirect(final_name, other_name)
                            time.sleep(self.delay)
                            self.check_file_double_redirect(final_name)
                            uploaded += 1
                except Exception as exc:
                    print(f'Error while processing {file_name}: {exc}')
                    failed += 1

            processed += 1

            emit_kwargs = {
                "processed": processed,
                "uploaded": uploaded,
                "failed": failed,
                "total": total,
                "current_identifier": current_identifier,
                "banner_duplicates": banner_duplicates,
            }
            if final_name:
                emit_kwargs["downloaded_file"] = final_name

            emit_status("processing", **emit_kwargs)

        emit_status(
            "completed",
            processed=processed,
            uploaded=uploaded,
            failed=failed,
            total=total,
            current_identifier=None,
            banner_duplicates=banner_duplicates,
        )

    def _process_item_variant(self, item_type, item_id, item_name, variant, redirect_suffix, cdn_variant=None):
        """
        Download and upload a single item variant image for the given CDN item type.

        Returns:
            tuple[str, int, int]: Final image name (or attempted canonical name on failure),
            upload count increment, duplicate count increment.
        """
        item_type = item_type.lower()
        path_segment = self.ITEM_SINGLE_TYPE_PATHS.get(item_type, item_type)
        if not path_segment:
            raise ValueError('Item type (CDN folder) is required for single-item uploads.')

        cdn_variant = variant if cdn_variant is None else cdn_variant
        cdn_variant = cdn_variant.strip('/') if cdn_variant else ''
        variant_path = f'{cdn_variant}/' if cdn_variant else ''
        url = (
            'https://prd-game-a-granbluefantasy.akamaized.net/assets_en/'
            f'img/sp/assets/item/{path_segment}/{variant_path}{item_id}.jpg'
        )
        true_name = f'item_{item_type}_{variant}_{item_id}.jpg'
        other_names = [f'{item_name} {redirect_suffix}.jpg']

        print(
            f'Downloading {url} for item "{item_name}" '
            f'(type: {item_type}, variant: {variant}, ID: {item_id})...'
        )
        success, sha1, size, io_obj = self.get_image(url)
        if not success:
            print(f'Failed to download item image for ID {item_id} variant {variant} ({item_type}).')
            return true_name, 0, 0

        check_image_result = self.check_image(true_name, sha1, size, io_obj, other_names)
        if check_image_result is False:
            print(f'Upload validation failed for {true_name}.')
            return true_name, 0, 0

        if check_image_result is True:
            final_name = true_name
            uploaded_increment = 1
            duplicate_increment = 0
        else:
            final_name = check_image_result
            uploaded_increment = 0
            duplicate_increment = 1

        for other_name in other_names:
            self.check_file_redirect(final_name, other_name)

        time.sleep(self.delay)
        self.check_file_double_redirect(final_name)

        return final_name, uploaded_increment, duplicate_increment

    def _process_item_article_variant(self, item_id, item_name, variant, redirect_suffix):
        """Backward-compatible wrapper for article item uploads."""
        return self._process_item_variant("article", item_id, item_name, variant, redirect_suffix)

    def upload_item_article_images(self, page):
        """
        Upload item images for each {{Item}} template found on a page.

        Args:
            page (mwclient.page.Page): Wiki page containing {{Item}} templates.
        """
        print(f'Processing item templates on page "{page.name}"...')
        pagetext = page.text()
        wikicode = mwparserfromhell.parse(pagetext)
        templates = wikicode.filter_templates()

        items = []
        seen_ids = set()

        for template in templates:
            template_name = template.name.strip()
            if template_name.lower() != 'item':
                continue

            item_id = None
            item_name = None
            item_type = 'article'

            for param in template.params:
                param_name = param.name.strip()
                value = str(param.value).strip()

                if param_name == 'id' and value:
                    match = re.match(r'^{{{id\|([^}]+)}}}$', value)
                    if match:
                        value = match.group(1)
                    item_id = value
                elif param_name == 'name' and value:
                    item_name = mwparserfromhell.parse(value).strip_code().strip()
                elif param_name == 'item_type' and value:
                    item_type = mwparserfromhell.parse(value).strip_code().strip().lower()

            if not item_id:
                print('Skipping template without id parameter.')
                continue

            if item_id in seen_ids:
                print(f'Skipping duplicate item id "{item_id}".')
                continue

            if not item_name:
                print(f'Skipping item id "{item_id}" without name parameter.')
                continue

            item_type = (item_type or 'article').lower()
            if item_type not in self.ITEM_SINGLE_TYPE_PATHS:
                print(f'Skipping item id "{item_id}" with unsupported type "{item_type}".')
                continue

            seen_ids.add(item_id)
            items.append({'id': item_id, 'name': item_name, 'type': item_type})

        if not items:
            print('No valid {{Item}} templates found.')
            return

        total_variants = sum(
            len(self._item_variant_specs(item["type"]))
            for item in items
        )
        processed_variants = 0
        successful_uploads = 0
        duplicate_matches = 0

        for item in items:
            item_id = item['id']
            item_name = item['name']
            item_type = item['type']

            variant_specs = self._item_variant_specs(item_type)

            for variant, redirect_suffix, cdn_variant in variant_specs:
                current_image, uploaded_increment, duplicate_increment = self._process_item_variant(
                    item_type, item_id, item_name, variant, redirect_suffix, cdn_variant
                )

                processed_variants += 1
                successful_uploads += uploaded_increment
                duplicate_matches += duplicate_increment

                if hasattr(self, '_status_callback'):
                    self._status_callback(
                        "processing",
                        processed=processed_variants,
                        total=total_variants,
                        current_image=current_image,
                        item_type=item_type,
                    )

        if hasattr(self, '_status_callback'):
            self._status_callback(
                "completed",
                processed=processed_variants,
                uploaded=successful_uploads,
                duplicates=duplicate_matches,
                total_urls=total_variants,
                item_type=None,
            )

    def upload_single_item_images(self, item_type, item_id, item_name):
        """
        Upload item images for a single CDN item category using its id and display name.

        Args:
            item_type (str): CDN path segment (e.g. article, normal, recycling).
            item_id (str): Unique identifier for the item on the CDN.
            item_name (str): Display name to use for redirect creation.
        """
        item_type = str(item_type).strip().lower()

        item_id = str(item_id).strip()
        item_name = mwparserfromhell.parse(item_name).strip_code().strip()

        if not item_id:
            print('Item id is required for single item upload.')
            return

        if not item_name:
            print('Item name is required for single item upload.')
            return

        print(f'Processing single item "{item_name}" (type: {item_type}, ID: {item_id})...')

        variant_specs = self._item_variant_specs(item_type)
        if not variant_specs:
            print(f'No configured variants for item type "{item_type}".')
            return

        total_variants = len(variant_specs)
        processed_variants = 0
        successful_uploads = 0
        duplicate_matches = 0

        for variant, redirect_suffix, cdn_variant in variant_specs:
            current_image, uploaded_increment, duplicate_increment = self._process_item_variant(
                item_type, item_id, item_name, variant, redirect_suffix, cdn_variant
            )

            processed_variants += 1
            successful_uploads += uploaded_increment
            duplicate_matches += duplicate_increment

            if hasattr(self, '_status_callback'):
                self._status_callback(
                    "processing",
                    processed=processed_variants,
                    total=total_variants,
                    current_image=current_image,
                    item_type=item_type,
                )

        if hasattr(self, '_status_callback'):
            self._status_callback(
                "completed",
                processed=processed_variants,
                uploaded=successful_uploads,
                duplicates=duplicate_matches,
                total_urls=total_variants,
                item_type=item_type,
            )

    def upload_single_item_article_images(self, item_id, item_name):
        """Backward-compatible wrapper for article single-item uploads."""
        self.upload_single_item_images("article", item_id, item_name)

    def upload_enemy_images(self, enemy_id):
        """
        Upload S and M variants for an enemy icon and create canonical + redirect files.
        """
        enemy_id = str(enemy_id).strip()
        if not enemy_id:
            raise ValueError("Enemy id is required for enemy uploads.")

        variants = [
            {
                "variant": "s",
                "url": (
                    "https://prd-game-a1-granbluefantasy.akamaized.net/assets_en/"
                    f"img/sp/assets/enemy/s/{enemy_id}.png"
                ),
                "canonical": f"enemy_s_{enemy_id}.png",
                "redirect": f"enemy_Icon_{enemy_id}_S.png",
            },
            {
                "variant": "m",
                "url": (
                    "https://prd-game-a1-granbluefantasy.akamaized.net/assets_en/"
                    f"img/sp/assets/enemy/m/{enemy_id}.png"
                ),
                "canonical": f"enemy_m_{enemy_id}.png",
                "redirect": f"enemy_Icon_{enemy_id}_M.png",
            },
        ]

        total = len(variants)
        processed = 0
        uploaded = 0
        duplicates = 0
        failed = 0
        file_entries = []

        if hasattr(self, "_status_callback"):
            self._status_callback(
                "processing",
                processed=processed,
                total=total,
                current_image=None,
                enemy_id=enemy_id,
            )

        for entry in variants:
            url = entry["url"]
            canonical_name = entry["canonical"]
            redirect_name = entry["redirect"]

            print(f'Downloading enemy variant "{entry["variant"]}" ({url})...')
            success, sha1, size, io_obj = self.get_image(url)
            if not success:
                failed += 1
                print(f'Failed to download {canonical_name}.')
                continue

            processed += 1

            if hasattr(self, "_status_callback"):
                self._status_callback(
                    "processing",
                    processed=processed,
                    total=total,
                    current_image=canonical_name,
                    enemy_id=enemy_id,
                )

            other_names = [redirect_name]
            check_image_result = self.check_image(canonical_name, sha1, size, io_obj, other_names)
            if check_image_result is True:
                uploaded += 1
            elif check_image_result is False:
                failed += 1
                print(f'Upload validation failed for {canonical_name}.')
                continue
            else:
                duplicates += 1
                canonical_name = check_image_result

            file_entries.append(
                {
                    "variant": entry["variant"],
                    "canonical": canonical_name,
                    "redirect": redirect_name,
                }
            )

            self.check_file_redirect(canonical_name, redirect_name)
            time.sleep(self.delay)
            self.check_file_double_redirect(canonical_name)

        if hasattr(self, "_status_callback"):
            self._status_callback(
                "completed",
                processed=processed,
                uploaded=uploaded,
                duplicates=duplicates,
                failed=failed,
                total=total,
                total_urls=processed,
                enemy_id=enemy_id,
            )

        return {
            "processed": processed,
            "uploaded": uploaded,
            "duplicates": duplicates,
            "failed": failed,
            "total": total,
            "total_urls": processed,
            "files": file_entries,
            "enemy_id": enemy_id,
        }

    def check_manatura(self, page):
        """
        Upload manatura familiar images for each {{Class/Manatura/Row}} template found on a page.
        
        For each template, extracts id and name parameters and uploads:
        - familiar_m_{id}.jpg (from /familiar/m/{id}.jpg) with redirect {name}_icon.jpg
        - familiar_s_{id}.jpg (from /familiar/s/{id}.jpg) with redirect {name}_square.jpg
        
        Args:
            page (mwclient.page.Page): Wiki page containing {{Class/Manatura/Row}} templates.
        """
        print(f'Processing Class/Manatura/Row templates on page "{page.name}"...')
        pagetext = page.text()
        wikicode = mwparserfromhell.parse(pagetext)
        templates = wikicode.filter_templates()

        manatura_rows = []
        seen_ids = set()

        for template in templates:
            template_name = template.name.strip()
            if template_name.lower() != 'class/manatura/row':
                continue

            row_id = None
            row_name = None

            for param in template.params:
                param_name = param.name.strip().lower()
                raw_value = str(param.value).strip()
                clean_value = mwparserfromhell.parse(raw_value).strip_code().strip()

                if param_name == 'id' and clean_value:
                    row_id = clean_value
                elif param_name == 'name' and clean_value:
                    row_name = clean_value

            if not row_id:
                print('Skipping template without id parameter.')
                continue

            if row_id in seen_ids:
                print(f'Skipping duplicate manatura id "{row_id}".')
                continue

            if not row_name:
                print(f'Skipping manatura id "{row_id}" without name parameter.')
                continue

            seen_ids.add(row_id)
            manatura_rows.append({
                'id': row_id,
                'name': row_name,
            })

        if not manatura_rows:
            print('No {{Class/Manatura/Row}} templates found with valid id and name parameters.')
            return

        total_rows = len(manatura_rows)
        total_variants = total_rows * 2  # m and s variants per row
        processed = 0
        uploaded = 0
        duplicates = 0
        failed = 0

        if hasattr(self, "_status_callback"):
            self._status_callback(
                "processing",
                processed=processed,
                total=total_variants,
                current_image=None,
            )

        for row in manatura_rows:
            row_id = row['id']
            row_name = row['name']

            variants = [
                {
                    "variant": "m",
                    "url": (
                        "https://prd-game-a-granbluefantasy.akamaized.net/assets_en/"
                        f"img/sp/assets/familiar/m/{row_id}.jpg"
                    ),
                    "canonical": f"familiar_m_{row_id}.jpg",
                    "redirect": f"{row_name}_icon.jpg",
                },
                {
                    "variant": "s",
                    "url": (
                        "https://prd-game-a-granbluefantasy.akamaized.net/assets_en/"
                        f"img/sp/assets/familiar/s/{row_id}.jpg"
                    ),
                    "canonical": f"familiar_s_{row_id}.jpg",
                    "redirect": f"{row_name}_square.jpg",
                },
            ]

            for entry in variants:
                url = entry["url"]
                canonical_name = entry["canonical"]
                redirect_name = entry["redirect"]

                print(f'Downloading manatura "{row_name}" variant "{entry["variant"]}" ({url})...')
                success, sha1, size, io_obj = self.get_image(url)
                if not success:
                    failed += 1
                    print(f'Failed to download {canonical_name}.')
                    processed += 1
                    if hasattr(self, "_status_callback"):
                        self._status_callback(
                            "processing",
                            processed=processed,
                            total=total_variants,
                            current_image=canonical_name,
                        )
                    continue

                processed += 1

                if hasattr(self, "_status_callback"):
                    self._status_callback(
                        "processing",
                        processed=processed,
                        total=total_variants,
                        current_image=canonical_name,
                    )

                other_names = [redirect_name]
                check_image_result = self.check_image(canonical_name, sha1, size, io_obj, other_names)
                if check_image_result is True:
                    uploaded += 1
                elif check_image_result is False:
                    failed += 1
                    print(f'Upload validation failed for {canonical_name}.')
                    continue
                else:
                    duplicates += 1
                    canonical_name = check_image_result

                self.check_file_redirect(canonical_name, redirect_name)
                time.sleep(self.delay)
                self.check_file_double_redirect(canonical_name)

        if hasattr(self, "_status_callback"):
            self._status_callback(
                "completed",
                processed=processed,
                uploaded=uploaded,
                duplicates=duplicates,
                failed=failed,
                total=total_variants,
            )

        print(f'\nManatura upload complete: {uploaded} uploaded, {duplicates} duplicates, {failed} failed')
        return {
            "uploaded": uploaded,
            "duplicates": duplicates,
            "failed": failed,
            "total": total_variants,
        }

    def check_shield(self, page):
        """
        Upload shield images for each {{Class/Shields/Row}} template found on a page.
        
        For each template, extracts id and name parameters and uploads:
        - shield_m_{id}.jpg (from /shield/m/{id}.jpg) with redirect {name}_icon.jpg
        - shield_s_{id}.jpg (from /shield/s/{id}.jpg) with redirect {name}_square.jpg
        
        Args:
            page (mwclient.page.Page): Wiki page containing {{Class/Shields/Row}} templates.
        """
        print(f'Processing Class/Shields/Row templates on page "{page.name}"...')
        pagetext = page.text()
        wikicode = mwparserfromhell.parse(pagetext)
        templates = wikicode.filter_templates()

        shield_rows = []
        seen_ids = set()

        for template in templates:
            template_name = template.name.strip()
            if template_name.lower() != 'class/shields/row':
                continue

            row_id = None
            row_name = None

            for param in template.params:
                param_name = param.name.strip().lower()
                raw_value = str(param.value).strip()
                clean_value = mwparserfromhell.parse(raw_value).strip_code().strip()

                if param_name == 'id' and clean_value:
                    row_id = clean_value
                elif param_name == 'name' and clean_value:
                    row_name = clean_value

            if not row_id:
                print('Skipping template without id parameter.')
                continue

            if row_id in seen_ids:
                print(f'Skipping duplicate shield id "{row_id}".')
                continue

            if not row_name:
                print(f'Skipping shield id "{row_id}" without name parameter.')
                continue

            seen_ids.add(row_id)
            shield_rows.append({
                'id': row_id,
                'name': row_name,
            })

        if not shield_rows:
            print('No {{Class/Shields/Row}} templates found with valid id and name parameters.')
            return

        total_rows = len(shield_rows)
        total_variants = total_rows * 2  # m and s variants per row
        processed = 0
        uploaded = 0
        duplicates = 0
        failed = 0

        if hasattr(self, "_status_callback"):
            self._status_callback(
                "processing",
                processed=processed,
                total=total_variants,
                current_image=None,
            )

        for row in shield_rows:
            row_id = row['id']
            row_name = row['name']

            variants = [
                {
                    "variant": "m",
                    "url": (
                        "https://prd-game-a-granbluefantasy.akamaized.net/assets_en/"
                        f"img/sp/assets/shield/m/{row_id}.jpg"
                    ),
                    "canonical": f"shield_m_{row_id}.jpg",
                    "redirect": f"{row_name}_icon.jpg",
                },
                {
                    "variant": "s",
                    "url": (
                        "https://prd-game-a-granbluefantasy.akamaized.net/assets_en/"
                        f"img/sp/assets/shield/s/{row_id}.jpg"
                    ),
                    "canonical": f"shield_s_{row_id}.jpg",
                    "redirect": f"{row_name}_square.jpg",
                },
            ]

            for entry in variants:
                url = entry["url"]
                canonical_name = entry["canonical"]
                redirect_name = entry["redirect"]

                print(f'Downloading shield "{row_name}" variant "{entry["variant"]}" ({url})...')
                success, sha1, size, io_obj = self.get_image(url)
                if not success:
                    failed += 1
                    print(f'Failed to download {canonical_name}.')
                    processed += 1
                    if hasattr(self, "_status_callback"):
                        self._status_callback(
                            "processing",
                            processed=processed,
                            total=total_variants,
                            current_image=canonical_name,
                        )
                    continue

                processed += 1

                if hasattr(self, "_status_callback"):
                    self._status_callback(
                        "processing",
                        processed=processed,
                        total=total_variants,
                        current_image=canonical_name,
                    )

                other_names = [redirect_name]
                check_image_result = self.check_image(canonical_name, sha1, size, io_obj, other_names)
                if check_image_result is True:
                    uploaded += 1
                elif check_image_result is False:
                    failed += 1
                    print(f'Upload validation failed for {canonical_name}.')
                    continue
                else:
                    duplicates += 1
                    canonical_name = check_image_result

                self.check_file_redirect(canonical_name, redirect_name)
                time.sleep(self.delay)
                self.check_file_double_redirect(canonical_name)

        if hasattr(self, "_status_callback"):
            self._status_callback(
                "completed",
                processed=processed,
                uploaded=uploaded,
                duplicates=duplicates,
                failed=failed,
                total=total_variants,
            )

        print(f'\nShield upload complete: {uploaded} uploaded, {duplicates} duplicates, {failed} failed')
        return {
            "uploaded": uploaded,
            "duplicates": duplicates,
            "failed": failed,
            "total": total_variants,
        }

    def check_bullet(self, page):
        """
        Upload bullet images for each {{Bullet}} template found on a page.

        For each template, extracts id and name parameters and uploads:
        - Bullet_m_{id}.jpg (from /bullet/m/{id}.jpg) with redirect {name}_icon.jpg
        - Bullet_s_{id}.jpg (from /bullet/s/{id}.jpg) with redirect {name}_square.jpg
        """
        print(f'Processing Bullet templates on page "{page.name}"...')
        pagetext = page.text()
        wikicode = mwparserfromhell.parse(pagetext)
        templates = wikicode.filter_templates()

        bullets = []
        seen_ids = set()

        for template in templates:
            template_name = template.name.strip().lower()
            if template_name != 'bullet':
                continue

            bullet_id = None
            bullet_name = None

            for param in template.params:
                param_name = str(param.name).strip().lower()
                raw_value = str(param.value).strip()
                if not raw_value:
                    continue

                clean_value = mwparserfromhell.parse(raw_value).strip_code().strip()

                if param_name == 'id' and clean_value:
                    match = re.match(r'^{{{id\|([^}]+)}}}$', raw_value)
                    if match:
                        clean_value = match.group(1).strip()
                    bullet_id = clean_value
                elif param_name == 'name' and clean_value:
                    bullet_name = clean_value

            if not bullet_id:
                print('Skipping {{Bullet}} template without id parameter.')
                continue

            if bullet_id in seen_ids:
                print(f'Skipping duplicate bullet id "{bullet_id}".')
                continue

            if not bullet_name:
                print(f'Skipping bullet id "{bullet_id}" without name parameter.')
                continue

            seen_ids.add(bullet_id)
            bullets.append({'id': bullet_id, 'name': bullet_name})

        if not bullets:
            print('No {{Bullet}} templates found with valid id and name parameters.')
            return

        total_rows = len(bullets)
        total_variants = total_rows * 2
        processed = 0
        uploaded = 0
        duplicates = 0
        failed = 0

        if hasattr(self, "_status_callback"):
            self._status_callback(
                "processing",
                processed=processed,
                total=total_variants,
                current_image=None,
            )

        for bullet in bullets:
            bullet_id = bullet['id']
            bullet_name = bullet['name']

            variants = [
                {
                    "variant": "m",
                    "url": (
                        "https://prd-game-a1-granbluefantasy.akamaized.net/assets_en/"
                        f"img/sp/assets/bullet/m/{bullet_id}.jpg"
                    ),
                    "canonical": f"Bullet_m_{bullet_id}.jpg",
                    "redirect": f"{bullet_name}_icon.jpg",
                },
                {
                    "variant": "s",
                    "url": (
                        "https://prd-game-a1-granbluefantasy.akamaized.net/assets_en/"
                        f"img/sp/assets/bullet/s/{bullet_id}.jpg"
                    ),
                    "canonical": f"Bullet_s_{bullet_id}.jpg",
                    "redirect": f"{bullet_name}_square.jpg",
                },
            ]

            for entry in variants:
                url = entry["url"]
                canonical_name = entry["canonical"]
                redirect_name = entry["redirect"]

                print(f'Downloading bullet "{bullet_name}" variant "{entry["variant"]}" ({url})...')
                success, sha1, size, io_obj = self.get_image(url)
                processed += 1

                if not success:
                    failed += 1
                    print(f'Failed to download {canonical_name}.')
                    if hasattr(self, "_status_callback"):
                        self._status_callback(
                            "processing",
                            processed=processed,
                            total=total_variants,
                            current_image=canonical_name,
                        )
                    continue

                if hasattr(self, "_status_callback"):
                    self._status_callback(
                        "processing",
                        processed=processed,
                        total=total_variants,
                        current_image=canonical_name,
                    )

                other_names = [redirect_name]
                check_image_result = self.check_image(canonical_name, sha1, size, io_obj, other_names)
                if check_image_result is True:
                    uploaded += 1
                elif check_image_result is False:
                    failed += 1
                    print(f'Upload validation failed for {canonical_name}.')
                    continue
                else:
                    duplicates += 1
                    canonical_name = check_image_result

                self.check_file_redirect(canonical_name, redirect_name)
                time.sleep(self.delay)
                self.check_file_double_redirect(canonical_name)

        if hasattr(self, "_status_callback"):
            self._status_callback(
                "completed",
                processed=processed,
                uploaded=uploaded,
                duplicates=duplicates,
                failed=failed,
                total=total_variants,
            )

        print(f'\nBullet upload complete: {uploaded} uploaded, {duplicates} duplicates, {failed} failed')
        return {
            "uploaded": uploaded,
            "duplicates": duplicates,
            "failed": failed,
            "total": total_variants,
        }

    def check_advyrnture_gear(self, page):
        """
        Upload Advyrnture gear images for each {{Advyrnture/Cosmetic/Row}} template.

        For each unique `id`, uploads:
        - cosmetic_m_{id}.jpg (from /cosmetic/m/{id}.jpg)
        - cosmetic_s_{id}.jpg (from /cosmetic/s/{id}.jpg)

        When `name` is present, also creates:
        - {name} (Advyrnture) icon.jpg
        - {name} (Advyrnture) square.jpg
        - page redirect {name} (Advyrnture) -> Let's Go, Advyrnturers!#{name}
        """
        print(f'Processing Advyrnture/Cosmetic/Row templates on page "{page.name}"...')
        pagetext = page.text()
        wikicode = mwparserfromhell.parse(pagetext)
        templates = wikicode.filter_templates()

        cosmetics_by_id = {}

        for template in templates:
            template_name = template.name.strip().lower()
            if template_name != 'advyrnture/cosmetic/row':
                continue

            cosmetic_id = None
            cosmetic_name = None

            for param in template.params:
                param_name = str(param.name).strip().lower()
                raw_value = str(param.value).strip()
                if not raw_value:
                    continue

                clean_value = mwparserfromhell.parse(raw_value).strip_code().strip()

                if param_name == 'id' and clean_value:
                    cosmetic_id = clean_value
                elif param_name == 'name':
                    cosmetic_name = clean_value

            if not cosmetic_id:
                print('Skipping {{Advyrnture/Cosmetic/Row}} template without id parameter.')
                continue

            existing = cosmetics_by_id.get(cosmetic_id)
            if existing:
                if not existing['name'] and cosmetic_name:
                    existing['name'] = cosmetic_name
                    print(
                        f'Using later non-blank name "{cosmetic_name}" '
                        f'for duplicate Advyrnture gear id "{cosmetic_id}".'
                    )
                else:
                    print(f'Skipping duplicate Advyrnture gear id "{cosmetic_id}".')
                continue

            cosmetics_by_id[cosmetic_id] = {'id': cosmetic_id, 'name': cosmetic_name}

        cosmetics = list(cosmetics_by_id.values())

        if not cosmetics:
            print('No {{Advyrnture/Cosmetic/Row}} templates found with valid id parameters.')
            return

        total_rows = len(cosmetics)
        total_variants = total_rows * 2
        processed = 0
        uploaded = 0
        duplicates = 0
        failed = 0

        if hasattr(self, "_status_callback"):
            self._status_callback(
                "processing",
                processed=processed,
                total=total_variants,
                current_image=None,
            )

        for cosmetic in cosmetics:
            cosmetic_id = cosmetic['id']
            cosmetic_name = cosmetic['name']

            if cosmetic_name:
                redirect_from = f"{cosmetic_name} (Advyrnture)"
                redirect_target = f"Let's Go, Advyrnturers!#{cosmetic_name}"
                try:
                    print(f"Ensuring page redirect: {redirect_from} -> {redirect_target}")
                    self.check_redirect(redirect_target, redirect_from)
                except Exception as redirect_error:  # pragma: no cover - best-effort logging
                    print(f"Failed to create {redirect_from} redirect: {redirect_error}")
            else:
                print(f'Skipping redirects for Advyrnture gear id "{cosmetic_id}" because name is blank.')

            variants = [
                {
                    "variant": "m",
                    "url": (
                        "https://prd-game-a-granbluefantasy.akamaized.net/assets_en/"
                        f"img/sp/assets/item/cosmetic/m/{cosmetic_id}.jpg"
                    ),
                    "canonical": f"cosmetic_m_{cosmetic_id}.jpg",
                    "redirect": (
                        f"{cosmetic_name} (Advyrnture) icon.jpg" if cosmetic_name else None
                    ),
                },
                {
                    "variant": "s",
                    "url": (
                        "https://prd-game-a-granbluefantasy.akamaized.net/assets_en/"
                        f"img/sp/assets/item/cosmetic/s/{cosmetic_id}.jpg"
                    ),
                    "canonical": f"cosmetic_s_{cosmetic_id}.jpg",
                    "redirect": (
                        f"{cosmetic_name} (Advyrnture) square.jpg" if cosmetic_name else None
                    ),
                },
            ]

            for entry in variants:
                url = entry["url"]
                canonical_name = entry["canonical"]
                redirect_name = entry["redirect"]

                print(
                    f'Downloading Advyrnture gear "{cosmetic_id}" variant '
                    f'"{entry["variant"]}" ({url})...'
                )
                success, sha1, size, io_obj = self.get_image(url)
                processed += 1

                if not success:
                    failed += 1
                    print(f'Failed to download {canonical_name}.')
                    if hasattr(self, "_status_callback"):
                        self._status_callback(
                            "processing",
                            processed=processed,
                            total=total_variants,
                            current_image=canonical_name,
                        )
                    continue

                if hasattr(self, "_status_callback"):
                    self._status_callback(
                        "processing",
                        processed=processed,
                        total=total_variants,
                        current_image=canonical_name,
                    )

                other_names = [redirect_name] if redirect_name else []
                check_image_result = self.check_image(
                    canonical_name,
                    sha1,
                    size,
                    io_obj,
                    other_names,
                )
                if check_image_result is True:
                    uploaded += 1
                elif check_image_result is False:
                    failed += 1
                    print(f'Upload validation failed for {canonical_name}.')
                    continue
                else:
                    duplicates += 1
                    canonical_name = check_image_result

                if redirect_name:
                    self.check_file_redirect(canonical_name, redirect_name)
                    time.sleep(self.delay)
                self.check_file_double_redirect(canonical_name)

        if hasattr(self, "_status_callback"):
            self._status_callback(
                "completed",
                processed=processed,
                uploaded=uploaded,
                duplicates=duplicates,
                failed=failed,
                total=total_variants,
            )

        print(
            f'\nAdvyrnture gear upload complete: '
            f'{uploaded} uploaded, {duplicates} duplicates, {failed} failed'
        )
        return {
            "uploaded": uploaded,
            "duplicates": duplicates,
            "failed": failed,
            "total": total_variants,
        }

    def check_advyrnture_pal(self, page):
        """
        Upload Advyrnture pal icon images for each {{Advyrnture/Pal}} template.

        For each unique `id`, uploads:
        - vyrnsampo_character_thumb_{id}.jpg
          (from /vyrnsampo/assets/character/thumb/{id}.jpg)
        - vyrnsampo_character_thumb_{id}_friendship.jpg
          (from /vyrnsampo/assets/character/thumb/{id}_friendship.jpg)
        - vyrnsampo_character_thumb_{id}_fatigue.jpg
          (from /vyrnsampo/assets/character/thumb/{id}_fatigue.jpg)
        - vyrnsampo_character_detail_{id}.png
          (from /vyrnsampo/assets/character/detail/{id}.png)
        - vyrnsampo_character_detail_{id}_friendship.png
          (from /vyrnsampo/assets/character/detail/{id}_friendship.png)
        - Label {name}.png
          (from /vyrnsampo/assets/character/special_skill_label/{id}.png)

        When `name` is present, also creates:
        - {name} (Advyrnture) icon.jpg
        - {name} (Friendship) icon.jpg
        - {name} (Fatigue) icon.jpg
        - {name} (Advyrnture).png
        - {name} (Friendship).png
        """
        print(f'Processing Advyrnture/Pal templates on page "{page.name}"...')
        pagetext = page.text()
        wikicode = mwparserfromhell.parse(pagetext)
        templates = wikicode.filter_templates()

        pals_by_id = {}

        for template in templates:
            template_name = template.name.strip().lower()
            if template_name != 'advyrnture/pal':
                continue

            pal_id = None
            pal_name = None

            for param in template.params:
                param_name = str(param.name).strip().lower()
                raw_value = str(param.value).strip()
                if not raw_value:
                    continue

                clean_value = mwparserfromhell.parse(raw_value).strip_code().strip()

                if param_name == 'id' and clean_value:
                    pal_id = clean_value
                elif param_name == 'name':
                    pal_name = clean_value

            if not pal_id:
                print('Skipping {{Advyrnture/Pal}} template without id parameter.')
                continue

            existing = pals_by_id.get(pal_id)
            if existing:
                if not existing['name'] and pal_name:
                    existing['name'] = pal_name
                    print(
                        f'Using later non-blank name "{pal_name}" '
                        f'for duplicate Advyrnture pal id "{pal_id}".'
                    )
                else:
                    print(f'Skipping duplicate Advyrnture pal id "{pal_id}".')
                continue

            pals_by_id[pal_id] = {'id': pal_id, 'name': pal_name}

        pals = list(pals_by_id.values())

        if not pals:
            print('No {{Advyrnture/Pal}} templates found with valid id parameters.')
            return

        total_images = len(pals) * 6
        processed = 0
        uploaded = 0
        duplicates = 0
        failed = 0

        if hasattr(self, "_status_callback"):
            self._status_callback(
                "processing",
                processed=processed,
                total=total_images,
                current_image=None,
            )

        for pal in pals:
            pal_id = pal['id']
            pal_name = pal['name']
            variants = [
                {
                    "canonical": f"vyrnsampo_character_thumb_{pal_id}.jpg",
                    "url": (
                        "https://prd-game-a-granbluefantasy.akamaized.net/assets_en/"
                        f"img/sp/vyrnsampo/assets/character/thumb/{pal_id}.jpg"
                    ),
                    "redirect": (
                        f"{pal_name} (Advyrnture) icon.jpg" if pal_name else None
                    ),
                },
                {
                    "canonical": f"vyrnsampo_character_thumb_{pal_id}_friendship.jpg",
                    "url": (
                        "https://prd-game-a-granbluefantasy.akamaized.net/assets_en/"
                        f"img/sp/vyrnsampo/assets/character/thumb/{pal_id}_friendship.jpg"
                    ),
                    "redirect": (
                        f"{pal_name} (Friendship) icon.jpg" if pal_name else None
                    ),
                },
                {
                    "canonical": f"vyrnsampo_character_thumb_{pal_id}_fatigue.jpg",
                    "url": (
                        "https://prd-game-a-granbluefantasy.akamaized.net/assets_en/"
                        f"img/sp/vyrnsampo/assets/character/thumb/{pal_id}_fatigue.jpg"
                    ),
                    "redirect": (
                        f"{pal_name} (Fatigue) icon.jpg" if pal_name else None
                    ),
                },
                {
                    "canonical": f"vyrnsampo_character_detail_{pal_id}.png",
                    "url": (
                        "https://prd-game-a-granbluefantasy.akamaized.net/assets_en/"
                        f"img/sp/vyrnsampo/assets/character/detail/{pal_id}.png"
                    ),
                    "redirect": (
                        f"{pal_name} (Advyrnture).png" if pal_name else None
                    ),
                },
                {
                    "canonical": f"vyrnsampo_character_detail_{pal_id}_friendship.png",
                    "url": (
                        "https://prd-game-a-granbluefantasy.akamaized.net/assets_en/"
                        f"img/sp/vyrnsampo/assets/character/detail/{pal_id}_friendship.png"
                    ),
                    "redirect": (
                        f"{pal_name} (Friendship).png" if pal_name else None
                    ),
                },
                {
                    "canonical": f"Label {pal_name}.png" if pal_name else None,
                    "url": (
                        "https://prd-game-a-granbluefantasy.akamaized.net/assets_en/"
                        f"img/sp/vyrnsampo/assets/character/special_skill_label/{pal_id}.png"
                    ),
                    "redirect": None,
                },
            ]

            for variant in variants:
                canonical_name = variant["canonical"]
                redirect_name = variant["redirect"]
                url = variant["url"]

                if not canonical_name:
                    failed += 1
                    print(
                        f'Skipping Advyrnture pal special skill label for id "{pal_id}" '
                        f'because name is blank.'
                    )
                    processed += 1
                    if hasattr(self, "_status_callback"):
                        self._status_callback(
                            "processing",
                            processed=processed,
                            total=total_images,
                            current_image=f"special_skill_label/{pal_id}",
                        )
                    continue

                print(f'Downloading Advyrnture pal "{pal_id}" ({url})...')
                success, sha1, size, io_obj = self.get_image(url)
                processed += 1

                if not success:
                    failed += 1
                    print(f'Failed to download {canonical_name}.')
                    if hasattr(self, "_status_callback"):
                        self._status_callback(
                            "processing",
                            processed=processed,
                            total=total_images,
                            current_image=canonical_name,
                        )
                    continue

                if hasattr(self, "_status_callback"):
                    self._status_callback(
                        "processing",
                        processed=processed,
                        total=total_images,
                        current_image=canonical_name,
                    )

                other_names = [redirect_name] if redirect_name else []
                check_image_result = self.check_image(
                    canonical_name,
                    sha1,
                    size,
                    io_obj,
                    other_names,
                )
                if check_image_result is True:
                    uploaded += 1
                elif check_image_result is False:
                    failed += 1
                    print(f'Upload validation failed for {canonical_name}.')
                    continue
                else:
                    duplicates += 1
                    canonical_name = check_image_result

                if redirect_name:
                    self.check_file_redirect(canonical_name, redirect_name)
                    time.sleep(self.delay)
                else:
                    print(f'Skipping Advyrnture pal redirect for id "{pal_id}" because name is blank.')

                self.check_file_double_redirect(canonical_name)

        if hasattr(self, "_status_callback"):
            self._status_callback(
                "completed",
                processed=processed,
                uploaded=uploaded,
                duplicates=duplicates,
                failed=failed,
                total=total_images,
            )

        print(
            f'\nAdvyrnture pal upload complete: '
            f'{uploaded} uploaded, {duplicates} duplicates, {failed} failed'
        )
        return {
            "uploaded": uploaded,
            "duplicates": duplicates,
            "failed": failed,
            "total": total_images,
        }

    def _upload_event_banner_series(
        self,
        *,
        event_id,
        event_name,
        event_run,
        image_type,
        url_template,
        canonical_template,
        redirect_template,
        max_index=None,
    ):
        """
        Internal helper to upload indexed event banner assets that follow a consistent pattern.
        """
        event_id = str(event_id).strip().lower()
        if not event_id:
            raise ValueError("Event id is required for event banner uploads.")

        event_name = mwparserfromhell.parse(event_name).strip_code().strip()
        max_index = max_index or self.EVENT_BANNER_MAX_INDEX

        processed = 0
        uploaded = 0
        duplicates = 0
        failed = 0
        file_entries = []

        if hasattr(self, "_status_callback"):
            self._status_callback(
                "processing",
                processed=processed,
                total=max_index,
                current_image=None,
                event_id=event_id,
                event_run=event_run,
                image_type=image_type,
            )

        for index in range(1, max_index + 1):
            url = url_template.format(event_id=event_id, index=index)
            canonical_name = canonical_template.format(event_id=event_id, index=index)
            redirect_name = redirect_template.format(event_name=event_name, index=index)

            print(
                f'Downloading {image_type} banner #{index} for "{event_name}" '
                f'(event id: {event_id}, run: {event_run}) -> {url}'
            )

            success, sha1, size, io_obj = self.get_image(url)
            if not success:
                if index == 1:
                    print(f'No {image_type} banners found for event id "{event_id}".')
                break

            processed += 1

            if hasattr(self, "_status_callback"):
                self._status_callback(
                    "processing",
                    processed=processed,
                    total=max_index,
                    current_image=canonical_name,
                    event_id=event_id,
                    event_run=event_run,
                    image_type=image_type,
                )

            other_names = [redirect_name]
            check_image_result = self.check_image(canonical_name, sha1, size, io_obj, other_names)
            if check_image_result is True:
                uploaded += 1
            elif check_image_result is False:
                failed += 1
                print(f'Upload validation failed for {canonical_name}.')
                continue
            else:
                duplicates += 1
                canonical_name = check_image_result

            file_entries.append(
                {
                    "index": index,
                    "canonical": canonical_name,
                    "redirect": redirect_name,
                }
            )

            self.check_file_redirect(canonical_name, redirect_name)
            time.sleep(self.delay)
            self.check_file_double_redirect(canonical_name)

        if processed == 0:
            raise ValueError(f'No {image_type} banner images found for event id "{event_id}".')

        if hasattr(self, "_status_callback"):
            self._status_callback(
                "completed",
                processed=processed,
                uploaded=uploaded,
                duplicates=duplicates,
                failed=failed,
                total=max_index,
                total_urls=processed,
                event_id=event_id,
                event_run=event_run,
                image_type=image_type,
            )

        return {
            "processed": processed,
            "uploaded": uploaded,
            "duplicates": duplicates,
            "failed": failed,
            "total_urls": processed,
            "total": max_index,
            "files": file_entries,
            "event_id": event_id,
            "event_run": event_run,
            "image_type": image_type,
        }

    def upload_event_banners(self, event_id, event_name, event_run, max_index=None):
        """Upload banner_event_start_<index>.png assets."""
        return self._upload_event_banner_series(
            event_id=event_id,
            event_name=event_name,
            event_run=event_run,
            image_type="banner_start",
            url_template=(
                "https://prd-game-a1-granbluefantasy.akamaized.net/assets_en/"
                "img/sp/banner/events/{event_id}/banner_event_start_{index}.png"
            ),
            canonical_template="events_{event_id}_banner_event_start_{index}.png",
            redirect_template="banner_{event_name}_{index}.png",
            max_index=max_index,
        )

    def upload_event_notice_banners(self, event_id, event_name, event_run, max_index=None):
        """Upload banner_event_notice_<index>.png assets."""
        return self._upload_event_banner_series(
            event_id=event_id,
            event_name=event_name,
            event_run=event_run,
            image_type="banner_notice",
            url_template=(
                "https://prd-game-a1-granbluefantasy.akamaized.net/assets_en/"
                "img/sp/banner/events/{event_id}/banner_event_notice_{index}.png"
            ),
            canonical_template="events_{event_id}_banner_event_notice_{index}.png",
            redirect_template="banner_{event_name}_notice_{index}.png",
            max_index=max_index,
        )

    def upload_event_assets(self, event_id, event_name, asset_type, max_index=None):
        """
        Upload indexed event banner assets from the CDN.
        Supported asset_type values:
        - notice: event teaser notice banners
        - start: event start banners
        - guide: event guide panels
        - trailer_mp3: event trailer audio
        - voice_banner: event trailer banners
        - top: event teaser top image
        - raid_thumb: event raid thumbnails
        """
        event_id = str(event_id).strip().lower()
        if not event_id:
            raise ValueError("Event id is required for event uploads.")

        asset_type_key = (asset_type or "").strip().lower()
        if not asset_type_key:
            raise ValueError("Asset type is required for event uploads.")
        if asset_type_key not in {"notice", "start", "guide", "trailer_mp3", "voice_banner", "top", "raid_thumb"}:
            raise ValueError(f'Unsupported asset type "{asset_type}" for event uploads.')

        event_name = mwparserfromhell.parse(event_name).strip_code().strip()

        if max_index is None:
            if asset_type_key == "start":
                max_index = self.EVENT_BANNER_MAX_INDEX
            elif asset_type_key in {"top", "trailer_mp3"}:
                max_index = 1
            elif asset_type_key == "raid_thumb":
                max_index = 13
            else:
                max_index = self.EVENT_TEASER_MAX_INDEX
        try:
            max_index = int(max_index)
        except (TypeError, ValueError):
            raise ValueError(f'Invalid maximum index "{max_index}" provided.')
        if max_index < 1:
            raise ValueError("Maximum index must be at least 1.")

        processed = 0
        uploaded = 0
        duplicates = 0
        failed = 0
        file_entries = []

        if asset_type_key == "notice":
            asset_label = "event notice"
            not_found_message = f'No event notice banners found for event id "{event_id}".'
            url_template = (
                "https://prd-game-a-granbluefantasy.akamaized.net/assets_en/"
                "img/sp/banner/events/{event_id}/banner_event_notice_{index}.png"
            )
            canonical_template = "{event_id}_banner_event_notice_{index}.png"
            redirect_template = "banner_{event_name}_notice_{index}.png"
        elif asset_type_key == "start":
            asset_label = "event start"
            not_found_message = f'No event start banners found for event id "{event_id}".'
            url_template = (
                "https://prd-game-a-granbluefantasy.akamaized.net/assets_en/"
                "img/sp/banner/events/{event_id}/banner_event_start_{index}.png"
            )
            canonical_template = "{event_id}_banner_event_start_{index}.png"
            redirect_template = "banner_{event_name}_{index}.png"
        elif asset_type_key == "guide":
            asset_label = "event guide"
            not_found_message = f'No event guide panels found for event id "{event_id}".'
            guide_suffixes = ("", "_0", "_1")
        elif asset_type_key == "trailer_mp3":
            asset_label = "trailer mp3"
            not_found_message = f'No trailer mp3 found for event id "{event_id}".'
            url_template = (
                "https://prd-game-a-granbluefantasy.akamaized.net/assets_en/"
                "sound/voice/{event_id}.mp3"
            )
            canonical_name = f"{event_id}.mp3"
        elif asset_type_key == "voice_banner":
            asset_label = "voice banner"
            not_found_message = f'No voice banners found for event id "{event_id}".'
        elif asset_type_key == "top":
            asset_label = "event top"
            not_found_message = f'No top teaser asset found for event id "{event_id}".'
            url_template = (
                "https://prd-game-a-granbluefantasy.akamaized.net/assets_en/"
                "img/sp/event/{event_id}/assets/teaser/event_teaser_top.jpg"
            )
            canonical_template = "{event_id}_top.jpg"
            redirect_template = "{event_name}_top.jpg"
        else:
            asset_label = "raid thumb"
            not_found_message = f'No raid thumbnails found for event id "{event_id}".'
            raid_thumb_variants = [
                {
                    "difficulty": "vhard",
                    "canonical": "summon_qm_{event_id}_vhard.png",
                    "redirect": "BattleRaid_{event_name}_Very_Hard.png",
                },
                {
                    "difficulty": "vhard_1",
                    "canonical": "summon_qm_{event_id}_vhard_1.png",
                    "redirects": [
                        "BattleRaid_{event_name}_Very_Hard2.png",
                        "BattleRaid_{event_name}_Very_Hard_2.png",
                    ],
                },
                {
                    "difficulty": "vhard_2",
                    "canonical": "summon_qm_{event_id}_vhard_2.png",
                    "redirects": [
                        "BattleRaid_{event_name}_Very_Hard3.png",
                        "BattleRaid_{event_name}_Very_Hard_3.png",
                    ],
                },
                {
                    "difficulty": "ex",
                    "canonical": "summon_qm_{event_id}_ex.png",
                    "redirect": "BattleRaid_{event_name}_Extreme.png",
                },
                {
                    "difficulty": "ex_1",
                    "canonical": "summon_qm_{event_id}_ex_1.png",
                    "redirects": [
                        "BattleRaid_{event_name}_Extreme2.png",
                        "BattleRaid_{event_name}_Extreme_2.png",
                    ],
                },
                {
                    "difficulty": "ex_2",
                    "canonical": "summon_qm_{event_id}_ex_2.png",
                    "redirects": [
                        "BattleRaid_{event_name}_Extreme3.png",
                        "BattleRaid_{event_name}_Extreme_3.png",
                    ],
                },
                {
                    "difficulty": "high",
                    "canonical": "summon_{event_id}_high.png",
                    "redirect": "BattleRaid_{event_name}_Impossible.png",
                },
                {
                    "difficulty": "high_1",
                    "canonical": "summon_qm_{event_id}_high_1.png",
                    "redirects": [
                        "BattleRaid_{event_name}_Impossible2.png",
                        "BattleRaid_{event_name}_Impossible 2.png",
                    ],
                },
                {
                    "difficulty": "high_2",
                    "canonical": "summon_qm_{event_id}_high_2.png",
                    "redirects": [
                        "BattleRaid_{event_name}_Impossible3.png",
                        "BattleRaid_{event_name}_Impossible 3.png",
                    ],
                },
                {
                    "difficulty": "hell",
                    "canonical": "qm_{event_id}_hell.png",
                    "redirect": "BattleRaid_{event_name}_Nightmare.png",
                },
                {
                    "difficulty": "free_proud",
                    "canonical": "quest_assets_{event_id}_free_proud.png",
                    "redirect": "BattleRaid_{event_name}_Proud.png",
                },
                {
                    "difficulty": "free_proud_1",
                    "canonical": "quest_assets_{event_id}_free_proud_1.png",
                    "redirects": [
                        "BattleRaid_{event_name}_Proud2.png",
                        "BattleRaid_{event_name}_Proud_2.png",
                    ],
                },
                {
                    "difficulty": "free_proud_2",
                    "canonical": "quest_assets_{event_id}_free_proud_2.png",
                    "redirects": [
                        "BattleRaid_{event_name}_Proud3.png",
                        "BattleRaid_{event_name}_Proud_3.png",
                    ],
                },
            ]

        if asset_type_key == "raid_thumb":
            loop_total = len(raid_thumb_variants)
        elif asset_type_key in {"top", "trailer_mp3"}:
            loop_total = 1
        else:
            loop_total = max_index

        def process_event_asset_result(display_index, canonical_name, redirect_name, redirect_names, sha1, size, io_obj):
            nonlocal processed, uploaded, duplicates, failed

            processed += 1

            if hasattr(self, "_status_callback"):
                self._status_callback(
                    "processing",
                    processed=processed,
                    total=loop_total,
                    current_image=canonical_name,
                    event_id=event_id,
                    asset_type=asset_type_key,
                )

            other_names = list(redirect_names)
            check_image_result = self.check_image(canonical_name, sha1, size, io_obj, other_names)
            if check_image_result is True:
                uploaded += 1
            elif check_image_result is False:
                failed += 1
                print(f'Upload validation failed for {canonical_name}.')
                return
            else:
                duplicates += 1
                canonical_name = check_image_result

            file_entries.append(
                {
                    "index": display_index,
                    "canonical": canonical_name,
                    "redirect": redirect_name,
                    "redirects": redirect_names,
                }
            )

            for redirect_title in redirect_names:
                self.check_file_redirect(canonical_name, redirect_title)
            time.sleep(self.delay)
            self.check_file_double_redirect(canonical_name)

        if hasattr(self, "_status_callback"):
            self._status_callback(
                "processing",
                processed=processed,
                total=loop_total,
                current_image=None,
                event_id=event_id,
                asset_type=asset_type_key,
            )
        for index in range(1, loop_total + 1):
            if asset_type_key == "raid_thumb":
                variant = raid_thumb_variants[index - 1]
                difficulty = variant["difficulty"]
                url = (
                    "https://prd-game-a-granbluefantasy.akamaized.net/assets_en/"
                    f"img/sp/assets/summon/qm/{event_id}_{difficulty}.png"
                )
                if difficulty == "free_proud":
                    url = (
                        "https://prd-game-a-granbluefantasy.akamaized.net/assets_en/"
                        f"img/sp/quest/assets/free/{event_id}_free_proud.png"
                    )
                elif difficulty == "free_proud_1":
                    url = (
                        "https://prd-game-a-granbluefantasy.akamaized.net/assets_en/"
                        f"img/sp/quest/assets/{event_id}_free_proud_1.png"
                    )
                elif difficulty == "free_proud_2":
                    url = (
                        "https://prd-game-a-granbluefantasy.akamaized.net/assets_en/"
                        f"img/sp/quest/assets/{event_id}_free_proud_2.png"
                    )
                canonical_name = variant["canonical"].format(event_id=event_id)
                redirect_names = [
                    redirect_template.format(event_name=event_name)
                    for redirect_template in variant.get("redirects", [])
                ]
                if not redirect_names:
                    redirect_names = [variant["redirect"].format(event_name=event_name)]
                redirect_name = redirect_names[0]
                display_index = index
            elif asset_type_key == "guide":
                base_index = index
                base_suffix = str(base_index)
                base_found = False
                for suffix_part in guide_suffixes:
                    suffix = f"{base_index}{suffix_part}"
                    display_index = suffix
                    resolved_extension = None
                    success = False
                    sha1 = None
                    size = None
                    io_obj = None
                    url = None
                    canonical_name = None
                    redirect_name = None
                    redirect_names = []
                    for extension in ("jpg", "png"):
                        candidate_url = (
                            "https://prd-game-a-granbluefantasy.akamaized.net/assets_en/"
                            f"img/sp/event/{event_id}/assets/tips/description_event_{suffix}.{extension}"
                        )
                        print(
                            f'Downloading {asset_label} #{suffix} for "{event_name}" '
                            f'(event id: {event_id}) -> {candidate_url}'
                        )
                        success, sha1, size, io_obj = self.get_image(candidate_url)
                        if success:
                            resolved_extension = extension
                            url = candidate_url
                            canonical_name = (
                                f"{event_id}_description_event_{suffix}.{resolved_extension}"
                            )
                            redirect_name = (
                                f"description_{event_name}_{suffix}.{resolved_extension}"
                            )
                            redirect_names = [redirect_name]
                            break
                    if not success:
                        if suffix == base_suffix:
                            if base_index == 1:
                                print(not_found_message)
                            else:
                                print(
                                    f'Event guide base index "{base_suffix}" not found '
                                    f'(event id: {event_id}); stopping.'
                                )
                            break
                        print(
                            f'Event guide panel not found for suffix "{suffix}" '
                            f'(event id: {event_id}); skipping.'
                        )
                        continue
                    base_found = True
                    process_event_asset_result(
                        display_index=display_index,
                        canonical_name=canonical_name,
                        redirect_name=redirect_name,
                        redirect_names=redirect_names,
                        sha1=sha1,
                        size=size,
                        io_obj=io_obj,
                    )
                if not base_found:
                    break
                continue
            elif asset_type_key == "voice_banner":
                display_index = index
                resolved_extension = None
                success = False
                sha1 = None
                size = None
                io_obj = None
                url = None
                canonical_name = None
                redirect_name = None
                redirect_names = []
                for extension in ("png", "jpg"):
                    candidate_url = (
                        "https://prd-game-a-granbluefantasy.akamaized.net/assets_en/"
                        f"img/sp/banner/events/{event_id}/banner_event_trailer_{index}.{extension}"
                    )
                    print(
                        f'Downloading {asset_label} #{index} for "{event_name}" '
                        f'(event id: {event_id}) -> {candidate_url}'
                    )
                    success, sha1, size, io_obj = self.get_image(candidate_url)
                    if success:
                        resolved_extension = extension
                        url = candidate_url
                        canonical_name = (
                            f"{event_id}_banner_event_trailer_{index}.{resolved_extension}"
                        )
                        redirect_name = (
                            f"banner_{event_name}_trailer_{index}.{resolved_extension}"
                        )
                        redirect_names = [redirect_name]
                        break
            elif asset_type_key == "trailer_mp3":
                display_index = 1
                url = url_template.format(event_id=event_id)
                redirect_name = None
                redirect_names = []
                success = False
                sha1 = None
                size = None
                io_obj = None
            else:
                url = url_template.format(event_id=event_id, index=index)
                canonical_name = canonical_template.format(event_id=event_id, index=index)
                redirect_name = redirect_template.format(event_name=event_name, index=index)
                redirect_names = [redirect_name]
                display_index = index

            if asset_type_key not in {"guide", "voice_banner"}:
                print(
                    f'Downloading {asset_label} #{display_index} for "{event_name}" '
                    f'(event id: {event_id}) -> {url}'
                )

            if asset_type_key not in {"guide", "voice_banner"}:
                success, sha1, size, io_obj = self.get_image(url)
            if not success:
                if asset_type_key == "raid_thumb":
                    print(
                        f'Raid thumbnail not found for variant "{difficulty}" '
                        f'(event id: {event_id}); skipping.'
                    )
                    continue
                if asset_type_key == "guide":
                    continue
                if asset_type_key == "voice_banner":
                    if index == 1:
                        print(not_found_message)
                    break
                if index == 1:
                    print(not_found_message)
                break
            process_event_asset_result(
                display_index=display_index,
                canonical_name=canonical_name,
                redirect_name=redirect_name,
                redirect_names=redirect_names,
                sha1=sha1,
                size=size,
                io_obj=io_obj,
            )

        if processed == 0:
            raise ValueError(not_found_message)

        if hasattr(self, "_status_callback"):
            self._status_callback(
                "completed",
                processed=processed,
                uploaded=uploaded,
                duplicates=duplicates,
                failed=failed,
                total=loop_total,
                total_urls=processed,
                event_id=event_id,
                asset_type=asset_type_key,
            )

        return {
            "processed": processed,
            "uploaded": uploaded,
            "duplicates": duplicates,
            "failed": failed,
            "total_urls": processed,
            "total": loop_total,
            "files": file_entries,
            "event_id": event_id,
            "asset_type": asset_type_key,
        }

    def _character_fs_skin_paths(self):
        return {
            'f_skin': ['jpg', '_tall',
            ['_01_s1', '_01_s2', '_01_s3', '_01_s4', '_01_s5', '_01_s6',
            '_01_1_s1', '_01_1_s2', '_01_1_s3', '_01_1_s4', '_01_1_s5', '_01_1_s6',
            '_01_101_s1', '_01_101_s2', '_01_101_s3', '_01_101_s4', '_01_101_s5', '_01_101_s6',
            '_01_102_s1', '_01_102_s2', '_01_102_s3', '_01_102_s4', '_01_102_s5', '_01_102_s6',
            '_01_103_s1', '_01_103_s2', '_01_103_s3', '_01_103_s4', '_01_103_s5', '_01_103_s6',
            '_01_104_s1', '_01_104_s2', '_01_104_s3', '_01_104_s4', '_01_104_s5', '_01_104_s6',
            '_01_105_s1', '_01_105_s2', '_01_105_s3', '_01_105_s4', '_01_105_s5', '_01_105_s6',
            '_02_s1', '_02_s2', '_02_s3', '_02_s4', '_02_s5', '_02_s6',
            '_02_1_s1', '_02_1_s2', '_02_1_s3', '_02_1_s4', '_02_1_s5', '_02_1_s6',
            '_02_101_s1', '_02_101_s2', '_02_101_s3', '_02_101_s4', '_02_101_s5', '_02_101_s6',
            '_02_102_s1', '_02_102_s2', '_02_102_s3', '_02_102_s4', '_02_102_s5', '_02_102_s6',
            '_02_103_s1', '_02_103_s2', '_02_103_s3', '_02_103_s4', '_02_103_s5', '_02_103_s6',
            '_02_104_s1', '_02_104_s2', '_02_104_s3', '_02_104_s4', '_02_104_s5', '_02_104_s6',
            '_02_105_s1', '_02_105_s2', '_02_105_s3', '_02_105_s4', '_02_105_s5', '_02_105_s6',
            '_03_s1', '_03_s2', '_03_s3', '_03_s4', '_03_s5', '_03_s6',
            '_03_1_s1', '_03_1_s2', '_03_1_s3', '_03_1_s4', '_03_1_s5', '_03_1_s6',
            '_03_101_s1', '_03_101_s2', '_03_101_s3', '_03_101_s4', '_03_101_s5', '_03_101_s6',
            '_03_102_s1', '_03_102_s2', '_03_102_s3', '_03_102_s4', '_03_102_s5', '_03_102_s6',
            '_03_103_s1', '_03_103_s2', '_03_103_s3', '_03_103_s4', '_03_103_s5', '_03_103_s6',
            '_03_104_s1', '_03_104_s2', '_03_104_s3', '_03_104_s4', '_03_104_s5', '_03_104_s6',
            '_03_105_s1', '_03_105_s2', '_03_105_s3', '_03_105_s4', '_03_105_s5', '_03_105_s6',
            '_04_s1', '_04_s2', '_04_s3', '_04_s4', '_04_s5', '_04_s6',
            '_81_s1', '_81_s2', '_81_s3', '_81_s4', '_81_s5', '_81_s6',
            '_82_s1', '_82_s2', '_82_s3', '_82_s4', '_82_s5', '_82_s6',
            '_91_s1', '_91_s2', '_91_s3', '_91_s4', '_91_s5', '_91_s6',
            '_91_0_s1', '_91_0_s2', '_91_0_s3', '_91_0_s4', '_91_0_s5', '_91_0_s6',
            '_91_1_s1', '_91_1_s2', '_91_1_s3', '_91_1_s4', '_91_1_s5', '_91_1_s6',

            '_01_01', '_01_02', '_01_03', '_01_04', '_01_05', '_01_06',
            '_02_01', '_02_02', '_02_03', '_02_04', '_02_05', '_02_06',
            '_03_01', '_03_02', '_03_03', '_03_04', '_03_05', '_03_06'
            ],
            ['A_fire', 'A_water', 'A_earth', 'A_wind', 'A_light', 'A_dark',
            'A2_fire', 'A2_water', 'A2_earth', 'A2_wind', 'A2_light', 'A2_dark',
            'A101_fire', 'A101_water', 'A101_earth', 'A101_wind', 'A101_light', 'A101_dark',
            'A102_fire', 'A102_water', 'A102_earth', 'A102_wind', 'A102_light', 'A102_dark',
            'A103_fire', 'A103_water', 'A103_earth', 'A103_wind', 'A103_light', 'A103_dark',
            'A104_fire', 'A104_water', 'A104_earth', 'A104_wind', 'A104_light', 'A104_dark',
            'A105_fire', 'A105_water', 'A105_earth', 'A105_wind', 'A105_light', 'A105_dark',
            'B_fire', 'B_water', 'B_earth', 'B_wind', 'B_light', 'B_dark',
            'B2_fire', 'B2_water', 'B2_earth', 'B2_wind', 'B2_light', 'B2_dark',
            'B101_fire', 'B101_water', 'B101_earth', 'B101_wind', 'B101_light', 'B101_dark',
            'B102_fire', 'B102_water', 'B102_earth', 'B102_wind', 'B102_light', 'B102_dark',
            'B103_fire', 'B103_water', 'B103_earth', 'B103_wind', 'B103_light', 'B103_dark',
            'B104_fire', 'B104_water', 'B104_earth', 'B104_wind', 'B104_light', 'B104_dark',
            'B105_fire', 'B105_water', 'B105_earth', 'B105_wind', 'B105_light', 'B105_dark',
            'C_fire', 'C_water', 'C_earth', 'C_wind', 'C_light', 'C_dark',
            'C2_fire', 'C2_water', 'C2_earth', 'C2_wind', 'C2_light', 'C2_dark',
            'C101_fire', 'C101_water', 'C101_earth', 'C101_wind', 'C101_light', 'C101_dark',
            'C102_fire', 'C102_water', 'C102_earth', 'C102_wind', 'C102_light', 'C102_dark',
            'C103_fire', 'C103_water', 'C103_earth', 'C103_wind', 'C103_light', 'C103_dark',
            'C104_fire', 'C104_water', 'C104_earth', 'C104_wind', 'C104_light', 'C104_dark',
            'C105_fire', 'C105_water', 'C105_earth', 'C105_wind', 'C105_light', 'C105_dark',
            'D_fire', 'D_water', 'D_earth', 'D_wind', 'D_light', 'D_dark',
            'ST_fire', 'ST_water', 'ST_earth', 'ST_wind', 'ST_light', 'ST_dark',
            'ST2_fire', 'ST2_water', 'ST2_earth', 'ST2_wind', 'ST2_light', 'ST2_dark',
            'EX_fire', 'EX_water', 'EX_earth', 'EX_wind', 'EX_light', 'EX_dark',
            'EX1_fire', 'EX1_water', 'EX1_earth', 'EX1_wind', 'EX1_light', 'EX1_dark',
            'EX2_fire', 'EX2_water', 'EX2_earth', 'EX2_wind', 'EX2_light', 'EX2_dark',

            'A01_fire', 'A01_water', 'A01_earth', 'A01_wind', 'A01_light', 'A01_dark',
            'A02_fire', 'A02_water', 'A02_earth', 'A02_wind', 'A02_light', 'A02_dark',
            'A03_fire', 'A03_water', 'A03_earth', 'A03_wind', 'A03_light', 'A03_dark',
            ],
            ['Character Images', 'Tall Skin Character Images' ]],

            's_skin': ['jpg', '_square',
            ['_01_s1', '_01_s2', '_01_s3', '_01_s4', '_01_s5', '_01_s6',
            '_01_1_s1', '_01_1_s2', '_01_1_s3', '_01_1_s4', '_01_1_s5', '_01_1_s6',
            '_01_101_s1', '_01_101_s2', '_01_101_s3', '_01_101_s4', '_01_101_s5', '_01_101_s6',
            '_01_102_s1', '_01_102_s2', '_01_102_s3', '_01_102_s4', '_01_102_s5', '_01_102_s6',
            '_01_103_s1', '_01_103_s2', '_01_103_s3', '_01_103_s4', '_01_103_s5', '_01_103_s6',
            '_01_104_s1', '_01_104_s2', '_01_104_s3', '_01_104_s4', '_01_104_s5', '_01_104_s6',
            '_01_105_s1', '_01_105_s2', '_01_105_s3', '_01_105_s4', '_01_105_s5', '_01_105_s6',
            '_02_s1', '_02_s2', '_02_s3', '_02_s4', '_02_s5', '_02_s6',
            '_02_1_s1', '_02_1_s2', '_02_1_s3', '_02_1_s4', '_02_1_s5', '_02_1_s6',
            '_02_101_s1', '_02_101_s2', '_02_101_s3', '_02_101_s4', '_02_101_s5', '_02_101_s6',
            '_02_102_s1', '_02_102_s2', '_02_102_s3', '_02_102_s4', '_02_102_s5', '_02_102_s6',
            '_02_103_s1', '_02_103_s2', '_02_103_s3', '_02_103_s4', '_02_103_s5', '_02_103_s6',
            '_02_104_s1', '_02_104_s2', '_02_104_s3', '_02_104_s4', '_02_104_s5', '_02_104_s6',
            '_02_105_s1', '_02_105_s2', '_02_105_s3', '_02_105_s4', '_02_105_s5', '_02_105_s6',
            '_03_s1', '_03_s2', '_03_s3', '_03_s4', '_03_s5', '_03_s6',
            '_03_1_s1', '_03_1_s2', '_03_1_s3', '_03_1_s4', '_03_1_s5', '_03_1_s6',
            '_03_101_s1', '_03_101_s2', '_03_101_s3', '_03_101_s4', '_03_101_s5', '_03_101_s6',
            '_03_102_s1', '_03_102_s2', '_03_102_s3', '_03_102_s4', '_03_102_s5', '_03_102_s6',
            '_03_103_s1', '_03_103_s2', '_03_103_s3', '_03_103_s4', '_03_103_s5', '_03_103_s6',
            '_03_104_s1', '_03_104_s2', '_03_104_s3', '_03_104_s4', '_03_104_s5', '_03_104_s6',
            '_03_105_s1', '_03_105_s2', '_03_105_s3', '_03_105_s4', '_03_105_s5', '_03_105_s6',
            '_04_s1', '_04_s2', '_04_s3', '_04_s4', '_04_s5', '_04_s6',
            '_81_s1', '_81_s2', '_81_s3', '_81_s4', '_81_s5', '_81_s6',
            '_82_s1', '_82_s2', '_82_s3', '_82_s4', '_82_s5', '_82_s6',
            '_91_s1', '_91_s2', '_91_s3', '_91_s4', '_91_s5', '_91_s6',
            '_91_0_s1', '_91_0_s2', '_91_0_s3', '_91_0_s4', '_91_0_s5', '_91_0_s6',
            '_91_1_s1', '_91_1_s2', '_91_1_s3', '_91_1_s4', '_91_1_s5', '_91_1_s6',

            '_01_01', '_01_02', '_01_03', '_01_04', '_01_05', '_01_06',
            '_02_01', '_02_02', '_02_03', '_02_04', '_02_05', '_02_06',
            '_03_01', '_03_02', '_03_03', '_03_04', '_03_05', '_03_06'
            ],
            ['A_fire', 'A_water', 'A_earth', 'A_wind', 'A_light', 'A_dark',
            'A2_fire', 'A2_water', 'A2_earth', 'A2_wind', 'A2_light', 'A2_dark',
            'A101_fire', 'A101_water', 'A101_earth', 'A101_wind', 'A101_light', 'A101_dark',
            'A102_fire', 'A102_water', 'A102_earth', 'A102_wind', 'A102_light', 'A102_dark',
            'A103_fire', 'A103_water', 'A103_earth', 'A103_wind', 'A103_light', 'A103_dark',
            'A104_fire', 'A104_water', 'A104_earth', 'A104_wind', 'A104_light', 'A104_dark',
            'A105_fire', 'A105_water', 'A105_earth', 'A105_wind', 'A105_light', 'A105_dark',
            'B_fire', 'B_water', 'B_earth', 'B_wind', 'B_light', 'B_dark',
            'B2_fire', 'B2_water', 'B2_earth', 'B2_wind', 'B2_light', 'B2_dark',
            'B101_fire', 'B101_water', 'B101_earth', 'B101_wind', 'B101_light', 'B101_dark',
            'B102_fire', 'B102_water', 'B102_earth', 'B102_wind', 'B102_light', 'B102_dark',
            'B103_fire', 'B103_water', 'B103_earth', 'B103_wind', 'B103_light', 'B103_dark',
            'B104_fire', 'B104_water', 'B104_earth', 'B104_wind', 'B104_light', 'B104_dark',
            'B105_fire', 'B105_water', 'B105_earth', 'B105_wind', 'B105_light', 'B105_dark',
            'C_fire', 'C_water', 'C_earth', 'C_wind', 'C_light', 'C_dark',
            'C2_fire', 'C2_water', 'C2_earth', 'C2_wind', 'C2_light', 'C2_dark',
            'C101_fire', 'C101_water', 'C101_earth', 'C101_wind', 'C101_light', 'C101_dark',
            'C102_fire', 'C102_water', 'C102_earth', 'C102_wind', 'C102_light', 'C102_dark',
            'C103_fire', 'C103_water', 'C103_earth', 'C103_wind', 'C103_light', 'C103_dark',
            'C104_fire', 'C104_water', 'C104_earth', 'C104_wind', 'C104_light', 'C104_dark',
            'C105_fire', 'C105_water', 'C105_earth', 'C105_wind', 'C105_light', 'C105_dark',
            'D_fire', 'D_water', 'D_earth', 'D_wind', 'D_light', 'D_dark',
            'ST_fire', 'ST_water', 'ST_earth', 'ST_wind', 'ST_light', 'ST_dark',
            'ST2_fire', 'ST2_water', 'ST2_earth', 'ST2_wind', 'ST2_light', 'ST2_dark',
            'EX_fire', 'EX_water', 'EX_earth', 'EX_wind', 'EX_light', 'EX_dark',
            'EX1_fire', 'EX1_water', 'EX1_earth', 'EX1_wind', 'EX1_light', 'EX1_dark',
            'EX2_fire', 'EX2_water', 'EX2_earth', 'EX2_wind', 'EX2_light', 'EX2_dark',

            'A01_fire', 'A01_water', 'A01_earth', 'A01_wind', 'A01_light', 'A01_dark',
            'A02_fire', 'A02_water', 'A02_earth', 'A02_wind', 'A02_light', 'A02_dark',
            'A03_fire', 'A03_water', 'A03_earth', 'A03_wind', 'A03_light', 'A03_dark',
            ],
            ['Character Images', 'Square Skin Character Images' ]],
        }

    def check_character(self, page):
        paths = {
            'zoom':          ['png', '', ['_01', '_01_1', '_01_101', '_01_102', '_01_103', '_01_104', '_01_105', '_02', '_02_1', '_02_101', '_02_102', '_02_103', '_02_104', '_02_105', '_03', '_03_1', '_03_101', '_03_102', '_03_103', '_03_104', '_03_105', '_04', 
            '_81', '_82', '_88', '_91', '_91_0', '_91_1', 
            # '_01_01', '_01_02', '_01_03', '_01_04', '_01_05', '_01_06', 
            # '_02_01', '_02_02', '_02_03', '_02_04', '_02_05', '_02_06', 
            # '_03_01', '_03_02', '_03_03', '_03_04', '_03_05', '_03_06'
            ], 
            
            ['A', 'A2', 'A101', 'A102', 'A103', 'A104', 'A105', 'B', 'B2',  'B101', 'B102', 'B103', 'B104', 'B105', 'C', 'C2',  'C101', 'C102', 'C103', 'C104', 'C105', 'D', 'ST', 'ST2', 'ST8', 'EX', 'EX1', 'EX2',
            # 'A01', 'A02', 'A03', 'A04', 'A05', 'A06', 
            # 'B01', 'B02', 'B03', 'B04', 'B05', 'B06', 
            # 'C01', 'C02', 'C03', 'C04', 'C05', 'C06'
            ], 
            ['Character Images', 'Full Character Images'  ]],

            'skycompass_zoom': ['png', '_HD',
            ['_01', '_01_1', '_01_101', '_01_102', '_01_103', '_01_104', '_01_105', '_02', '_02_1', '_02_101', '_02_102', '_02_103', '_02_104', '_02_105', '_03', '_03_1', '_03_101', '_03_102', '_03_103', '_03_104', '_03_105', '_04',
            '_81', '_82', '_88', '_91', '_91_0', '_91_1',
            # '_01_01', '_01_02', '_01_03', '_01_04', '_01_05', '_01_06',
            # '_02_01', '_02_02', '_02_03', '_02_04', '_02_05', '_02_06',
            # '_03_01', '_03_02', '_03_03', '_03_04', '_03_05', '_03_06'
            ],
            ['A', 'A2', 'A101', 'A102', 'A103', 'A104', 'A105', 'B', 'B2',  'B101', 'B102', 'B103', 'B104', 'B105', 'C', 'C2',  'C101', 'C102', 'C103', 'C104', 'C105', 'D', 'ST', 'ST2', 'ST8', 'EX', 'EX1', 'EX2',
            # 'A01', 'A02', 'A03', 'A04', 'A05', 'A06',
            # 'B01', 'B02', 'B03', 'B04', 'B05', 'B06',
            # 'C01', 'C02', 'C03', 'C04', 'C05', 'C06'
            ],
            ['Sky Compass Images', 'Sky Compass Character Images']],
            
            'f':             ['jpg', '_tall', ['_01', '_01_1', '_01_101', '_01_102', '_01_103', '_01_104', '_01_105', '_02', '_02_1', '_02_101', '_02_102', '_02_103', '_02_104', '_02_105', '_03', '_03_1', '_03_101', '_03_102', '_03_103', '_03_104', '_03_105', '_04', '_81', '_82', '_91', '_91_0', '_91_1',
            '_01_01', '_01_02', '_01_03', '_01_04', '_01_05', '_01_06', 
            '_02_01', '_02_02', '_02_03', '_02_04', '_02_05', '_02_06', 
            '_03_01', '_03_02', '_03_03', '_03_04', '_03_05', '_03_06'
            ], 
            ['A', 'A2', 'A101', 'A102', 'A103', 'A104', 'A105', 'B', 'B2',  'B101', 'B102', 'B103', 'B104', 'B105', 'C', 'C2',  'C101', 'C102', 'C103', 'C104', 'C105', 'D', 'ST', 'ST2', 'EX', 'EX1', 'EX2',
            'A01', 'A02', 'A03', 'A04', 'A05', 'A06', 
            'B01', 'B02', 'B03', 'B04', 'B05', 'B06', 
            'C01', 'C02', 'C03', 'C04', 'C05', 'C06'
            ], 
            ['Character Images', 'Tall Character Images'  ]],
            
            'm':             ['jpg', '_icon',   ['_01', '_01_1', '_01_101', '_01_102', '_01_103', '_01_104', '_01_105', '_02', '_02_1', '_02_101', '_02_102', '_02_103', '_02_104', '_02_105', '_03', '_03_1', '_03_101', '_03_102', '_03_103', '_03_104', '_03_105', '_04', 
            '_81', '_82', '_88', '_91', '_91_0', '_91_1',
            '_01_01', '_01_02', '_01_03', '_01_04', '_01_05', '_01_06', 
            '_02_01', '_02_02', '_02_03', '_02_04', '_02_05', '_02_06', 
            '_03_01', '_03_02', '_03_03', '_03_04', '_03_05', '_03_06'
            ], 
            ['A', 'A2', 'A101', 'A102', 'A103', 'A104', 'A105', 'B', 'B2',  'B101', 'B102', 'B103', 'B104', 'B105', 'C', 'C2',  'C101', 'C102', 'C103', 'C104', 'C105', 'D', 'ST', 'ST2', 'ST8', 'EX', 'EX1', 'EX2',
            'A01', 'A02', 'A03', 'A04', 'A05', 'A06', 
            'B01', 'B02', 'B03', 'B04', 'B05', 'B06', 
            'C01', 'C02', 'C03', 'C04', 'C05', 'C06'
            ], 
            
            ['Character Images', 'Icon Character Images'  ]],

            'my':            ['png', '_my',     ['_01', '_01_1', '_01_101', '_01_102', '_01_103', '_01_104', '_01_105', '_02', '_02_1', '_02_101', '_02_102', '_02_103', '_02_104', '_02_105', '_03', '_03_1', '_03_101', '_03_102', '_03_103', '_03_104', '_03_105', '_04',
            '_81', '_82', '_88', '_91', '_91_0', '_91_1',
            # '_01_01', '_01_02', '_01_03', '_01_04', '_01_05', '_01_06',
            # '_02_01', '_02_02', '_02_03', '_02_04', '_02_05', '_02_06',
            # '_03_01', '_03_02', '_03_03', '_03_04', '_03_05', '_03_06'
            ],
            ['A', 'A2', 'A101', 'A102', 'A103', 'A104', 'A105', 'B', 'B2',  'B101', 'B102', 'B103', 'B104', 'B105', 'C', 'C2',  'C101', 'C102', 'C103', 'C104', 'C105', 'D', 'ST', 'ST2', 'ST8', 'EX', 'EX1', 'EX2',
            #'A01', 'A02', 'A03', 'A04', 'A05', 'A06',
            #'B01', 'B02', 'B03', 'B04', 'B05', 'B06',
            #'C01', 'C02', 'C03', 'C04', 'C05', 'C06'
            ],

            ['Character Images', 'Home Character Images'  ]],

            'result_lvup':   ['png', '_result_lvup',
            ['_01', '_01_1', '_01_101', '_01_102', '_01_103', '_01_104', '_01_105', '_02', '_02_1', '_02_101', '_02_102', '_02_103', '_02_104', '_02_105', '_03', '_03_1', '_03_101', '_03_102', '_03_103', '_03_104', '_03_105', '_04',
            '_81', '_82', '_88', '_91', '_91_0', '_91_1',
            # '_01_01', '_01_02', '_01_03', '_01_04', '_01_05', '_01_06',
            # '_02_01', '_02_02', '_02_03', '_02_04', '_02_05', '_02_06',
            # '_03_01', '_03_02', '_03_03', '_03_04', '_03_05', '_03_06'
            ],
            ['A', 'A2', 'A101', 'A102', 'A103', 'A104', 'A105', 'B', 'B2',  'B101', 'B102', 'B103', 'B104', 'B105', 'C', 'C2',  'C101', 'C102', 'C103', 'C104', 'C105', 'D', 'ST', 'ST2', 'ST8', 'EX', 'EX1', 'EX2',
            # 'A01', 'A02', 'A03', 'A04', 'A05', 'A06',
            # 'B01', 'B02', 'B03', 'B04', 'B05', 'B06',
            # 'C01', 'C02', 'C03', 'C04', 'C05', 'C06'
            ],

            ['Character Images', 'Result Level Up Character Images'  ]],
            
            's':             ['jpg', '_square', 
            ['_01', '_01_1', '_01_101', '_01_102', '_01_103', '_01_104', '_01_105', '_02',  '_02_1', '_02_101', '_02_102', '_02_103', '_02_104', '_02_105',   '_03', '_03_1', '_03_101', '_03_102', '_03_103', '_03_104', '_03_105', '_04', '_81', '_82', '_88', '_91', '_91_0', '_91_1',
            '_01_01', '_01_02', '_01_03', '_01_04', '_01_05', '_01_06', 
            '_02_01', '_02_02', '_02_03', '_02_04', '_02_05', '_02_06', 
            '_03_01', '_03_02', '_03_03', '_03_04', '_03_05', '_03_06'
            ], 
            ['A', 'A2', 'A101', 'A102', 'A103', 'A104', 'A105', 'B', 'B2', 'B101', 'B102', 'B103', 'B104', 'B105', 'C', 'C2', 'C101', 'C102', 'C103', 'C104', 'C105', 'D', 'ST', 'ST2', 'ST8', 'EX', 'EX1', 'EX2',
            'A01', 'A02', 'A03', 'A04', 'A05', 'A06', 
            'B01', 'B02', 'B03', 'B04', 'B05', 'B06', 
            'C01', 'C02', 'C03', 'C04', 'C05', 'C06'
            ], 
            ['Character Images', 'Square Character Images']],
            
            'sd':            ['png', '_SD',     ['_01', '_01_1', '_01_101', '_01_102', '_01_103', '_01_104', '_01_105', '_02', '_02_1', '_02_101', '_02_102', '_02_103', '_02_104', '_02_105', '_03', '_03_1', '_03_101', '_03_102', '_03_103', '_03_104', '_03_105', '_04', '_81', '_82', '_91', '_91_0', '_91_1'], ['A', 'A2', 'A101', 'A102', 'A103', 'A104', 'A105', 'B', 'B2',  'B101', 'B102', 'B103', 'B104', 'B105', 'C', 'C2',  'C101', 'C102', 'C103', 'C104', 'C105', 'D', 'ST', 'ST2', 'EX', 'EX1', 'EX2'], ['Character Images', 'Sprite Character Images']],
            
            'cutin_special': ['jpg', '_cutin',     ['_01', '_01_1', '_01_101', '_01_102', '_01_103', '_01_104', '_01_105', '_02', '_02_1', '_02_101', '_02_102', '_02_103', '_02_104', '_02_105', '_03', '_03_1', '_03_101', '_03_102', '_03_103', '_03_104', '_03_105', '_04', '_81', '_82', '_91', '_91_0', '_91_1'], ['A', 'A2', 'A101', 'A102', 'A103', 'A104', 'A105', 'B', 'B2',  'B101', 'B102', 'B103', 'B104', 'B105', 'C', 'C2',  'C101', 'C102', 'C103', 'C104', 'C105', 'D', 'ST', 'ST2', 'EX', 'EX1', 'EX2'], ['Character Images', 'Cutin Character Images']],
            
            'raid_chain': ['jpg', '_chain',     ['_01', '_01_1', '_01_101', '_01_102', '_01_103', '_01_104', '_01_105', '_02', '_02_1', '_02_101', '_02_102', '_02_103', '_02_104', '_02_105', '_03', '_03_1', '_03_101', '_03_102', '_03_103', '_03_104', '_03_105', '_04', '_81', '_82', '_91', '_91_0', '_91_1'], ['A', 'A2', 'A101', 'A102', 'A103', 'A104', 'A105', 'B', 'B2',  'B101', 'B102', 'B103', 'B104', 'B105', 'C', 'C2',  'C101', 'C102', 'C103', 'C104', 'C105', 'D', 'ST', 'ST2', 'EX', 'EX1', 'EX2'], ['Character Images', 'Chain Burst Character Images']],
            
            't': ['png', '_babyl',     ['_01', '_01_1', '_01_101', '_01_102', '_01_103', '_01_104', '_01_105', '_02', '_02_1', '_02_101', '_02_102', '_02_103', '_02_104', '_02_105', '_03', '_03_1', '_03_101', '_03_102', '_03_103', '_03_104', '_03_105', '_04', '_81', '_82', '_91', '_91_0', '_91_1'], ['A', 'A2', 'A101', 'A102', 'A103', 'A104', 'A105', 'B', 'B2',  'B101', 'B102', 'B103', 'B104', 'B105', 'C', 'C2',  'C101', 'C102', 'C103', 'C104', 'C105', 'D', 'ST', 'ST2', 'EX', 'EX1', 'EX2'], ['Character Images', 'Babyl Character Images']],
            
            'detail': ['png', '_detail',     ['_01', '_01_1', '_01_101', '_01_102', '_01_103', '_01_104', '_01_105', '_02', '_02_1', '_02_101', '_02_102', '_02_103', '_02_104', '_02_105', '_03', '_03_1', '_03_101', '_03_102', '_03_103', '_03_104', '_03_105', '_04', '_81', '_82', '_91', '_91_0', '_91_1'], ['A', 'A2', 'A101', 'A102', 'A103', 'A104', 'A105', 'B', 'B2',  'B101', 'B102', 'B103', 'B104', 'B105', 'C', 'C2',  'C101', 'C102', 'C103', 'C104', 'C105', 'D', 'ST', 'ST2', 'EX', 'EX1', 'EX2'], ['Character Images', 'Detail Character Images']],
            
            'raid_normal': ['jpg', '_raid',     ['_01', '_01_1', '_01_101', '_01_102', '_01_103', '_01_104', '_01_105', '_02', '_02_1', '_02_101', '_02_102', '_02_103', '_02_104', '_02_105', '_03', '_03_1', '_03_101', '_03_102', '_03_103', '_03_104', '_03_105', '_04', '_81', '_82', '_91', '_91_0', '_91_1'], ['A', 'A2', 'A101', 'A102', 'A103', 'A104', 'A105', 'B', 'B2',  'B101', 'B102', 'B103', 'B104', 'B105', 'C', 'C2',  'C101', 'C102', 'C103', 'C104', 'C105', 'D', 'ST', 'ST2', 'EX', 'EX1', 'EX2'], ['Character Images', 'Raid Character Images']],
            
            'quest': ['jpg', '_quest',     ['_01', '_01_1', '_01_101', '_01_102', '_01_103', '_01_104', '_01_105', '_02', '_02_1', '_02_101', '_02_102', '_02_103', '_02_104', '_02_105', '_03', '_03_1', '_03_101', '_03_102', '_03_103', '_03_104', '_03_105', '_04', '_81', '_82', '_91', '_91_0', '_91_1'], ['A', 'A2', 'A101', 'A102', 'A103', 'A104', 'A105', 'B', 'B2',  'B101', 'B102', 'B103', 'B104', 'B105', 'C', 'C2',  'C101', 'C102', 'C103', 'C104', 'C105', 'D', 'ST', 'ST2', 'EX', 'EX1', 'EX2'], ['Character Images', 'Quest Character Images']],

            'skycompass': ['png', '_HD',     
                ['_01', '_01_0', '_01_1', '_01_101', '_01_102', '_01_103', '_01_104', '_01_105', '_02', '_0201', '_02_1', '_02_101', '_02_102', '_02_103', '_02_104', '_02_105', '_03', '_03_1', '_03_101', '_03_102', '_03_103', '_03_104', '_03_105', '_04', '_81', '_82', '_91', '_91_0', '_91_1'], 
                ['A', 'A1', 'A2', 'A101', 'A102', 'A103', 'A104', 'A105', 'B', 'B1', 'B2',  'B101', 'B102', 'B103', 'B104', 'B105', 'C', 'C2',  'C101', 'C102', 'C103', 'C104', 'C105', 'D', 'ST', 'ST2', 'EX', 'EX1', 'EX2'], ['Character Images', 'Skycompass Images', 'Skycompass Character Images']],
            
            # 'zoom':          ['png', '',        ['_01_101', '_01_102', '_01_103'], ['A1', 'A2', 'A3'], ['Character Images', 'Full Character Images'  ]],
            # 'f':             ['jpg', '_tall',   ['_01_101', '_01_102', '_01_103', '_02_101', '_02_102', '_02_103', '_03_101', '_03_102', '_03_103'], ['A1', 'A2', 'A3', 'B1', 'B2', 'B3', 'C1', 'C2', 'C3'], ['Character Images', 'Tall Character Images'  ]],
            # 'm':             ['jpg', '_icon',   ['_01_101', '_01_102', '_01_103', '_02_101', '_02_102', '_02_103', '_03_101', '_03_102', '_03_103'], ['A1', 'A2', 'A3', 'B1', 'B2', 'B3', 'C1', 'C2', 'C3'], ['Character Images', 'Icon Character Images'  ]],
            # 's':             ['jpg', '_square', ['_01_101', '_01_102', '_01_103', '_02_101', '_02_102', '_02_103', '_03_101', '_03_102', '_03_103'], ['A1', 'A2', 'A3', 'B1', 'B2', 'B3', 'C1', 'C2', 'C3'], ['Character Images', 'Square Character Images']],
            # 'sd':            ['png', '_SD',     ['_01_101', '_01_102', '_01_103', '_02_101', '_02_102', '_02_103', '_03_101', '_03_102', '_03_103'], ['A1', 'A2', 'A3', 'B1', 'B2', 'B3', 'C1', 'C2', 'C3'], ['Character Images', 'Sprite Character Images']],
          
            # 'zoom':          ['png', '',        ['_91_0', '_91_1'], ['EX_A1', 'EX_A2'], ['Character Images', 'Full Character Images'  ]],
            # 'f':             ['jpg', '_tall',   ['_91_0', '_91_1'], ['EX_A1', 'EX_A2'], ['Character Images', 'Tall Character Images'  ]],
            # 'm':             ['jpg', '_icon',   ['_91_0', '_91_1'], ['EX_A1', 'EX_A2'], ['Character Images', 'Icon Character Images'  ]],
            # 's':             ['jpg', '_square', ['_91_0', '_91_1'], ['EX_A1', 'EX_A2'], ['Character Images', 'Square Character Images']],
            
            # 'cutin_special': ['jpg', '_CA',     ['_01', '_02'], ['A', 'B'], ['Character Images', 'Square Character Images']],
            
            # 'zoom':          ['png', '',        ['_01_st2'], ['A'], ['Character Images', 'Full Character Images']],
            # 'f':             ['jpg', '_tall',   ['_01_st2'], ['A'], ['Character Images', 'Tall Character Images']],
            # 'm':             ['jpg', '_icon',   ['_01_st2'], ['A'], ['Character Images', 'Icon Character Images']],
            # 's':             ['jpg', '_square', ['_01_st2'], ['A'], ['Character Images', 'Square Character Images']],
            # 'sd':            ['png', '_SD',     ['_01_st2'], ['A'], ['Character Images', 'Sprite Character Images']],
            
            #http://prd-game-a-granbluefantasy.akamaized.net/assets_en/img/sp/assets/npc/zoom/1010200300.png
            #http://prd-game-a-granbluefantasy.akamaized.net/assets_en/img/sp/assets/npc/f/1010200300.jpg
            #http://prd-game-a-granbluefantasy.akamaized.net/assets_en/img/sp/assets/npc/m/1010200300.jpg
            #http://prd-game-a-granbluefantasy.akamaized.net/assets_en/img/sp/assets/npc/s/1010200300.jpg
        }
        self.check_sp_asset(page, 'npc', 'Character', paths, False)

    def check_character_full(self, page):
        self.check_character(page)
        self.check_character_fs_skin(page)

    def check_character_fs_skin(self, page):
        self.check_sp_asset(page, 'npc', 'Character', self._character_fs_skin_paths(), False)

    def check_skill_icons(self, page):
        """
        Extract skill icon parameters from Character template and upload the icons.
        Looks for a1_icon, a2_icon, a3_icon, a4_icon, a1a_icon, a2a_icon, a3a_icon, a4a_icon,
        a1b_icon, a2b_icon, a3b_icon, a4b_icon parameters.
        Icons can be comma-separated (e.g., "Ability_m_2232_3.png,Ability_m_2233_3.png").
        """
        print(f'Checking skill icons for page {page.name}...')
        
        # Get page text and parse templates
        pagetext = page.text()
        wikicode = mwparserfromhell.parse(pagetext)
        templates = wikicode.filter_templates()
        
        # Find Character template
        character_template = None
        for template in templates:
            if template.name.strip() == 'Character':
                character_template = template
                break
        
        if not character_template:
            print('No Character template found; aborting.')
            return
        
        # Icon parameter names to check
        icon_params = [
            'a1_icon', 'a2_icon', 'a3_icon', 'a4_icon',
            'a1a_icon', 'a2a_icon', 'a3a_icon', 'a4a_icon',
            'a1b_icon', 'a2b_icon', 'a3b_icon', 'a4b_icon'
        ]
        
        # Extract all icon filenames from template parameters
        icon_filenames = []
        for param in character_template.params:
            param_name = param.name.strip()
            if param_name in icon_params:
                value = str(param.value).strip()
                if value:  # Skip empty params
                    # Split by comma to handle multiple icons
                    icons = [icon.strip() for icon in value.split(',') if icon.strip()]
                    icon_filenames.extend(icons)
        
        if not icon_filenames:
            print('No skill icons found in icon parameters; skipping.')
            return
        
        # Extract indices from icon filenames
        # Pattern: Ability_m_{index}.png -> extract {index}
        icon_indices = []
        for icon_filename in icon_filenames:
            # Match pattern like "Ability_m_2731_3.png" or "Ability_m_2232_3.png"
            match = re.match(r'Ability_m_([0-9_]+)\.png', icon_filename)
            if match:
                index = match.group(1)
                icon_indices.append((index, icon_filename))  # Store both index and original filename
            else:
                print(f'Warning: Could not extract index from icon filename: {icon_filename}')
        
        if not icon_indices:
            print('No valid icon indices found; skipping.')
            return
        
        print(f'Found {len(icon_indices)} skill icon(s) to upload.')
        
        # Status callback for progress updates
        def emit_status(stage, **kwargs):
            if hasattr(self, '_status_callback'):
                self._status_callback(stage, **kwargs)
        
        emit_status("downloading", total_urls=len(icon_indices), successful=0, failed=0)
        
        # Download and upload each icon
        url_template = (
            'https://prd-game-a-granbluefantasy.akamaized.net/assets_en/'
            'img/sp/ui/icon/ability/m/{0}.png'
        )
        
        category_text = '[[Category:Ability Icons]]'
        successful = 0
        failed = 0
        uploaded = 0
        duplicates = 0
        
        for index, original_filename in icon_indices:
            url = url_template.format(index)
            canonical_name = original_filename  # Use the exact filename from the param
            
            print(f'Processing icon: {canonical_name} (index: {index})')
            
            # Always try to download from CDN first to get SHA1 for verification
            success, sha1, size, io = self.get_image(url)
            if not success:
                # Download failed - check if file exists on wiki by name as fallback
                true_name = canonical_name.capitalize()
                file_page = self.wiki.pages["File:" + true_name]
                if file_page.exists and not file_page.redirect:
                    # File exists on wiki but CDN download failed - count as duplicate
                    duplicates += 1
                    print(f'Image {canonical_name} exists on wiki but CDN download failed (counted as duplicate).')
                    emit_status("downloading", successful=successful, failed=failed)
                else:
                    # Download failed and file doesn't exist on wiki
                    print(f'Skipping {canonical_name} (download failed and not found on wiki).')
                    failed += 1
                    emit_status("downloading", successful=successful, failed=failed)
                continue
            
            successful += 1
            emit_status("downloading", successful=successful, failed=failed)
            
            # Download succeeded - check SHA1 against wiki
            other_names = []  # No redirects needed for skill icons
            check_image_result = self.check_image(
                canonical_name,
                sha1,
                size,
                io,
                other_names,
            )
            
            if check_image_result is False:
                print(f'Checking image {canonical_name} failed! Skipping...')
                failed += 1
                continue
            elif check_image_result is not True:
                # Image already exists with a different name (SHA1 match found)
                duplicates += 1
                print(f'Image {canonical_name} found as duplicate: {check_image_result}')
                canonical_name = check_image_result
            else:
                uploaded += 1
                print(f'Successfully uploaded {canonical_name}')

            self.check_image_categories(canonical_name, ['Ability Icons'])
            
            # Small delay between uploads
            time.sleep(self.delay)
        
        emit_status("completed", successful=successful, uploaded=uploaded, duplicates=duplicates, failed=failed)
        print(f'Skill icon upload completed: {successful} downloaded, {uploaded} uploaded, {duplicates} duplicates, {failed} failed.')

    def check_summon(self, page):
        print('Checking page {0}...'.format(page.name))
        specs = self._get_summon_asset_specs()
        asset_ids = self._extract_asset_ids_from_template(page, 'Summon')

        if not asset_ids:
            print('No Summon asset ids found; aborting.')
            return

        download_tasks = self._build_asset_download_tasks('summon', page.name, asset_ids, specs)
        if not download_tasks:
            print('No Summon download tasks generated; nothing to do.')
            return

        self._process_download_tasks_sequential(download_tasks, 'Summon')

    def check_weapon(self, page):
        paths = {
            'b':  ['png', '',        ['', '_02', '_03'], ['A', 'B', 'C'], ['Weapon Images', 'Full Weapon Images'  ]],
            'ls': ['jpg', '_tall',   ['', '_02', '_03'], ['A', 'B', 'C'], ['Weapon Images', 'Tall Weapon Images'  ]],
            'm':  ['jpg', '_icon',   ['', '_02', '_03'], ['A', 'B', 'C'], ['Weapon Images', 'Icon Weapon Images'  ]],
            's':  ['jpg', '_square', ['', '_02', '_03'], ['A', 'B', 'C'], ['Weapon Images', 'Square Weapon Images']],
            'wsp': ['png', '_sprite', ['', '_02', '_03'], ['A', 'B', 'C'], ['Weapon Images', 'Weapon Sprites']],
            
            # 'b':  ['png', '',        [''], [''], ['Weapon Images', 'Full Weapon Images'  ]],
            # 'ls': ['jpg', '_tall',   [''], [''], ['Weapon Images', 'Tall Weapon Images'  ]],
            # 'm':  ['jpg', '_icon',   [''], [''], ['Weapon Images', 'Icon Weapon Images'  ]],
            # 's':  ['jpg', '_square', [''], [''], ['Weapon Images', 'Square Weapon Images']],
            # 'wsp': ['png', '_sprite', [''], [''], ['Weapon Images', 'Weapon Sprites']],
            
            #http://prd-game-a-granbluefantasy.akamaized.net/assets_en/img/sp/assets/weapon/b/1010200300.png
            #http://prd-game-a-granbluefantasy.akamaized.net/assets_en/img/sp/assets/weapon/ls/1010200300.jpg
            #http://prd-game-a-granbluefantasy.akamaized.net/assets_en/img/sp/assets/weapon/m/1010200300.jpg
            #http://prd-game-a-granbluefantasy.akamaized.net/assets_en/img/sp/assets/weapon/s/1010200300.jpg
            #http://prd-game-a-granbluefantasy.akamaized.net/assets_en/img/sp/cjs/1010200300.png
        }
        self.check_sp_asset(page, 'weapon', 'Weapon', paths, True)

    def _extract_asset_ids_from_template(self, page, template_name):
        """
        Locate asset ids defined inside the requested template.
        """
        pagetext = page.text()
        wikicode = mwparserfromhell.parse(pagetext)
        templates = wikicode.filter_templates()
        asset_ids = []
        seen_ids = set()

        for template in templates:
            if template.name.strip() != template_name:
                continue

            for param in template.params:
                if param.name.strip() != 'id':
                    continue

                asset_id = str(param.value).strip()
                asset_match = re.match(r'^{{{id\|([A-Za-z0-9_]+)}}}', asset_id)
                if asset_match is not None:
                    asset_id = asset_match.group(1)

                if asset_id and asset_id not in seen_ids:
                    seen_ids.add(asset_id)
                    asset_ids.append(asset_id)

        if not asset_ids:
            print(f'No asset ids found in template "{template_name}".')
        return asset_ids

    def _build_asset_variants(self, suffixes, labels):
        suffix_list = list(suffixes or [''])
        label_list = list(labels or [])
        if not label_list:
            label_list = [''] * len(suffix_list)
        if len(label_list) < len(suffix_list):
            label_list.extend([''] * (len(suffix_list) - len(label_list)))
        elif len(label_list) > len(suffix_list):
            suffix_list.extend([''] * (len(label_list) - len(suffix_list)))
        return tuple(AssetVariant(suffix=suffix_list[i], label=label_list[i]) for i in range(len(suffix_list)))

    def _build_asset_specs_from_paths(self, raw_paths):
        specs = []
        for section, params in raw_paths.items():
            extension, filename_suffix, suffixes, labels, categories = params
            specs.append(
                AssetSpec(
                    section=section,
                    extension=extension,
                    filename_suffix=filename_suffix,
                    variants=self._build_asset_variants(suffixes, labels),
                    categories=tuple(categories),
                )
            )
        return specs

    def _get_npc_asset_specs(self):
        raw_paths = {
            'zoom': ['png', '', ['_01'], [''], ['NPC Images', 'Full NPC Images']],
            'm': ['jpg', '_icon', ['_01'], [''], ['NPC Images', 'Icon NPC Images']],
        }
        return self._build_asset_specs_from_paths(raw_paths)

    def _get_summon_asset_specs(self):
        raw_paths = {
            'b': ['png', '',        ['', '_02', '_03', '_04'], ['A', 'B', 'C', 'D'], ['Summon Images', 'Full Summon Images']],
            'ls': ['jpg', '_tall',   ['', '_02', '_03', '_04'], ['A', 'B', 'C', 'D'], ['Summon Images', 'Tall Summon Images']],
            'm': ['jpg', '_icon',   ['', '_02', '_03', '_04'], ['A', 'B', 'C', 'D'], ['Summon Images', 'Icon Summon Images']],
            's': ['jpg', '_square', ['', '_02', '_03', '_04'], ['A', 'B', 'C', 'D'], ['Summon Images', 'Square Summon Images']],
            'party_main': ['jpg', '_party_main', ['', '_02', '_03', '_04'], ['A', 'B', 'C', 'D'], ['Summon Images', 'Party Main Summon Images']],
            'party_sub': ['jpg', '_party_sub', ['', '_02', '_03', '_04'], ['A', 'B', 'C', 'D'], ['Summon Images', 'Party Sub Summon Images']],
            'detail': ['png', '_detail', ['', '_02', '_03', '_04'], ['A', 'B', 'C', 'D'], ['Summon Images', 'Detail Summon Images']],
        }
        specs = self._build_asset_specs_from_paths(raw_paths)

        def build_skycompass_url(asset_type, asset_id, variant, spec):
            return (
                'https://media.skycompass.io/assets/archives/summons/'
                f'{asset_id}/detail_l.png'
            )

        def build_skycompass_canonical_name(asset_type, asset_id, variant, spec):
            return f'archives_summons_{asset_id}_detail_l.png'

        def build_skycompass_other_names(asset_name, asset_type, canonical_name, variant, variant_count, spec):
            return [f'{asset_name}_HD.png']

        specs.append(
            AssetSpec(
                section='skycompass',
                extension='png',
                filename_suffix='',
                variants=(AssetVariant('', ''),),
                categories=('Summon Images', 'Skycompass Images', 'Skycompass Summon Images'),
                section_label='skycompass',
                url_builder=build_skycompass_url,
                canonical_name_builder=build_skycompass_canonical_name,
                other_names_builder=build_skycompass_other_names,
            )
        )

        return specs

    def _build_asset_download_tasks(self, asset_type, asset_name, asset_ids, specs):
        download_tasks = []

        for asset_id in asset_ids:
            for spec in specs:
                variants = spec.variants or (AssetVariant('', ''),)
                variant_count = len(variants)
                for variant in variants:
                    url = spec.build_url(asset_type, asset_id, variant)
                    true_name = spec.build_canonical_name(asset_type, asset_id, variant)
                    other_names = spec.build_other_names(
                        asset_name,
                        asset_type,
                        true_name,
                        variant,
                        variant_count
                    )
                    download_tasks.append({
                        'url': url,
                        'true_name': true_name,
                        'other_names': other_names,
                        'categories': list(spec.categories),
                    })

        return download_tasks

    def _process_download_tasks_sequential(self, download_tasks, asset_label):
        if not download_tasks:
            print('No download tasks to process.')
            return

        total = len(download_tasks)
        uploaded = 0
        duplicates = 0
        failed = 0

        print(f'Beginning sequential processing of {total} {asset_label} images.')

        for index, task in enumerate(download_tasks, start=1):
            print(f'[{index}/{total}] Downloading {task["url"]}...')
            success, sha1, size, io_obj = self.get_image(task['url'])
            if not success:
                failed += 1
                continue

            check_result = self.check_image(task['true_name'], sha1, size, io_obj, task['other_names'])
            if check_result is False:
                failed += 1
                print(f'Upload validation failed for {task["true_name"]}.')
                continue

            final_name = task['true_name'] if check_result is True else check_result
            if check_result is True:
                uploaded += 1
            else:
                duplicates += 1

            self.check_image_categories(final_name, task['categories'])
            for other_name in task['other_names']:
                self.check_file_redirect(final_name, other_name)

            time.sleep(self.delay)
            self.check_file_double_redirect(final_name)

            processed = uploaded + duplicates
            if hasattr(self, '_status_callback'):
                self._status_callback(
                    "processing",
                    processed=processed,
                    total=total,
                    current_image=final_name
                )

        print(
            f'{asset_label} processing summary — uploaded: {uploaded}, '
            f'duplicates: {duplicates}, failed: {failed}, total: {total}.'
        )

        if hasattr(self, '_status_callback'):
            self._status_callback(
                "completed",
                processed=uploaded + duplicates,
                uploaded=uploaded,
                duplicates=duplicates,
                failed=failed,
                total_urls=total
            )


    def check_class_skin(self, page, filter_id):
        print(f'Checking ClassSkin template id {filter_id} on page {page.name}...')

        def emit_status(stage, **kwargs):
            if hasattr(self, '_status_callback'):
                self._status_callback(stage, **kwargs)

        pagetext = page.text()
        wikicode = mwparserfromhell.parse(pagetext)
        templates = wikicode.filter_templates()

        def clean_param(template, param_name):
            if not template.has(param_name):
                return ''
            raw_value = str(template.get(param_name).value)
            return mwparserfromhell.parse(raw_value).strip_code().strip()

        target_template = None
        for template in templates:
            if template.name.strip().lower() != 'classskin':
                continue
            template_id = clean_param(template, 'id')
            if template_id == filter_id:
                target_template = template
                break

        if target_template is None:
            print(f'No ClassSkin template with id {filter_id} found; aborting.')
            emit_status('completed', processed=0, uploaded=0, duplicates=0, failed=1, total_urls=0)
            return

        skin_name = clean_param(target_template, 'desc') or page.name
        weapon_id = clean_param(target_template, 'id_weapon')

        if skin_name:
            redirect_from = f"{skin_name} (MC)"
            redirect_target = f"Main Character#{skin_name}"
            try:
                print(f"Ensuring page redirect: {redirect_from} -> {redirect_target}")
                self.check_redirect(redirect_target, redirect_from)
            except Exception as redirect_error:  # pragma: no cover - best-effort logging
                print(f"Failed to create {redirect_from} redirect: {redirect_error}")

        base_categories = ['Outfit Images', 'Skin Outfit Images']
        icon_categories = ['Outfit Images', 'Icon Outfit Images']
        square_categories = ['Outfit Images', 'Square Outfit Images']
        raid_categories = ['Outfit Images', 'Raid Outfit Images']
        p_categories = ['Outfit Images', 'P Outfit Images']
        t_categories = ['Outfit Images', 'Babyl Outfit Images']
        talk_categories = ['Outfit Images', 'Talk Outfit Images']
        btn_categories = ['Outfit Images', 'Btn Outfit Images']
        result_ml_categories = ['Outfit Images', 'Result ML Outfit Images']
        result_categories = ['Outfit Images', 'Result Outfit Images']
        jobon_z_categories = ['Outfit Images', 'Jobon z Outfit Images']
        quest_categories = ['Outfit Images', 'Quest Outfit Images']
        sd_categories = ['Outfit Images', 'Sprite Outfit Images']
        pm_categories = ['Outfit Images', 'PM Outfit Images']
        sky_compass_categories = ['Outfit Images''Sky Compass Images', 'Sky Compass Outfit Images']
        skin_name_categories = ['Outfit Images', 'Skin Name Outfit Images']

        download_tasks = []

        def add_task(label, url, canonical, others, categories):
            download_tasks.append({
                'label': label,
                'url': url,
                'canonical': canonical,
                'other_names': others,
                'categories': categories,
            })

        base_url = (
            'https://prd-game-a1-granbluefantasy.akamaized.net/assets_en/'
            f'img/sp/assets/leader/skin/{filter_id}_01.png'
        )
        base_redirects = [f'{skin_name} skin.png'] if skin_name else []
        add_task('skin artwork', base_url, f'Leader_skin_{filter_id}_01.png', base_redirects, base_categories)

        icon_url = (
            'https://prd-game-a3-granbluefantasy.akamaized.net/assets_en/'
            f'img/sp/assets/leader/m/{filter_id}_01.jpg'
        )
        icon_redirects = [f'{skin_name} (MC) icon.jpg'] if skin_name else []
        add_task('mc icon', icon_url, f'Leader_m_{filter_id}_01.jpg', icon_redirects, icon_categories)

        square_url = (
            'https://prd-game-a4-granbluefantasy.akamaized.net/assets_en/'
            f'img/sp/assets/leader/s/{filter_id}_01.jpg'
        )
        square_redirects = [f'{skin_name} (MC) square.jpg'] if skin_name else []
        add_task('square (MC)', square_url, f'Leader_s_{filter_id}_01.jpg', square_redirects, square_categories)

        gender_specs = [
            {'value': 0, 'display': 'Gran'},
            {'value': 1, 'display': 'Djeeta'},
        ]

        square_hosts = {0: 'prd-game-a4-granbluefantasy.akamaized.net', 1: 'prd-game-a5-granbluefantasy.akamaized.net'}
        p_hosts = {0: 'prd-game-a4-granbluefantasy.akamaized.net', 1: 'prd-game-a4-granbluefantasy.akamaized.net'}
        t_hosts = {0: 'prd-game-a4-granbluefantasy.akamaized.net', 1: 'prd-game-a4-granbluefantasy.akamaized.net'}
        talk_hosts = {0: 'prd-game-a-granbluefantasy.akamaized.net', 1: 'prd-game-a-granbluefantasy.akamaized.net'}
        btn_hosts = {0: 'prd-game-a2-granbluefantasy.akamaized.net', 1: 'prd-game-a2-granbluefantasy.akamaized.net'}
        result_ml_hosts = {0: 'prd-game-a4-granbluefantasy.akamaized.net', 1: 'prd-game-a4-granbluefantasy.akamaized.net'}
        result_hosts = {0: 'prd-game-a4-granbluefantasy.akamaized.net', 1: 'prd-game-a4-granbluefantasy.akamaized.net'}
        jobon_z_hosts = {0: 'prd-game-a-granbluefantasy.akamaized.net', 1: 'prd-game-a-granbluefantasy.akamaized.net'}
        quest_hosts = {0: 'prd-game-a2-granbluefantasy.akamaized.net', 1: 'prd-game-a2-granbluefantasy.akamaized.net'}
        sd_hosts = {0: 'prd-game-a2-granbluefantasy.akamaized.net', 1: 'prd-game-a2-granbluefantasy.akamaized.net'}
        sky_hosts = {0: 'media.skycompass.io', 1: 'media.skycompass.io'}
        pm_hosts = {0: 'prd-game-a1-granbluefantasy.akamaized.net', 1: 'prd-game-a1-granbluefantasy.akamaized.net'}

        if weapon_id:
            for spec in gender_specs:
                gender_value = spec['value']
                alias = spec['display']

                raid_url = (
                    'https://prd-game-a1-granbluefantasy.akamaized.net/assets_en/'
                    f'img/sp/assets/leader/raid_normal/{filter_id}_{weapon_id}_{gender_value}_01.jpg'
                )
                gender_redirect = 'gran' if gender_value == 0 else 'djeeta'
                raid_other = [
                    f'Leader_raid_normal_{filter_id}_{gender_value}_01.jpg',
                    f'{page.name}_{gender_redirect}_profile.jpg',
                ]
                if skin_name:
                    raid_other.append(f'{skin_name} ({alias}) raid.jpg')
                add_task(
                    f'raid_normal ({alias})',
                    raid_url,
                    f'leader_raid_normal_{filter_id}_{weapon_id}_{gender_value}_01.jpg',
                    raid_other,
                    raid_categories,
                )

                square_host = square_hosts.get(gender_value, 'prd-game-a4-granbluefantasy.akamaized.net')
                square_variant_url = (
                    f'https://{square_host}/assets_en/img/sp/assets/leader/s/{filter_id}_{weapon_id}_{gender_value}_01.jpg'
                )
                square_variant_other = [f'Leader_s_{filter_id}_{gender_value}_01.jpg']
                if skin_name:
                    square_variant_other.append(f'{skin_name} ({alias}) square.jpg')
                add_task(
                    f'square ({alias})',
                    square_variant_url,
                    f'Leader_s_{filter_id}_{weapon_id}_{gender_value}_01.jpg',
                    square_variant_other,
                    square_categories,
                )

                p_host = p_hosts.get(gender_value, 'prd-game-a4-granbluefantasy.akamaized.net')
                p_url = (
                    f'https://{p_host}/assets_en/img/sp/assets/leader/p/{filter_id}_{weapon_id}_{gender_value}_01.png'
                )
                p_other = [f'{skin_name} ({alias}) p.png'] if skin_name else []
                add_task(
                    f'p asset ({alias})',
                    p_url,
                    f'Leader_p_{filter_id}_{weapon_id}_{gender_value}_01.png',
                    p_other,
                    p_categories,
                )

                t_host = t_hosts.get(gender_value, 'prd-game-a4-granbluefantasy.akamaized.net')
                t_url = (
                    f'https://{t_host}/assets_en/img/sp/assets/leader/t/{filter_id}_{weapon_id}_{gender_value}_01.png'
                )
                t_other = [f'{skin_name} ({alias}) t.png'] if skin_name else []
                add_task(
                    f't asset ({alias})',
                    t_url,
                    f'Leader_t_{filter_id}_{weapon_id}_{gender_value}_01.png',
                    t_other,
                    t_categories,
                )

                talk_host = talk_hosts.get(gender_value, 'prd-game-a-granbluefantasy.akamaized.net')
                talk_url = (
                    f'https://{talk_host}/assets_en/img/sp/assets/leader/talk/{filter_id}_{weapon_id}_{gender_value}_01.png'
                )
                talk_other = [f'{skin_name} ({alias}) talk.png'] if skin_name else []
                add_task(
                    f'talk asset ({alias})',
                    talk_url,
                    f'Leader_talk_{filter_id}_{weapon_id}_{gender_value}_01.png',
                    talk_other,
                    talk_categories,
                )

                btn_host = btn_hosts.get(gender_value, 'prd-game-a2-granbluefantasy.akamaized.net')
                btn_url = (
                    f'https://{btn_host}/assets_en/img/sp/assets/leader/btn/{filter_id}_{weapon_id}_{gender_value}_01.png'
                )
                btn_other = [f'{skin_name} ({alias}) btn.png'] if skin_name else []
                add_task(
                    f'btn asset ({alias})',
                    btn_url,
                    f'Leader_btn_{filter_id}_{weapon_id}_{gender_value}_01.png',
                    btn_other,
                    btn_categories,
                )

                result_ml_host = result_ml_hosts.get(gender_value, 'prd-game-a4-granbluefantasy.akamaized.net')
                result_ml_url = (
                    f'https://{result_ml_host}/assets_en/img/sp/assets/leader/result_ml/{filter_id}_{weapon_id}_{gender_value}_01.jpg'
                )
                result_ml_other = [f'{skin_name} ({alias}) result ml.jpg'] if skin_name else []
                add_task(
                    f'result_ml asset ({alias})',
                    result_ml_url,
                    f'Leader_result_ml_{filter_id}_{weapon_id}_{gender_value}_01.jpg',
                    result_ml_other,
                    result_ml_categories,
                )

                result_host = result_hosts.get(gender_value, 'prd-game-a4-granbluefantasy.akamaized.net')
                result_url = (
                    f'https://{result_host}/assets_en/img/sp/assets/leader/result/{filter_id}_{weapon_id}_{gender_value}_01.jpg'
                )
                result_other = [f'{skin_name} ({alias}) result.jpg'] if skin_name else []
                add_task(
                    f'result asset ({alias})',
                    result_url,
                    f'Leader_result_{filter_id}_{weapon_id}_{gender_value}_01.jpg',
                    result_other,
                    result_categories,
                )

                jobon_host = jobon_z_hosts.get(gender_value, 'prd-game-a-granbluefantasy.akamaized.net')
                jobon_url = (
                    f'https://{jobon_host}/assets_en/img/sp/assets/leader/jobon_z/{filter_id}_{weapon_id}_{gender_value}_01.png'
                )
                jobon_other = [f'{skin_name} ({alias}) jobon z.png'] if skin_name else []
                add_task(
                    f'jobon_z asset ({alias})',
                    jobon_url,
                    f'Leader_jobon_z_{filter_id}_{weapon_id}_{gender_value}_01.png',
                    jobon_other,
                    jobon_z_categories,
                )

                quest_host = quest_hosts.get(gender_value, 'prd-game-a2-granbluefantasy.akamaized.net')
                quest_url = (
                    f'https://{quest_host}/assets_en/img/sp/assets/leader/quest/{filter_id}_{weapon_id}_{gender_value}_01.jpg'
                )
                quest_other = [f'{skin_name} ({alias}) quest.jpg'] if skin_name else []
                add_task(
                    f'quest asset ({alias})',
                    quest_url,
                    f'Leader_quest_{filter_id}_{weapon_id}_{gender_value}_01.jpg',
                    quest_other,
                    quest_categories,
                )

                sd_host = sd_hosts.get(gender_value, 'prd-game-a2-granbluefantasy.akamaized.net')
                sd_url = (
                    f'https://{sd_host}/assets_en/img/sp/assets/leader/sd/{filter_id}_{weapon_id}_{gender_value}_01.png'
                )
                sd_other = [f'{skin_name} ({alias}) SD.png'] if skin_name else []
                add_task(
                    f'sd asset ({alias})',
                    sd_url,
                    f'Leader_sd_{filter_id}_{weapon_id}_{gender_value}_01.png',
                    sd_other,
                    sd_categories,
                )

                sky_host = sky_hosts.get(gender_value, 'media.skycompass.io')
                sky_url = (
                    f'https://{sky_host}/assets/customizes/jobs/1138x1138/{filter_id}_{gender_value}.png'
                )
                sky_other = []
                if skin_name:
                    sky_other.append(f'{skin_name} ({alias}).png')
                    sky_other.append(f'{skin_name} ({alias}) HD.png')
                add_task(
                    f'sky compass asset ({alias})',
                    sky_url,
                    f'jobs_1138x1138_{filter_id}_{gender_value}.png',
                    sky_other,
                    sky_compass_categories,
                )

                pm_host = pm_hosts.get(gender_value, 'prd-game-a1-granbluefantasy.akamaized.net')
                pm_url = (
                    f'https://{pm_host}/assets_en/img/sp/assets/leader/pm/{filter_id}_{weapon_id}_{gender_value}_01.png'
                )
                pm_other = [f'{skin_name} ({alias}) pm.png'] if skin_name else []
                add_task(
                    f'pm asset ({alias})',
                    pm_url,
                    f'Leader_pm_{filter_id}_{weapon_id}_{gender_value}_01.png',
                    pm_other,
                    pm_categories,
                )
        else:
            print('No id_weapon parameter in template; skipping gendered assets.')

        skin_name_url = (
            'https://prd-game-a-granbluefantasy.akamaized.net/assets_en/img/sp/event/common/teamraid/assets/skin_name/'
            f'{filter_id}.png'
        )
        skin_name_other = [f'{skin_name} skin_name.png'] if skin_name else []
        add_task('skin name asset', skin_name_url, f'Assets_skin_name_{filter_id}.png', skin_name_other, skin_name_categories)

        if not download_tasks:
            print('No ClassSkin assets queued; aborting.')
            emit_status('completed', processed=0, uploaded=0, duplicates=0, failed=0, total_urls=0)
            return

        emit_status('downloading', successful=0, failed=0, total=len(download_tasks))

        successful_downloads = []
        failed_downloads = 0
        for task in download_tasks:
            print(f"Downloading {task['label']}: {task['url']}")
            success, sha1, size, io_obj = self.get_image(task['url'])
            if success:
                successful_downloads.append((task, sha1, size, io_obj))
            else:
                failed_downloads += 1
            emit_status('downloading', successful=len(successful_downloads), failed=failed_downloads, total=len(download_tasks))

        if not successful_downloads:
            print('All ClassSkin downloads failed; aborting.')
            emit_status('completed', processed=0, uploaded=0, duplicates=0, failed=failed_downloads, total_urls=len(download_tasks))
            return

        emit_status('downloaded', successful=len(successful_downloads), failed=failed_downloads, total=len(download_tasks))

        uploaded = 0
        duplicates = 0
        upload_failures = 0
        processed = 0

        for task, sha1, size, io_obj in successful_downloads:
            check_result = self.check_image(task['canonical'], sha1, size, io_obj, task['other_names'])
            processed += 1

            if check_result is False:
                upload_failures += 1
                emit_status('processing', processed=processed, total=len(successful_downloads), current_image=task['canonical'])
                continue

            final_name = task['canonical'] if check_result is True else check_result
            if check_result is True:
                uploaded += 1
            else:
                duplicates += 1

            self.check_image_categories(final_name, task['categories'])
            for other_name in task['other_names']:
                self.check_file_redirect(final_name, other_name)

            time.sleep(self.delay)
            self.check_file_double_redirect(final_name)

            emit_status('processing', processed=processed, total=len(successful_downloads), current_image=final_name)

        total_failed = failed_downloads + upload_failures

        print(
            'ClassSkin processing summary — '
            f'uploaded: {uploaded}, '
            f'duplicates: {duplicates}, '
            f'failed: {total_failed}, '
            f'total requested: {len(download_tasks)}.'
        )

        emit_status(
            'completed',
            processed=processed,
            uploaded=uploaded,
            duplicates=duplicates,
            failed=total_failed,
            total_urls=len(download_tasks),
        )


    def check_npc(self, page):
        print('Checking page {0}...'.format(page.name))
        specs = self._get_npc_asset_specs()
        asset_ids = self._extract_asset_ids_from_template(page, 'Non-party Character')

        if not asset_ids:
            print('No NPC asset ids found; aborting.')
            return

        download_tasks = self._build_asset_download_tasks('npc', page.name, asset_ids, specs)
        if not download_tasks:
            print('No NPC download tasks generated; nothing to do.')
            return

        self._process_download_tasks_sequential(download_tasks, 'NPC')

    def check_artifact(self, page):
        paths = {
            'hdr':  ['png', '',        [''], [''], ['Artifact Images', 'Full Artifact Images' ]],
            'm':  ['jpg', '_icon',   [''], [''], ['Artifact Images', 'Icon Artifact Images'  ]],
            's':  ['jpg', '_square', [''], [''], ['Artifact Images', 'Square Artifact Images']],
        }
        self.check_sp_asset(page, 'artifact', 'Artifact', paths, False)

    def check_rucksack(self, page):
        paths = {
            'base':  ['png', '',        [''], [''], ['Rucksack Battles Images', 'Base Rucksack Battles Images' ]],
            # 'bright':  ['png', '',   [''], [''], ['Rucksack Battles Images', 'Bright Rucksack Battles Images'  ]],
            # 'shadow':  ['png', '', [''], [''], ['Rucksack Battles Images', 'Shadow Rucksack Battles Images']],
        }
        self.check_sp_rucksack_asset(page, 'item', 'User:AdlaiT/RucksackItem', paths, False)

    def check_skin(self, page):
        paths = {
            # 'zoom':          ['png', '',        ['_01', '_01_0', '_01_1', '_81', '_82'], ['A', 'A0', 'A1', 'ST', 'ST2'], ['Outfit Images', 'Full Outfit Images'  ]],
            # 'sd':            ['png', '_SD',     ['_01', '_81', '_82'], ['A', 'ST', 'ST2'], ['Outfit Images', 'Sprite Outfit Images'  ]],
            # 'f':             ['jpg', '_tall',   ['_01', '_81', '_82'], ['A', 'ST', 'ST2'], ['Outfit Images', 'Tall Outfit Images'  ]],
            # 'm':             ['jpg', '_icon',   ['_01', '_81', '_82'], ['A', 'ST', 'ST2'], ['Outfit Images', 'Icon Outfit Images'  ]],
            # 's':             ['jpg', '_square', ['_01', '_81', '_82'], ['A', 'ST', 'ST2'], ['Outfit Images', 'Square Outfit Images'  ]],
            # 'skin':          ['png', '_skin',   ['_01', '_81', '_82'], ['A', 'ST', 'ST2'], ['Outfit Images', 'Skin Outfit Images'  ]],
            # 'detail':        ['png', '_detail', ['_01', '_81', '_82'], ['A', 'ST', 'ST2'], ['Outfit Images', 'Detail Outfit Character Images']],
            # 't':             ['png', '_babyl',  ['_01', '_81', '_82'], ['A', 'ST', 'ST2'], ['Outfit Images', 'Babyl Outfit Character Images']],
            # 'raid_normal':   ['jpg', '_raid',   ['_01', '_81', '_82'], ['A', 'ST', 'ST2'], ['Outfit Images', 'Raid Outfit Character Images']],
            # 'cutin_special': ['jpg', '_cutin',  ['_01', '_81', '_82'], ['A', 'ST', 'ST2'], ['Outfit Images', 'Cutin Outfit Character Images']],
            # 'raid_chain':    ['jpg', '_chain',  ['_01', '_01_01', '_81', '_82'], ['A', 'A01', 'ST', 'ST2'], ['Outfit Images', 'Chain Burst Outfit Character Images']],
            # 'quest':         ['jpg', '_quest',  ['_01', '_81', '_82'], ['A', 'ST', 'ST2'], ['Outfit Images', 'Quest Outfit Character Images']],
            # 'qm':            ['png', '_qm',     ['_01', '_81', '_82'], ['A', 'ST', 'ST2'], ['Outfit Images', 'QM Outfit Character Images']],


            'zoom':          ['png', '',        ['_01', '_01_0', '_01_1', '_81', '_82'], ['A', 'A0', 'A1', 'ST', 'ST2'], ['Outfit Images', 'Full Outfit Images'  ]],
            'sd':            ['png', '_SD',     ['_01', '_01_0', '_01_1', '_81', '_82'], ['A', 'A0', 'A1', 'ST', 'ST2'], ['Outfit Images', 'Sprite Outfit Images'  ]],
            'f':             ['jpg', '_tall',   ['_01', '_01_0', '_01_1', '_81', '_82'], ['A', 'A0', 'A1', 'ST', 'ST2'], ['Outfit Images', 'Tall Outfit Images'  ]],
            'm':             ['jpg', '_icon',   ['_01', '_01_0', '_01_1', '_81', '_82'], ['A', 'A0', 'A1', 'ST', 'ST2'], ['Outfit Images', 'Icon Outfit Images'  ]],
            's':             ['jpg', '_square', ['_01', '_01_0', '_01_1', '_81', '_82'], ['A', 'A0', 'A1', 'ST', 'ST2'], ['Outfit Images', 'Square Outfit Images'  ]],
            'skin':          ['png', '_skin',   ['_01', '_01_0', '_01_1', '_81', '_82'], ['A', 'A0', 'A1', 'ST', 'ST2'], ['Outfit Images', 'Skin Outfit Images'  ]],
            'detail':        ['png', '_detail', ['_01', '_01_0', '_01_1', '_81', '_82'], ['A', 'A0', 'A1', 'ST', 'ST2'], ['Outfit Images', 'Detail Outfit Character Images']],
            't':             ['png', '_babyl',  ['_01', '_01_0', '_01_1', '_81', '_82'], ['A', 'A0', 'A1', 'ST', 'ST2'], ['Outfit Images', 'Babyl Outfit Character Images']],
            'raid_normal':   ['jpg', '_raid',   ['_01', '_01_0', '_01_1', '_81', '_82'], ['A', 'A0', 'A1', 'ST', 'ST2'], ['Outfit Images', 'Raid Outfit Character Images']],
            'cutin_special': ['jpg', '_cutin',  ['_01', '_01_0', '_01_1', '_81', '_82'], ['A', 'A0', 'A1', 'ST', 'ST2'], ['Outfit Images', 'Cutin Outfit Character Images']],
            'raid_chain':    ['jpg', '_chain',  ['_01', '_01_0', '_01_1', '_81', '_82'], ['A', 'A0', 'A1', 'ST', 'ST2'], ['Outfit Images', 'Chain Burst Outfit Character Images']],
            'quest':         ['jpg', '_quest',  ['_01', '_01_0', '_01_1', '_81', '_82'], ['A', 'A0', 'A1', 'ST', 'ST2'], ['Outfit Images', 'Quest Outfit Character Images']],
            'qm':            ['png', '_qm',     ['_01', '_01_0', '_01_1', '_81', '_82'], ['A', 'A0', 'A1', 'ST', 'ST2'], ['Outfit Images', 'QM Outfit Character Images']],

            'f_skin': [
                'jpg',
                '_tall',
                [
                    '_01_s1', '_01_s2', '_01_s3', '_01_s4', '_01_s5', '_01_s6',
                    '_01_1_s1', '_01_1_s2', '_01_1_s3', '_01_1_s4', '_01_1_s5', '_01_1_s6',
                    '_81_s1', '_81_s2', '_81_s3', '_81_s4', '_81_s5', '_81_s6',
                    '_82_s1', '_82_s2', '_82_s3', '_82_s4', '_82_s5', '_82_s6',
                    '_81_0_s1', '_81_0_s2', '_81_0_s3', '_81_0_s4', '_81_0_s5', '_81_0_s6',
                    '_82_1_s1', '_82_1_s2', '_82_1_s3', '_82_1_s4', '_82_1_s5', '_82_1_s6'
                ],
                [
                    'fire', 'water', 'earth', 'wind', 'light', 'dark',
                    'A1_fire', 'A1_water', 'A1_earth', 'A1_wind', 'A1_light', 'A1_dark',
                    'ST_fire', 'ST_water', 'ST_earth', 'ST_wind', 'ST_light', 'ST_dark',
                    'ST2_fire', 'ST2_water', 'ST2_earth', 'ST2_wind', 'ST2_light', 'ST2_dark',
                    'ST0_fire', 'ST0_water', 'ST0_earth', 'ST0_wind', 'ST0_light', 'ST0_dark',
                    'ST21_fire', 'ST21_water', 'ST21_earth', 'ST21_wind', 'ST21_light', 'ST21_dark'
                ],
                ['Outfit Images', 'Tall Skin Character Images']
            ],
            's_skin': [
                'jpg',
                '_square',
                [
                    '_01_s1', '_01_s2', '_01_s3', '_01_s4', '_01_s5', '_01_s6',
                    '_01_1_s1', '_01_1_s2', '_01_1_s3', '_01_1_s4', '_01_1_s5', '_01_1_s6',
                    '_81_s1', '_81_s2', '_81_s3', '_81_s4', '_81_s5', '_81_s6',
                    '_82_s1', '_82_s2', '_82_s3', '_82_s4', '_82_s5', '_82_s6',
                    '_81_0_s1', '_81_0_s2', '_81_0_s3', '_81_0_s4', '_81_0_s5', '_81_0_s6',
                    '_82_1_s1', '_82_1_s2', '_82_1_s3', '_82_1_s4', '_82_1_s5', '_82_1_s6'
                ],
                [
                    'fire', 'water', 'earth', 'wind', 'light', 'dark',
                    'A1_fire', 'A1_water', 'A1_earth', 'A1_wind', 'A1_light', 'A1_dark',
                    'ST_fire', 'ST_water', 'ST_earth', 'ST_wind', 'ST_light', 'ST_dark',
                    'ST2_fire', 'ST2_water', 'ST2_earth', 'ST2_wind', 'ST2_light', 'ST2_dark',
                    'ST0_fire', 'ST0_water', 'ST0_earth', 'ST0_wind', 'ST0_light', 'ST0_dark',
                    'ST21_fire', 'ST21_water', 'ST21_earth', 'ST21_wind', 'ST21_light', 'ST21_dark'
                ],
                ['Outfit Images', 'Square Skin Character Images']
            ],

            #                                      ['Outfit Images', 'Tall Outfit Images' ]],
            # 'sd_ability':    ['png', '',   ['_01_ability', '_01_stbwait', '_01_attack', '_01_double', '_01_vs_motion_1', '_01_vs_motion_2', '_01_vs_motion_3', '_ab_motion'], [' SD_ability', ' SD_stbwait', ' SD_attack', ' SD_double', ' SD_vs_motion_1', ' SD_vs_motion_2', ' SD_vs_motion_3', ' SD_ab_motion'], ['Outfit Images', 'Skin Outfit Images'  ]],
            # 'my':          ['png', '_my',        ['_01', '_81'], ['','_alt'], ['Outfit Images', 'Home Outfit Images' ]],

            #http://prd-game-a-granbluefantasy.akamaized.net/assets_en/img/sp/assets/npc/zoom/1010200300.png
            #http://prd-game-a-granbluefantasy.akamaized.net/assets_en/img/sp/assets/npc/f/1010200300.jpg
            #http://prd-game-a-granbluefantasy.akamaized.net/assets_en/img/sp/assets/npc/m/1010200300.jpg
            #http://prd-game-a-granbluefantasy.akamaized.net/assets_en/img/sp/assets/npc/s/1010200300.jpg
        }
        self.check_sp_skin_asset(page, 'npc', 'CharSkin', paths, False)

    def check_sp_asset(self, page, asset_type, asset_template, paths, check_inherit=False):
        print('Checking page {0}...'.format(page.name))
        asset_id = ''
        asset_name = page.name
        base_name = 'unknown'
        element_names = ['Incendo', 'Aqua', 'Terra', 'Ventus', 'Lumen', 'Nyx']
        if check_inherit and ('(' in asset_name):
            base_name = asset_name.partition('(')[0].strip()
        if check_inherit and any(x in asset_name for x in element_names):
            base_name = asset_name.rsplit(' ', 1)[0] + ' (Element)'

        pagetext = page.text()
        wikicode = mwparserfromhell.parse(pagetext)
        templates = wikicode.filter_templates()
        
        # Collect all URLs to download first
        download_tasks = []
        total_urls_generated = 0
        
        for template in templates:
            template_name = template.name.strip()

            if (template_name != asset_template):
                if (template_name.startswith('Weapon/Common/')):
                    pass
                elif (template_name != ':{0}'.format(base_name)):
                    continue

            asset_ids = []
            weapon_type = None
            style_suffix = ''

            for param in template.params:
                param_name = param.name.strip()
                if param_name == 'id':
                    asset_id = param.value.strip()
                    asset_match = re.match(r'^{{{id\|([A-Za-z0-9_]+)}}}', asset_id)
                    if asset_match != None:
                        asset_id = asset_match.group(1)
                    asset_ids.append(asset_id)
                elif (
                    asset_template == 'Character'
                    and asset_type == 'npc'
                    and param_name == 'style_id'
                ):
                    style_value = str(param.value).strip()
                    style_value = (
                        mwparserfromhell.parse(style_value)
                        .strip_code()
                        .strip()
                    )
                    if style_value.isdigit():
                        style_id = int(style_value)
                        if style_id >= 2:
                            style_suffix = f'_st{style_id}'
                elif asset_type == 'weapon' and param_name == 'weapon':
                    weapon_value = str(param.value).strip()
                    weapon_value = mwparserfromhell.parse(weapon_value).strip_code().strip().lower()
                    if weapon_value:
                        weapon_type = weapon_value

            for asset_id in asset_ids:
                for section, params in paths.items():
                    if (
                        section == 'wsp'
                        and asset_type == 'weapon'
                        and weapon_type == 'melee'
                    ):
                        melee_variants = [('_1', '1'), ('_2', '2')]
                        for suffix, label in melee_variants:
                            url = (
                                'http://prd-game-a-granbluefantasy.akamaized.net/assets_en/'
                                'img/sp/cjs/{0}{1}.{2}'
                            ).format(
                                asset_id,
                                suffix,
                                params[0]
                            )
                            true_name = "{0} sp {1} {2}.{3}".format(
                                asset_type.capitalize(),
                                asset_id,
                                label,
                                params[0]
                            )
                            other_names = [
                                f'{asset_name} sprite{label}.{params[0]}'
                            ]
                            download_tasks.append({
                                'url': url,
                                'true_name': true_name,
                                'other_names': other_names,
                                'categories': params[4]
                            })
                            total_urls_generated += 1
                        continue

                    versions = len(params[2])
                    for version in range(versions):
                        variant_suffix = '{0}{1}'.format(
                            params[2][version],
                            style_suffix
                        )
                        if section == 'wsp':
                            url = (
                                'http://prd-game-a-granbluefantasy.akamaized.net/assets_en/'
                                'img/sp/cjs/{0}{1}.{2}'
                            ).format(
                                asset_id,
                                variant_suffix,
                                params[0]
                            )
                            section_label = 'sp'
                        elif section == 'skycompass_zoom':
                            url = (
                                'https://media.skycompass.io/'
                                'assets/customizes/characters/1138x1138/{0}{1}.{2}'
                            ).format(
                                asset_id,
                                variant_suffix,
                                params[0]
                            )
                            section_label = section
                        elif section == 'f_skin':
                            url = (
                                'http://prd-game-a-granbluefantasy.akamaized.net/assets_en/'
                                'img/sp/assets/{0}/{1}/{2}{3}.{4}'
                            ).format(
                                asset_type,
                                'f/skin',
                                asset_id,
                                variant_suffix,
                                params[0]
                            )
                            section_label = section
                        elif section == 's_skin':
                            url = (
                                'http://prd-game-a-granbluefantasy.akamaized.net/assets_en/'
                                'img/sp/assets/{0}/{1}/{2}{3}.{4}'
                            ).format(
                                asset_type,
                                's/skin',
                                asset_id,
                                variant_suffix,
                                params[0]
                            )
                            section_label = section
                        else:
                            url = (
                                'http://prd-game-a-granbluefantasy.akamaized.net/assets_en/'
                                'img/sp/assets/{0}/{1}/{2}{3}.{4}'
                            ).format(
                                asset_type,
                                section,
                                asset_id,
                                variant_suffix,
                                params[0]
                            )
                            section_label = section

                        if section == 'skycompass_zoom':
                            true_name = "characters_1138x1138_{0}{1}.{2}".format(
                                asset_id,
                                variant_suffix,
                                params[0]
                            )
                        elif section == 'f_skin':
                            true_name = "{0}_f_skin_{1}{2}.{3}".format(
                                asset_type.lower(),
                                asset_id,
                                variant_suffix,
                                params[0]
                            )
                        elif section == 's_skin':
                            true_name = "{0}_s_skin_{1}{2}.{3}".format(
                                asset_type.lower(),
                                asset_id,
                                variant_suffix,
                                params[0]
                            )
                        elif asset_type == 'npc' and section in ('my', 'result_lvup'):
                            true_name = "{0}_{1}_{2}{3}.{4}".format(
                                asset_type.capitalize(),
                                section,
                                asset_id,
                                variant_suffix,
                                params[0]
                            )
                        else:
                            true_name = "{0} {1} {2}{3}.{4}".format(
                                asset_type.capitalize(),
                                section_label,
                                asset_id,
                                variant_suffix,
                                params[0]
                            )
                        
                        other_names = []
                        if (versions < 2) or (params[3][version] == 'A'):
                            other_names.append(
                                '{0}{1}.{2}'.format(
                                    asset_name,
                                    params[1],
                                    params[0]
                                )
                            )

                        if (versions > 1):
                            joiner = ''
                            if params[1] == '':
                                joiner = ' ' if params[3][version] != '' else ''
                            other_names.append(
                                '{0}{1}{2}.{3}'.format(
                                    asset_name,
                                    params[1],
                                    joiner + params[3][version],
                                    params[0]
                                )
                            )
                        
                        download_tasks.append({
                            'url': url,
                            'true_name': true_name,
                            'other_names': other_names,
                            'categories': params[4],
                        })
                        total_urls_generated += 1
        
        # Download all images concurrently
        if download_tasks:
            urls = [task['url'] for task in download_tasks]
            use_concurrent_downloads = not self._use_paced_local_downloads
            if use_concurrent_downloads:
                print(f"Starting concurrent download of {len(urls)} images...")
                print(f"Sample URLs: {urls[:2]}...")
            else:
                print(
                    f"Using paced sequential download for {len(urls)} images "
                    f"(no proxy and probe delay configured)."
                )
            
            download_results = []
            pending_urls = []
            concurrent_timeout = 900  # 15-minute safeguard
            
            try:
                if use_concurrent_downloads:
                    # Run async function in current thread
                    loop = asyncio.new_event_loop()
                    asyncio.set_event_loop(loop)
                    try:
                        download_task = self.get_images_concurrent(
                            urls,
                            timeout_seconds=concurrent_timeout,
                            progress_interval=25,
                        )
                        download_results, pending_urls = loop.run_until_complete(download_task)
                    finally:
                        loop.close()
                else:
                    for url in urls:
                        success, sha1, size, io_obj = self.get_image(url)
                        download_results.append((url, success, sha1, size, io_obj))
                
                completed_url_set = {url for url, _, _, _, _ in download_results}
                
                if pending_urls:
                    print(f"Sequentially downloading remaining {len(pending_urls)} URLs after timeout...")
                    for url in pending_urls:
                        if url in completed_url_set:
                            continue
                        success, sha1, size, io_obj = self.get_image(url)
                        download_results.append((url, success, sha1, size, io_obj))
                
                total_requested = len(download_tasks)
                total_received = len(download_results)
                
                if total_received < total_requested:
                    # Fill any missing URLs with explicit failures to keep counts honest
                    task_urls = {task['url'] for task in download_tasks}
                    missing_urls = task_urls - {url for url, *_ in download_results}
                    if missing_urls:
                        print(f"WARNING: Missing results for {len(missing_urls)} URLs; marking as failed.")
                        for url in missing_urls:
                            download_results.append((url, False, "", 0, False))
                
                successful = sum(1 for _, success, _, _, _ in download_results if success)
                failed = len(download_results) - successful
                
                print("Download Status Report:")
                print(f"  Successful (200): {successful}")
                print(f"  Failed (404/errors): {failed}")
                print(f"  Total attempted: {len(download_results)}")
                print(f"  Total URLs requested: {len(download_tasks)}")
                
                if hasattr(self, '_status_callback'):
                    self._status_callback("downloaded", successful=successful, failed=failed, total=len(download_results))
                        
            except Exception as e:
                print(f"Concurrent download failed: {e}")
                import traceback
                traceback.print_exc()
                print("Falling back to sequential downloads...")
                
                # Fallback to sequential method - ensure ALL tasks are processed
                download_results = []
                for task in download_tasks:
                    success, sha1, size, io_obj = self.get_image(task['url'])
                    download_results.append((task['url'], success, sha1, size, io_obj))
                
                successful = sum(1 for _, success, _, _, _ in download_results if success)
                failed = len(download_results) - successful
                print(f"Sequential Download Status Report:")
                print(f"  Successful (200): {successful}")
                print(f"  Failed (404/errors): {failed}")
                print(f"  Total attempted: {len(download_results)}")
                
                if hasattr(self, '_status_callback'):
                    self._status_callback("downloaded", successful=successful, failed=failed, total=len(download_results))
            
            # Process results with wiki operations (keep delays for wiki politeness)
            # Match results to tasks by URL to handle reordering in fallback cases
            url_to_task = {task['url']: task for task in download_tasks}
            url_to_result = {url: (success, sha1, size, io_obj) for url, success, sha1, size, io_obj in download_results}
            
            images_processed = 0
            images_uploaded = 0
            images_duplicate = 0
            images_failed = 0
            
            # Process tasks in original order to maintain consistency
            for task in download_tasks:
                url = task['url']
                if url not in url_to_result:
                    # This shouldn't happen, but skip if it does
                    print(f"WARNING: No result found for URL: {url}")
                    continue
                
                success, sha1, size, io_obj = url_to_result[url]
                if success:
                    images_processed += 1
                    print(f"Processing image {images_processed}/{successful}: {task['true_name']}")
                    
                    if hasattr(self, '_status_callback'):
                        self._status_callback("processing", processed=images_processed, total=successful, current_image=task['true_name'])
                    
                    check_image_result = self.check_image(
                        task['true_name'],
                        sha1,
                        size,
                        io_obj,
                        task['other_names'],
                    )
                    
                    if check_image_result == True:
                        images_uploaded += 1
                    elif check_image_result == False:
                        images_failed += 1
                        print('Checking image {0} failed! Skipping...'.format(task['true_name']))
                        continue
                    else:
                        # Result is a string - means duplicate was found and renamed
                        images_duplicate += 1
                        task['true_name'] = check_image_result
                    
                    self.check_image_categories(task['true_name'], task['categories'])

                    for other_name in task['other_names']:
                        self.check_file_redirect(task['true_name'], other_name)

                    time.sleep(self.delay)

                    self.check_file_double_redirect(task['true_name'])
            
            print(f"Processing Summary:")
            print(f"  Images downloaded: {successful}")
            print(f"  Images uploaded: {images_uploaded}")
            print(f"  Images found as duplicates: {images_duplicate}")
            print(f"  Images failed processing: {images_failed}")
            print(f"  Download failures: {failed}")
            print(f"  Total URLs checked: {total_urls_generated}")
            
            if hasattr(self, '_status_callback'):
                self._status_callback("completed", processed=images_processed, uploaded=images_uploaded, duplicates=images_duplicate, total_urls=total_urls_generated)


    def check_sp_rucksack_asset(self, page, asset_type, asset_template, paths, check_inherit=False):
        print('Checking page {0}...'.format(page.name))
        asset_id = ''
        asset_name = page.name
        base_name = 'unknown'

        pagetext = page.text()
        wikicode = mwparserfromhell.parse(pagetext)
        templates = wikicode.filter_templates()
        for template in templates:
            template_name = template.name.strip()

            if (template_name != asset_template):
                if (template_name.startswith('Weapon/Common/')):
                    pass
                elif (template_name != ':{0}'.format(base_name)):
                    continue

            asset_ids = []

            for param in template.params:
                param_name = param.name.strip()
                if param_name == 'id':
                    asset_id = param.value.strip()
                    #asset_id = asset_id.replace('_note', '')
                    asset_match = re.match(r'^{{{id\|([A-Za-z0-9_]+)}}}', asset_id)
                    if asset_match != None:
                        asset_id = asset_match.group(1)
                    asset_ids.append(asset_id)

            for asset_id in asset_ids:
                for section, params in paths.items():
                    versions = len(params[2])
                    version = 0
                    while version < versions:
                        # if section == 'wsp':
                        #     url = (
                        #         'https://prd-game-a-granbluefantasy.akamaized.net/assets_en/img/sp/event/revival012/minigame/assets/item/{0}/{1}.png'
                        #     ).format(
                        #         asset_id,
                        #         params[2][version]
                        #     )
                        #     section = 'sp'
                            
                        # else:
                        url = (
                            'http://prd-game-a-granbluefantasy.akamaized.net/assets_en/'
                            'img/sp/event/revival012/minigame/assets/{0}/{1}/{2}.{3}'
                        ).format(
                            asset_type,
                            section,
                            asset_id,
                            params[0]
                        )

                        success, sha1, size, io = self.get_image(url)
                        if success:
                            true_name = "{0} {1} {2}.{3}".format(
                                asset_type,
                                section,
                                asset_id,
                                params[0]
                            )
                            other_names = []

                            # if (versions < 2) or (params[3][version] == 'A'):
                            #     other_names.append(
                            #         '{0}{1}.{2}'.format(
                            #             asset_name,
                            #             params[1],
                            #             params[0]
                            #         )
                            #     )

                            # if (versions > 1):
                            #     other_names.append(
                            #         '{0}{1}{2}.{3}'.format(
                            #             asset_name,
                            #             params[1],
                            #             (' ' if (params[1] == '' and params[3][version] != '') else '') + params[3][version],
                            #             params[0]
                            #         )
                            #     )

                            # true_name may be changed by
                            check_image_result = self.check_image(true_name, sha1, size, io, other_names)
                            if check_image_result == True:
                                pass
                            elif check_image_result == False:
                                print('Checking image {0} failed! Skipping...'.format(true_name))
                                version += 1
                                continue
                            else:
                                true_name = check_image_result
                            self.check_image_categories(true_name, params[4])

                            for other_name in other_names:
                                self.check_file_redirect(true_name, other_name)

                            time.sleep(self.delay)

                            self.check_file_double_redirect(true_name)


                        version += 1

    def check_sp_skin_asset(self, page, asset_type, asset_template, paths, check_inherit=False):
        print('Checking page {0}...'.format(page.name))
        asset_id = ''
        asset_name = page.name
        base_name = 'MC'
        element_names = ['Incendo', 'Aqua', 'Terra', 'Ventus', 'Lumen', 'Nyx']
        if check_inherit and ('(' in asset_name):
            base_name = asset_name.partition('(')[0].strip()
        if check_inherit and any(x in asset_name for x in element_names):
            base_name = asset_name.rsplit(' ', 1)[0] + ' (Element)'

        pagetext = page.text()
        wikicode = mwparserfromhell.parse(pagetext)
        templates = wikicode.filter_templates()
        def emit_status(stage, **kwargs):
            if hasattr(self, '_status_callback'):
                self._status_callback(stage, **kwargs)

        successful_downloads = 0
        download_failures = 0
        upload_failures = 0
        processed = 0
        uploaded = 0
        duplicates = 0
        total_urls = 0
        emit_status('downloading', successful=0, failed=0, total=0)
        for template in templates:
            template_name = template.name.strip()

            if (template_name != asset_template):
                if (template_name.startswith('Weapon/Common/')):
                    pass
                elif (template_name != ':{0}'.format(base_name)):
                    continue

            asset_ids = []

            for param in template.params:
                param_name = param.name.strip()
                if param_name == 'id':
                    asset_id = param.value.strip()
                    asset_match = re.match(r'^{{{id\|([A-Za-z0-9_]+)}}}', asset_id)
                    if asset_match != None:
                        asset_id = asset_match.group(1)
                    asset_ids.append(asset_id)
                elif param_name == 'char':
                    base_name = param.value.strip()
                elif param_name == 'desc':
                    asset_name = param.value.strip()
            if asset_name and base_name:
                redirect_from = f"{asset_name} ({base_name})"
                redirect_target = f"{base_name}#{asset_name}"
                try:
                    print(f"Ensuring page redirect: {redirect_from} -> {redirect_target}")
                    self.check_redirect(redirect_target, redirect_from)
                except Exception as redirect_error:  # pragma: no cover - best-effort logging
                    print(f"Failed to create {redirect_from} redirect: {redirect_error}")
            for asset_id in asset_ids:
                for section, params in paths.items():
                    versions = len(params[2])
                    version = 0
                    while version < versions:
                        if section == 'wsp':
                            url = (
                                'http://prd-game-a-granbluefantasy.akamaized.net/assets_en/'
                                'img/sp/cjs/{0}.{1}'
                            ).format(
                                asset_id,
                                params[0]
                            )
                            section = 'sp'
                        elif section == 'f_skin': # for tall element skins
                            url = (
                                'https://prd-game-a2-granbluefantasy.akamaized.net/assets_en/'
                                'img/sp/assets/{0}/f/skin/{1}{2}.{3}'
                            ).format(
                                asset_type,
                                asset_id,
                                params[2][version],
                                params[0]
                            )
                        elif section == 's_skin': # for square element skins
                            url = (
                                'https://prd-game-a-granbluefantasy.akamaized.net/assets_en/'
                                'img/sp/assets/{0}/s/skin/{1}{2}.{3}'
                            ).format(
                                asset_type,
                                asset_id,
                                params[2][version],
                                params[0]
                            )
                        else:
                            url = (
                                'http://prd-game-a-granbluefantasy.akamaized.net/assets_en/'
                                'img/sp/assets/{0}/{1}/{2}{3}.{4}'
                            ).format(
                                asset_type,
                                section,
                                asset_id,
                                params[2][version],
                                params[0]
                            )

                        total_urls += 1
                        success, sha1, size, io = self.get_image(url)
                        if success:
                            successful_downloads += 1
                        else:
                            download_failures += 1
                        emit_status(
                            'downloading',
                            successful=successful_downloads,
                            failed=download_failures,
                            total=total_urls
                        )
                        if success:
                            if section == 'f_skin':
                                true_name = "{0}_f_skin_{1}{2}.{3}".format(
                                    asset_type.lower(),
                                    asset_id,
                                    params[2][version],
                                    params[0]
                                )
                            elif section == 's_skin':
                                true_name = "{0}_s_skin_{1}{2}.{3}".format(
                                    asset_type.lower(),
                                    asset_id,
                                    params[2][version],
                                    params[0]
                                )
                            else:
                                true_name = "{0} {1} {2}{3}.{4}".format(
                                    asset_type.capitalize(),
                                    section,
                                    asset_id,
                                    params[2][version],
                                    params[0]
                                )
                            other_names = []

                            if section in ('f_skin', 's_skin'):
                                element_label = params[3][version] if version < len(params[3]) else ''
                                if element_label:
                                    other_names.append(
                                        '{0}_({1}){2}_{3}.{4}'.format(
                                            asset_name,
                                            base_name,
                                            params[1],
                                            element_label,
                                            params[0]
                                        )
                                    )
                            else:
                                if (versions < 2) or (params[3][version] == 'A'):
                                    other_names.append(
                                        '{0}_({1}){2}.{3}'.format(
                                            asset_name,
                                            base_name,
                                            params[1],
                                            params[0]
                                        )
                                    )
                                #  'skin':          ['png', '_skin',   ['_01'], ['A'], ['Outfit Images', 'Skin Outfit Images'  ]],
                                if (versions > 1):
                                    other_names.append(
                                        '{0}_({1}){2}{3}.{4}'.format(
                                            asset_name,
                                            base_name,
                                            params[1],
                                            (' ' if params[1] == '' else '') + params[3][version], # removed space from first quote
                                            params[0]
                                        )
                                    )

                            # true_name may be changed by
                            check_image_result = self.check_image(true_name, sha1, size, io, other_names)
                            if check_image_result == True:
                                uploaded += 1
                            elif check_image_result == False:
                                upload_failures += 1
                                print('Checking image {0} failed! Skipping...'.format(true_name))
                                version += 1
                                processed += 1
                                emit_status(
                                    'processing',
                                    processed=processed,
                                    total=successful_downloads,
                                    current_image=true_name
                                )
                                continue
                            else:
                                true_name = check_image_result
                                duplicates += 1
                            self.check_image_categories(true_name, params[4])
                            for other_name in other_names:
                                self.check_file_redirect(true_name, other_name)

                            time.sleep(self.delay)

                            self.check_file_double_redirect(true_name)

                            processed += 1
                            emit_status(
                                'processing',
                                processed=processed,
                                total=successful_downloads,
                                current_image=true_name
                            )

                        version += 1
        total_failed = download_failures + upload_failures
        emit_status(
            'completed',
            processed=processed,
            uploaded=uploaded,
            duplicates=duplicates,
            failed=total_failed,
            total_urls=total_urls,
            successful=successful_downloads
        )

    def check_characters(self, category, resume_from=''):
        resume = len(resume_from) > 0
        pages = self.wiki.categories[category]
        for page in pages:
            if resume:
                if page.name == resume_from:
                    resume = False
                else:
                    continue
            self.check_character(page)

    def check_summons(self, category, resume_from=''):
        resume = len(resume_from) > 0
        pages = self.wiki.categories[category]
        for page in pages:
            if resume:
                if page.name == resume_from:
                    resume = False
                else:
                    continue
            self.check_summon(page)

    def check_weapons(self, category, resume_from=''):
        resume = len(resume_from) > 0
        pages = self.wiki.categories[category]
        for page in pages:
            if resume:
                if page.name == resume_from:
                    resume = False
                else:
                    continue
            self.check_weapon(page)

    def class_images(self, name=''):
        if len(name) > 0:
            self.check_class(self.wiki.pages[name])
        else:
            skip_mode = False
            skip_until = 'Luchador'
            pages = self.wiki.categories['Class']
            for page in pages:
                if skip_mode and (page.name != skip_until):
                    continue
                skip_mode = False
                self.check_class(page)

    def check_class(self, page):
        """
        New class image pipeline using the revised naming schema.

        Adding a new asset type:
        1. Decide whether the asset is gendered.
           - Gendered assets (Gran/Djeeta variants) should use
             `process_gendered_assets`, which automatically assigns the base
             class image category and a gender-specific category.
           - Non-gendered assets can use `process_single_asset`.
        2. Build the variant list.
           - If Lv50 artwork exists, call
             `build_variants('label', include_lvl50=supports_lvl50_assets,
             lvl50_label='asset (Lv50) image')`, replacing the label text with
             something appropriate (for example `quest (Lv50) image`) so the
             helper creates both the base and Lv50 entries when template data is
             available.
           - If the asset has a single form, skip `build_variants` and call
             `process_single_asset` directly.
        3. Prepare helper callbacks.
           - `url_builder`: create the remote URL from `variant` fields (id,
             id_num, abbr) plus `gender` when needed.
           - `canonical_builder`: define the wiki filename, following the
             existing leader_* naming convention.
           - `other_names_builder`: supply redirect titles; helpers create and
             validate the redirects.
           - Optional `extra_categories_builder`: provide extra categories if
             you need more than the defaults.
           - Optional `label_builder`: customize the progress text, if useful.
        4. Hook the asset into the flow.
           - Gendered assets call `process_gendered_assets(...)`.
           - Single-form assets call
             `process_single_asset(label, url, canonical_name, other_names, ...)`.
        5. Validate.
           - Run `python -m compileall images.py` to check syntax.
           - Test against a representative class page to confirm URLs, uploads,
             and redirects look correct.

        Args:
            page (mwclient.page.Page): Class page to process.

        Returns:
            dict | None: Parsed template metadata for future processing steps.
        """
        print(f'Preparing new class workflow for "{page.name}"...')

        pagetext = page.text()
        wikicode = mwparserfromhell.parse(pagetext)
        templates = wikicode.filter_templates()

        class_data = None

        for template in templates:
            template_name = template.name.strip()
            if template_name.lower() != 'class':
                continue

            class_data = {
                'id': '',
                'id_num': '',
                'abbr': '',
                'id_lvl50': '',
                'id_lvl50_num': '',
                'id_lvl50_abbr': '',
                'name': '',
                'family': '',
                'row': None,
            }

            for param in template.params:
                param_name = param.name.strip().lower()
                raw_value = str(param.value).strip()
                clean_value = mwparserfromhell.parse(raw_value).strip_code().strip()

                if param_name == 'id' and clean_value:
                    class_data['id'] = clean_value
                    if '_' in clean_value:
                        id_num, abbr = clean_value.split('_', 1)
                        class_data['id_num'] = id_num
                        class_data['abbr'] = abbr
                    else:
                        class_data['id_num'] = clean_value
                elif param_name == 'class' and clean_value:
                    class_data['name'] = clean_value
                elif param_name == 'family' and clean_value:
                    class_data['family'] = clean_value
                elif param_name == 'id_lvl50' and clean_value:
                    class_data['id_lvl50'] = clean_value
                    if '_' in clean_value:
                        id_lvl50_num, id_lvl50_abbr = clean_value.split('_', 1)
                        class_data['id_lvl50_num'] = id_lvl50_num
                        class_data['id_lvl50_abbr'] = id_lvl50_abbr
                    else:
                        class_data['id_lvl50_num'] = clean_value
                elif param_name == 'row' and clean_value:
                    try:
                        class_data['row'] = int(clean_value)
                    except ValueError:
                        class_data['row'] = clean_value

            break

        if class_data is None:
            print('No {{Class}} template found; skipping.')
            return None

        required_keys = ['id', 'id_num', 'abbr', 'name', 'family', 'row']
        missing = [key for key in required_keys if not class_data.get(key) and class_data.get(key) != 0]
        if missing:
            print(f'Class metadata incomplete ({", ".join(missing)} missing); no uploads attempted yet.')

        uploads_total = 0
        uploads_success = 0
        uploads_duplicates = 0
        class_categories = ['Class Images']

        GENDER_LABELS = {0: 'gran', 1: 'djeeta'}
        genders = (0, 1)

        supports_lvl50_assets = (
            class_data.get('row') == 0
            and class_data.get('id_lvl50')
            and class_data.get('id_lvl50_num')
            and class_data.get('id_lvl50_abbr')
        )

        def has_class_fields(*fields):
            """
            Ensure required class template fields are present.

            Row is allowed to be integer 0, so only None counts as missing.
            """
            for field in fields:
                value = class_data.get(field)
                if field == 'row':
                    if value is None:
                        return False
                elif not value:
                    return False
            return True

        def get_row_suffix():
            roman_rows = ['0', 'I', 'II', 'III', 'IV', 'V', 'VI', 'VII', 'VIII', 'IX']
            row_value = class_data.get('row')
            if isinstance(row_value, int) and 0 <= row_value < len(roman_rows):
                return roman_rows[row_value]
            return str(row_value)

        def get_gender_categories(gender_alias):
            if gender_alias == 'gran':
                return ['Gran Class Images']
            if gender_alias == 'djeeta':
                return ['Djeeta Class Images']
            return []

        def gendered_asset_total(include_lvl50=False, require_supported_lvl50=False):
            """
            Return planned URL checks for a gendered asset family.
            """
            variant_count = 1
            if include_lvl50:
                if require_supported_lvl50:
                    variant_count = 2 if supports_lvl50_assets else 1
                else:
                    variant_count = 2 if class_data.get('id_lvl50') else 1
            return variant_count * len(genders)

        def build_variants(label, include_lvl50=False, lvl50_label=None):
            variants = [{
                'id': class_data['id'],
                'id_num': class_data['id_num'],
                'abbr': class_data['abbr'],
                'label': label,
                'is_lvl50': False,
            }]

            if include_lvl50 and class_data.get('id_lvl50'):
                lvl50_id = class_data['id_lvl50']
                split = lvl50_id.split('_', 1)
                lvl50_num = class_data.get('id_lvl50_num') or split[0]
                lvl50_abbr = class_data.get('id_lvl50_abbr') or (split[1] if len(split) > 1 else class_data['abbr'])
                variants.append({
                    'id': lvl50_id,
                    'id_num': lvl50_num,
                    'abbr': lvl50_abbr,
                    'label': lvl50_label or f'{label} (Lv50)',
                    'is_lvl50': True,
                })

            return variants

        def process_asset(label_text, url, canonical_name, other_names, extra_categories=None):
            nonlocal uploads_success, uploads_duplicates

            print(f'Downloading {label_text}: {url}')
            success, sha1, size, io_obj = self.get_image(url)
            if not success:
                print(f'Failed to download {label_text}.')
                return False

            check_result = self.check_image(canonical_name, sha1, size, io_obj, other_names)
            if check_result is False:
                print(f'Upload validation failed for {canonical_name}.')
                return False

            final_name = canonical_name if check_result is True else check_result
            if check_result is True:
                uploads_success += 1
            else:
                uploads_duplicates += 1

            categories = class_categories if not extra_categories else class_categories + list(extra_categories)
            self.check_image_categories(final_name, categories)
            for other_name in other_names:
                self.check_file_redirect(final_name, other_name)

            time.sleep(self.delay)
            self.check_file_double_redirect(final_name)

            emit_status(
                "processing",
                processed=uploads_success + uploads_duplicates,
                total=uploads_total,
                current_image=final_name,
            )
            return True

        def process_gendered_assets(
            variants,
            url_builder,
            canonical_builder,
            other_names_builder,
            label_builder=None,
            adjust_attempts_for_failures=False,
            extra_categories_builder=None,
        ):
            any_success = False

            for variant in variants:
                for gender in genders:
                    gender_alias = GENDER_LABELS.get(gender, str(gender))
                    label_text = (
                        label_builder(variant, gender, gender_alias)
                        if label_builder
                        else f'{variant["label"]} ({gender_alias})'
                    )
                    url = url_builder(variant, gender)
                    canonical_name = canonical_builder(variant, gender)
                    other_names = other_names_builder(variant, gender, gender_alias)
                    extra_categories = (
                        extra_categories_builder(variant, gender, gender_alias)
                        if extra_categories_builder
                        else None
                    )
                    if process_asset(label_text, url, canonical_name, other_names, extra_categories):
                        any_success = True

            return any_success

        def process_single_asset(label_text, url, canonical_name, other_names, extra_categories=None):
            return process_asset(label_text, url, canonical_name, other_names, extra_categories)

        def emit_status(stage, **kwargs):
            if hasattr(self, '_status_callback'):
                self._status_callback(stage, **kwargs)

        # Compute total checks up front so progress denominator stays fixed.
        if has_class_fields('id', 'id_num', 'abbr'):
            uploads_total += gendered_asset_total(include_lvl50=True, require_supported_lvl50=True)   # sd

        if has_class_fields('id_num', 'name', 'row', 'family'):
            uploads_total += 1  # shared SD

        uploads_total += gendered_asset_total()  # job_change
        uploads_total += gendered_asset_total()  # jobm
        uploads_total += gendered_asset_total(include_lvl50=True)  # party
        uploads_total += gendered_asset_total(include_lvl50=True)  # jobon_z
        uploads_total += gendered_asset_total(include_lvl50=True)  # jlon
        uploads_total += gendered_asset_total(include_lvl50=True)  # result_ml

        if has_class_fields('id', 'id_num', 'abbr', 'name'):
            uploads_total += gendered_asset_total(include_lvl50=True)  # result
            uploads_total += gendered_asset_total(include_lvl50=True)  # profile
            uploads_total += gendered_asset_total(include_lvl50=True)  # raid_log
            uploads_total += gendered_asset_total(include_lvl50=True, require_supported_lvl50=True)  # raid_normal
            uploads_total += gendered_asset_total(include_lvl50=True, require_supported_lvl50=True)  # talk
            uploads_total += gendered_asset_total(include_lvl50=True, require_supported_lvl50=True)  # quest
            uploads_total += gendered_asset_total(include_lvl50=True, require_supported_lvl50=True)  # coop
            uploads_total += gendered_asset_total(include_lvl50=True, require_supported_lvl50=True)  # btn
            uploads_total += gendered_asset_total(include_lvl50=True, require_supported_lvl50=True)  # HD
            uploads_total += gendered_asset_total(include_lvl50=True, require_supported_lvl50=True)  # homescreen
            uploads_total += gendered_asset_total(include_lvl50=True, require_supported_lvl50=True)  # zenith
            uploads_total += gendered_asset_total(include_lvl50=True, require_supported_lvl50=True)  # tower

        if has_class_fields('id_num', 'name'):
            uploads_total += 1  # icon
            uploads_total += gendered_asset_total(include_lvl50=True, require_supported_lvl50=True)  # square gendered
            uploads_total += 1  # square shared

        if has_class_fields('id_num', 'family', 'name'):
            uploads_total += 2  # job icon + jobtree

        if has_class_fields('id_num'):
            uploads_total += 1  # job name tree
            if has_class_fields('name'):
                uploads_total += 2  # job name + job list

        if has_class_fields('id', 'id_num', 'abbr'):
            sprite_variants = build_variants(
                'class sprite',
                include_lvl50=supports_lvl50_assets,
                lvl50_label='class sprite (Lv50)',
            )
            process_gendered_assets(
                sprite_variants,
                lambda variant, gender: (
                    'https://prd-game-a-granbluefantasy.akamaized.net/assets_en/'
                    f'img/sp/assets/leader/sd/{variant["id"]}_{gender}_01.png'
                ),
                lambda variant, gender: (
                    f'leader_sd_{variant["id_num"]}_{variant["abbr"]}_{gender}_01.png'
                ),
                lambda variant, gender, alias: [
                    f'leader_sd_{variant["id_num"]}_{gender}_01.png',
                    *([f'{page.name} ({alias.title()}) SD.png'] if not variant['is_lvl50'] else []),
                    *([f'{page.name} ({alias.title()}) SD2.png'] if variant['is_lvl50'] else []),
                ],
                extra_categories_builder=lambda variant, gender, alias: get_gender_categories(alias),
            )

        if has_class_fields('id_num', 'name', 'row', 'family'):
            row_suffix = get_row_suffix()
            process_single_asset(
                'shared SD image',
                url=f'https://prd-game-a-granbluefantasy.akamaized.net/assets_en/img/sp/assets/leader/sd/m/{class_data["id_num"]}_01.jpg',
                canonical_name=f'leader_sd_m_{class_data["id_num"]}_01.jpg',
                other_names=[
                    f'{class_data["name"]}_sdm.jpg',
                    f'{class_data["family"]}_{row_suffix}_sdm.jpg',
                ],
            )
        job_change_redirects = {
            0: f'{class_data["name"]}_gran.png',
            1: f'{class_data["name"]}_djeeta.png',
        }
        job_change_variants = build_variants('job change image')
        process_gendered_assets(
            job_change_variants,
            lambda variant, gender: (
                'https://prd-game-a-granbluefantasy.akamaized.net/assets_en/'
                f'img/sp/assets/leader/job_change/{variant["id"]}_{gender}_01.png'
            ),
            lambda variant, gender: (
                f'leader_job_change_{variant["id_num"]}_{variant["abbr"]}_{gender}_01.png'
            ),
            lambda variant, gender, alias: [
                job_change_redirects.get(gender, f'{class_data["name"]}_{alias}.png'),
            ],
            extra_categories_builder=lambda variant, gender, alias: get_gender_categories(alias),
        )

        jobm_variants = build_variants('jobm image')
        process_gendered_assets(
            jobm_variants,
            lambda variant, gender: (
                'https://prd-game-a-granbluefantasy.akamaized.net/assets_en/'
                f'img/sp/assets/leader/jobm/{variant["id"]}_{gender}_01.jpg'
            ),
            lambda variant, gender: (
                f'leader_jobm_{variant["id_num"]}_{variant["abbr"]}_{gender}_01.jpg'
            ),
            lambda variant, gender, alias: [
                f'{class_data["name"]}_{alias}_jobm.png',
                f'leader_jobm_{variant["id_num"]}_{gender}_01.jpg',
            ],
            extra_categories_builder=lambda variant, gender, alias: get_gender_categories(alias),
        )

        party_variants = build_variants('party image', include_lvl50=True)
        process_gendered_assets(
            party_variants,
            lambda variant, gender: (
                'https://prd-game-a-granbluefantasy.akamaized.net/assets_en/'
                f'img/sp/assets/leader/p/{variant["id"]}_{gender}_01.png'
            ),
            lambda variant, gender: (
                f'leader_p_{variant["id_num"]}_{variant["abbr"]}_{gender}_01.png'
            ),
            lambda variant, gender, alias: [
                f'leader_p_{variant["id_num"]}_{gender}_01.png',
                    *([f'{class_data["name"]}_{alias}_party.png'] if not variant['is_lvl50'] else []),
                    *([f'{class_data["name"]}_{alias}_party2.png'] if variant['is_lvl50'] else []),
            ],
            extra_categories_builder=lambda variant, gender, alias: get_gender_categories(alias),
        )

        jobonz_variants = build_variants('jobon_z image', include_lvl50=True, lvl50_label='jobon_z (Lv50) image')
        process_gendered_assets(
            jobonz_variants,
            lambda variant, gender: (
                'https://prd-game-a-granbluefantasy.akamaized.net/assets_en/'
                f'img/sp/assets/leader/jobon_z/{variant["id"]}_{gender}_01.png'
            ),
            lambda variant, gender: (
                f'leader_jobon_z_{variant["id_num"]}_{variant["abbr"]}_{gender}_01.png'
            ),
            lambda variant, gender, alias: [
                f'leader_jobon_z_{variant["id_num"]}_{gender}_01.png',
                *([f'{class_data["name"]}_{alias}_jobon_z.png'] if not variant['is_lvl50'] else []),
                *([f'{class_data["name"]}_{alias}_jobon_z2.png'] if variant['is_lvl50'] else []),
            ],
            extra_categories_builder=lambda variant, gender, alias: get_gender_categories(alias),
        )

        jlon_variants = build_variants('jlon image', include_lvl50=True, lvl50_label='jlon (Lv50) image')
        process_gendered_assets(
            jlon_variants,
            lambda variant, gender: (
                'https://prd-game-a-granbluefantasy.akamaized.net/assets_en/'
                f'img/sp/assets/leader/jlon/{variant["id"]}_{gender}_01.png'
            ),
            lambda variant, gender: (
                f'leader_jlon_{variant["id_num"]}_{variant["abbr"]}_{gender}_01.png'
            ),
            lambda variant, gender, alias: [
                f'{class_data["name"]}_{alias}_jlon.png',
                f'leader_jlon_{variant["id_num"]}_{gender}_01.png',
            ],
            extra_categories_builder=lambda variant, gender, alias: get_gender_categories(alias),
        )

        result_ml_variants = build_variants('result_ml image', include_lvl50=True, lvl50_label='result_ml (Lv50) image')
        process_gendered_assets(
            result_ml_variants,
            lambda variant, gender: (
                'https://prd-game-a-granbluefantasy.akamaized.net/assets_en/'
                f'img/sp/assets/leader/result_ml/{variant["id"]}_{gender}_01.jpg'
            ),
            lambda variant, gender: (
                f'leader_result_ml_{variant["id_num"]}_{variant["abbr"]}_{gender}_01.jpg'
            ),
            lambda variant, gender, alias: [
                f'leader_result_ml_{variant["id_num"]}_{gender}_01.jpg',
                *([f'{class_data["name"]}_{alias}_result_ml.jpg'] if not variant['is_lvl50'] else []),
                *([f'{class_data["name"]}_{alias}_result_ml_lvl50.jpg'] if variant['is_lvl50'] else []),
            ],
            extra_categories_builder=lambda variant, gender, alias: get_gender_categories(alias),
        )
        if has_class_fields('id', 'id_num', 'abbr', 'name'):
            result_variants = build_variants('result image', include_lvl50=True, lvl50_label='result (Lv50) image')

        if has_class_fields('id', 'id_num', 'abbr', 'name'):
            result_variants = build_variants('result image', include_lvl50=True, lvl50_label='result (Lv50) image')
            process_gendered_assets(
                result_variants,
                lambda variant, gender: (
                    'https://prd-game-a5-granbluefantasy.akamaized.net/assets_en/'
                    f'img/sp/assets/leader/result/{variant["id"]}_{gender}_01.jpg'
                ),
                lambda variant, gender: (
                    f'leader_result_{variant["id_num"]}_{variant["abbr"]}_{gender}_01.jpg'
                ),
                lambda variant, gender, alias: [
                    f'leader_result_{variant["id_num"]}_{gender}_01.jpg',
                    *([f'{class_data["name"]}_{alias}_result.jpg'] if not variant['is_lvl50'] else []),
                    *([f'{class_data["name"]}_{alias}_result_lvl50.jpg'] if variant['is_lvl50'] else []),
                ],
                extra_categories_builder=lambda variant, gender, alias: get_gender_categories(alias),
            )

            profile_variants = build_variants('profile image', include_lvl50=True, lvl50_label='profile (Lv50) image')
            process_gendered_assets(
                profile_variants,
                lambda variant, gender: (
                    'https://prd-game-a5-granbluefantasy.akamaized.net/assets_en/'
                    f'img/sp/assets/leader/pm/{variant["id"]}_{gender}_01.png'
                ),
                lambda variant, gender: (
                    f'leader_pm_{variant["id_num"]}_{variant["abbr"]}_{gender}_01.png'
                ),
                lambda variant, gender, alias: [
                    f'leader_pm_{variant["id_num"]}_{gender}_01.png',
                    *([f'{class_data["name"]}_{alias}_profile.png'] if not variant['is_lvl50'] else []),
                    *([f'{class_data["name"]}_{alias}_profile2.png'] if variant['is_lvl50'] else []),
                ],
                extra_categories_builder=lambda variant, gender, alias: get_gender_categories(alias),
            )

            raid_log_variants = build_variants('raid log image', include_lvl50=True, lvl50_label='raid log (Lv50) image')
            process_gendered_assets(
                raid_log_variants,
                lambda variant, gender: (
                    'https://prd-game-a1-granbluefantasy.akamaized.net/assets_en/'
                    f'img/sp/assets/leader/raid_log/{variant["id"]}_{gender}_01.png'
                ),
                lambda variant, gender: (
                    f'leader_raid_log_{variant["id_num"]}_{variant["abbr"]}_{gender}_01.png'
                ),
                lambda variant, gender, alias: [
                    f'leader_raid_log_{variant["id_num"]}_{gender}_01.png',
                    *([f'{class_data["name"]}_{alias}_raid_log.png'] if not variant['is_lvl50'] else []),
                    *([f'{class_data["name"]}_{alias}_raid_log2.png'] if variant['is_lvl50'] else []),
                ],
                extra_categories_builder=lambda variant, gender, alias: get_gender_categories(alias),
            )

            raid_normal_variants = build_variants(
                'raid_normal image',
                include_lvl50=supports_lvl50_assets,
                lvl50_label='raid_normal (Lv50) image',
            )
            process_gendered_assets(
                raid_normal_variants,
                lambda variant, gender: (
                    'https://prd-game-a1-granbluefantasy.akamaized.net/assets_en/'
                    f'img/sp/assets/leader/raid_normal/{variant["id"]}_{gender}_01.jpg'
                ),
                lambda variant, gender: (
                    f'leader_raid_normal_{variant["id_num"]}_{variant["abbr"]}_{gender}_01.jpg'
                ),
                lambda variant, gender, alias: [
                    f'leader_raid_normal_{variant["id_num"]}_{gender}_01.jpg',
                    *([f'{page.name}_{alias}_profile.jpg'] if not variant['is_lvl50'] else []),
                    *([f'{page.name}_{alias}_profile2.jpg'] if variant['is_lvl50'] else []),
                    *([f'{class_data["name"]}_{alias}_raid.jpg'] if not variant['is_lvl50'] else []),
                ],
                extra_categories_builder=lambda variant, gender, alias: get_gender_categories(alias),
            )

            talk_variants = build_variants(
                'talk image',
                include_lvl50=supports_lvl50_assets,
                lvl50_label='talk (Lv50) image',
            )
            process_gendered_assets(
                talk_variants,
                lambda variant, gender: (
                    'https://prd-game-a-granbluefantasy.akamaized.net/assets_en/'
                    f'img/sp/assets/leader/talk/{variant["id"]}_{gender}_01.png'
                ),
                lambda variant, gender: f'leader_talk_{variant["id_num"]}_{variant["abbr"]}_{gender}_01.png',
                lambda variant, gender, alias: [
                    f'leader_talk_{variant["id_num"]}_{gender}_01.png',
                    *([f'{page.name}_{alias}_talk.png'] if not variant['is_lvl50'] else []),
                    *([f'{page.name}_{alias}_talk2.png'] if variant['is_lvl50'] else []),
                ],
                extra_categories_builder=lambda variant, gender, alias: get_gender_categories(alias),
            )

            quest_variants = build_variants(
                'quest image',
                include_lvl50=supports_lvl50_assets,
                lvl50_label='quest (Lv50) image',
            )
            process_gendered_assets(
                quest_variants,
                lambda variant, gender: (
                    'https://prd-game-a-granbluefantasy.akamaized.net/assets_en/'
                    f'img/sp/assets/leader/quest/{variant["id"]}_{gender}_01.jpg'
                ),
                lambda variant, gender: f'leader_quest_{variant["id_num"]}_{variant["abbr"]}_{gender}_01.jpg',
                lambda variant, gender, alias: [
                    f'leader_quest_{variant["id_num"]}_{gender}_01.jpg',
                    *([f'{class_data["name"]}_{alias}_quest.png'] if not variant['is_lvl50'] else []),
                    *([f'{class_data["name"]}_{alias}_quest2.png'] if variant['is_lvl50'] else []),
                ],
                extra_categories_builder=lambda variant, gender, alias: get_gender_categories(alias),
            )

            coop_variants = build_variants(
                'coop image',
                include_lvl50=supports_lvl50_assets,
                lvl50_label='coop (Lv50) image',
            )
            process_gendered_assets(
                coop_variants,
                lambda variant, gender: (
                    'https://prd-game-a-granbluefantasy.akamaized.net/assets_en/'
                    f'img/sp/assets/leader/coop/{variant["id"]}_{gender}_01.png'
                ),
                lambda variant, gender: f'leader_coop_{variant["id_num"]}_{variant["abbr"]}_{gender}_01.png',
                lambda variant, gender, alias: [
                    f'leader_coop_{variant["id_num"]}_{gender}_01.png',
                    *([f'{class_data["name"]}_{alias}_coop.png'] if not variant['is_lvl50'] else []),
                    *([f'{class_data["name"]}_{alias}_coop2.png'] if variant['is_lvl50'] else []),
                ],
                extra_categories_builder=lambda variant, gender, alias: get_gender_categories(alias),
            )

            btn_variants = build_variants(
                'btn image',
                include_lvl50=supports_lvl50_assets,
                lvl50_label='btn (Lv50) image',
            )
            process_gendered_assets(
                btn_variants,
                lambda variant, gender: (
                    'https://prd-game-a-granbluefantasy.akamaized.net/assets_en/'
                    f'img/sp/assets/leader/btn/{variant["id"]}_{gender}_01.png'
                ),
                lambda variant, gender: f'leader_btn_{variant["id_num"]}_{variant["abbr"]}_{gender}_01.png',
                lambda variant, gender, alias: [
                    f'leader_btn_{variant["id_num"]}_{gender}_01.png',
                    *([f'{class_data["name"]}_{alias}_btn.png'] if not variant['is_lvl50'] else []),
                    *([f'{class_data["name"]}_{alias}_btn2.png'] if variant['is_lvl50'] else []),
                ],
                extra_categories_builder=lambda variant, gender, alias: get_gender_categories(alias),
            )

            hd_variants = build_variants(
                'HD image',
                include_lvl50=supports_lvl50_assets,
                lvl50_label='HD (Lv50) image',
            )
            process_gendered_assets(
                hd_variants,
                lambda variant, gender: f'https://media.skycompass.io/assets/customizes/jobs/1138x1138/{variant["id_num"]}_{gender}.png',
                lambda variant, gender: f'jobs_1138x1138_{variant["id_num"]}_{gender}.png',
                lambda variant, gender, alias: [
                    *([f'{class_data["name"]} {alias} HD.png'] if not variant['is_lvl50'] else []),
                    *([f'{class_data["name"]} {alias} HD2.png'] if variant['is_lvl50'] else []),
                ],
                extra_categories_builder=lambda variant, gender, alias: get_gender_categories(alias),
            )

            my_variants = build_variants(
                'homescreen image',
                include_lvl50=supports_lvl50_assets,
                lvl50_label='homescreen (Lv50) image',
            )
            process_gendered_assets(
                my_variants,
                lambda variant, gender: (
                    'https://prd-game-a-granbluefantasy.akamaized.net/assets_en/'
                    f'img/sp/assets/leader/my/{variant["id"]}_{gender}_01.png'
                ),
                lambda variant, gender: f'leader_my_{variant["id_num"]}_{variant["abbr"]}_{gender}_01.png',
                lambda variant, gender, alias: [
                    f'leader_my_{variant["id_num"]}_{gender}_01.png',
                    *([
                        f'{class_data["name"]}_{alias}_homescreen.png',
                        f'{class_data["name"]}_{alias}_my.png',
                    ] if not variant['is_lvl50'] else []),
                    *([
                        f'{class_data["name"]}_{alias}_homescreen2.png',
                        f'{class_data["name"]}_{alias}_my2.png',
                    ] if variant['is_lvl50'] else []),
                ],
                extra_categories_builder=lambda variant, gender, alias: get_gender_categories(alias),
            )

            zenith_variants = build_variants(
                'zenith image',
                include_lvl50=supports_lvl50_assets,
                lvl50_label='zenith (Lv50) image',
            )
            process_gendered_assets(
                zenith_variants,
                lambda variant, gender: (
                    'https://prd-game-a-granbluefantasy.akamaized.net/assets_en/'
                    f'img/sp/assets/leader/zenith/{variant["id"]}_{gender}_01.png'
                ),
                lambda variant, gender: f'leader_zenith_{variant["id_num"]}_{variant["abbr"]}_{gender}_01.png',
                lambda variant, gender, alias: [
                    f'leader_zenith_{variant["id_num"]}_{gender}_01.png',
                    *([f'{class_data["name"]}_{alias}_zenith.png'] if not variant['is_lvl50'] else []),
                    *([f'{class_data["name"]}_{alias}_zenith2.png'] if variant['is_lvl50'] else []),
                ],
                extra_categories_builder=lambda variant, gender, alias: get_gender_categories(alias),
            )

            tower_variants = build_variants(
                'Tower of Babyl image',
                include_lvl50=supports_lvl50_assets,
                lvl50_label='Tower of Babyl (Lv50) image',
            )
            process_gendered_assets(
                tower_variants,
                lambda variant, gender: (
                    'https://prd-game-a-granbluefantasy.akamaized.net/assets_en/'
                    f'img/sp/assets/leader/t/{variant["id"]}_{gender}_01.png'
                ),
                lambda variant, gender: f'leader_t_{variant["id_num"]}_{variant["abbr"]}_{gender}_01.png',
                lambda variant, gender, alias: [
                    f'leader_t_{variant["id_num"]}_{gender}_01.png',
                    *([f'{class_data["name"]}_{alias}_babyl2.png'] if variant['is_lvl50'] else []),
                ],
                extra_categories_builder=lambda variant, gender, alias: get_gender_categories(alias),
            )

        if has_class_fields('id_num', 'name'):
            process_single_asset(
                'leader icon',
                url=(
                    'https://prd-game-a-granbluefantasy.akamaized.net/assets_en/'
                    f'img/sp/assets/leader/m/{class_data["id_num"]}_01.jpg'
                ),
                canonical_name=f'leader_m_{class_data["id_num"]}_01.jpg',
                other_names=[f'{class_data["name"]} icon.jpg'],
            )

        square_images_handled = False
        if has_class_fields('id', 'id_num', 'abbr', 'name'):
            square_variants = build_variants(
                'square image',
                include_lvl50=supports_lvl50_assets,
                lvl50_label='square (Lv50) image',
            )
            process_gendered_assets(
                square_variants,
                lambda variant, gender: (
                    'https://prd-game-a-granbluefantasy.akamaized.net/assets_en/'
                    f'img/sp/assets/leader/s/{variant["id"]}_{gender}_01.jpg'
                ),
                lambda variant, gender: f'leader_s_{variant["id_num"]}_{variant["abbr"]}_{gender}_01.jpg',
                lambda variant, gender, alias: [
                    f'leader_s_{variant["id_num"]}_{gender}_01.jpg',
                    *([f'{class_data["name"]}_{alias}_square.jpg'] if not variant['is_lvl50'] else []),
                    *([f'{class_data["name"]}_{alias}_square_lvl50.jpg'] if variant['is_lvl50'] else []),
                ],
                adjust_attempts_for_failures=True,
                extra_categories_builder=lambda variant, gender, alias: get_gender_categories(alias),
            )

        if has_class_fields('id_num', 'name'):
            process_single_asset(
                'leader square (both)',
                url=(
                    'https://prd-game-a-granbluefantasy.akamaized.net/assets_en/'
                    f'img/sp/assets/leader/s/{class_data["id_num"]}_01.jpg'
                ),
                canonical_name=f'leader_s_{class_data["id_num"]}_01.jpg',
                other_names=[f'{class_data["name"]} square.jpg'],
            )

        if has_class_fields('id_num', 'family', 'name'):
            row_suffix = get_row_suffix()
            name_cased = class_data["name"].strip()
            process_single_asset(
                'job icon',
                url=(
                    'https://prd-game-a-granbluefantasy.akamaized.net/assets_en/'
                    f'img/sp/ui/icon/job/{class_data["id_num"]}.png'
                ),
                canonical_name=f'icon_job_{class_data["id_num"]}.png',
                other_names=[
                    f'icon_{class_data["family"]}_{row_suffix}.png',
                    f'icon_{name_cased}.png',
                ],
            )
            process_single_asset(
                'jobtree image',
                url=(
                    'https://prd-game-a-granbluefantasy.akamaized.net/assets_en/'
                    f'img/sp/assets/leader/jobtree/{class_data["id_num"]}.png'
                ),
                canonical_name=f'leader_jobtree_{class_data["id_num"]}.png',
                other_names=[
                    f'{class_data["family"]}_{row_suffix}_jobtree.png',
                    f'{class_data["name"]}_jobtree.png',
                ],
            )

        if has_class_fields('id_num'):
            process_single_asset(
                'job name tree image',
                url=(
                    'https://prd-game-a5-granbluefantasy.akamaized.net/assets_en/'
                    f'img/sp/ui/job_name_tree_l/{class_data["id_num"]}.png'
                ),
                canonical_name=f'job_name_tree_l_{class_data["id_num"]}.png',
                other_names=[],
            )
            if has_class_fields('name'):
                process_single_asset(
                    'job name image',
                    url=(
                        'https://prd-game-a-granbluefantasy.akamaized.net/assets_en/'
                        f'img/sp/ui/job_name/job_change/{class_data["id_num"]}.png'
                    ),
                    canonical_name=f'job_name_{class_data["id_num"]}.png',
                    other_names=[
                        f'{class_data["name"]}_name.png',
                        f'job_name_job_change_{class_data["id_num"]}.png',
                    ],
                )
                process_single_asset(
                    'job list image',
                    url=(
                        'https://prd-game-a1-granbluefantasy.akamaized.net/assets_en/'
                        f'img/sp/ui/job_name/job_list/{class_data["id_num"]}.png'
                    ),
                    canonical_name=f'job_list_{class_data["id_num"]}.png',
                    other_names=[
                        f'{class_data["name"]}_job_list.png',
                        f'Job_name_job_list_{class_data["id_num"]}.png',
                    ],
                )

        emit_status(
            "completed",
            processed=uploads_success + uploads_duplicates,
            uploaded=uploads_success,
            duplicates=uploads_duplicates,
            total_urls=uploads_total,
        )

        return class_data

def main():
    wi = WikiImages()
    #wi.weapon_images('Unsigned Kaneshige (Fire)')

    if len(sys.argv) < 2:
        wi.delay = 25
        #wi.weapon_images()
        #print('Please supply character, class, summon or weapon.')
        #wi.check_character(wi.wiki.pages["Vira"])
        #wi.check_character(wi.wiki.pages["Vira (SSR)"])
        #wi.check_character(wi.wiki.pages["Vira (Summer)"])
        #wi.check_character(wi.wiki.pages["Vira (Grand)"])
        #wi.check_character(wi.wiki.pages["Lady Katapillar and Vira"])
        #wi.check_summon(wi.wiki.pages["Celeste Omega"])
        #wi.check_weapon(wi.wiki.pages["Atma Fist (Fire)"])
        #wi.check_weapon(wi.wiki.pages["Ultima Claw (Fire)"])

        #wi.delay = 25
        #wi.check_weapons('R Weapons', '')
        #wi.check_weapon(wi.wiki.pages["Cat's Purr‎"])

        #wi.check_weapon(wi.wiki.pages["Vortex of the Void"])
        #wi.check_weapon(wi.wiki.pages["Froststar Staff"])

        # wi.check_class(wi.wiki.pages['Alchemist'])
        # wi.check_class(wi.wiki.pages['Apsaras'])
        # wi.check_class(wi.wiki.pages['Arcana Dueler'])
        # wi.check_class(wi.wiki.pages['Archer'])
        # wi.check_class(wi.wiki.pages['Assassin'])
        # wi.check_class(wi.wiki.pages['Bandit Tycoon'])
        # wi.check_class(wi.wiki.pages['Bard'])
        # wi.check_class(wi.wiki.pages['Berserker'])
        # wi.check_class(wi.wiki.pages['Bishop'])
        # wi.check_class(wi.wiki.pages['Chaos Ruler'])
        # wi.check_class(wi.wiki.pages['Chrysaor'])
        # wi.check_class(wi.wiki.pages['Cleric'])
        # wi.check_class(wi.wiki.pages['Dancer'])
        # wi.check_class(wi.wiki.pages['Dark Fencer'])
        # wi.check_class(wi.wiki.pages['Doctor'])
        # wi.check_class(wi.wiki.pages['Dragoon'])
        # wi.check_class(wi.wiki.pages['Drum Master'])
        # wi.check_class(wi.wiki.pages['Elysian'])
        # wi.check_class(wi.wiki.pages['Enhancer'])
        # wi.check_class(wi.wiki.pages['Fighter'])
        # wi.check_class(wi.wiki.pages['Gladiator'])
        # wi.check_class(wi.wiki.pages['Glorybringer'])
        # wi.check_class(wi.wiki.pages['Grappler'])
        # wi.check_class(wi.wiki.pages['Gunslinger'])
        # wi.check_class(wi.wiki.pages['Harpist'])
        # wi.check_class(wi.wiki.pages['Hawkeye'])
        # wi.check_class(wi.wiki.pages['Hermit'])
        # wi.check_class(wi.wiki.pages['Holy Saber'])
        # wi.check_class(wi.wiki.pages['Kengo'])
        # wi.check_class(wi.wiki.pages['Knight'])
        # wi.check_class(wi.wiki.pages['Kung Fu Artist'])
        # wi.check_class(wi.wiki.pages['Lancer'])
        # wi.check_class(wi.wiki.pages['Luchador'])
        # wi.check_class(wi.wiki.pages['Mechanic'])
        # wi.check_class(wi.wiki.pages['Mystic'])
        # wi.check_class(wi.wiki.pages['Nekomancer'])
        # wi.check_class(wi.wiki.pages['Nighthound'])
        # wi.check_class(wi.wiki.pages['Ninja'])
        # wi.check_class(wi.wiki.pages['Ogre'])
        # wi.check_class(wi.wiki.pages['Priest'])
        # wi.check_class(wi.wiki.pages['Raider'])
        # wi.check_class(wi.wiki.pages['Ranger'])
        # wi.check_class(wi.wiki.pages['Runeslayer'])
        # wi.check_class(wi.wiki.pages['Sage'])
        # wi.check_class(wi.wiki.pages['Samurai'])
        # wi.check_class(wi.wiki.pages['Sentinel'])
        # wi.check_class(wi.wiki.pages['Sidewinder'])
        # wi.check_class(wi.wiki.pages['Sorcerer'])
        # wi.check_class(wi.wiki.pages['Soldier'])
        # wi.check_class(wi.wiki.pages['Spartan'])
        # wi.check_class(wi.wiki.pages['Superstar'])
        # wi.check_class(wi.wiki.pages['Sword Master'])
        # wi.check_class(wi.wiki.pages['Thief'])
        # wi.check_class(wi.wiki.pages['Valkyrie'])
        # wi.check_class(wi.wiki.pages['Warlock'])
        # wi.check_class(wi.wiki.pages['Warrior'])
        # wi.check_class(wi.wiki.pages['Weapon Master'])
        # wi.check_class(wi.wiki.pages['Wizard'])

        return

    mode = sys.argv[1]
    wi.delay = 1

    if (mode == 'character') or (mode == 'char'):
        wi.check_character(wi.wiki.pages[sys.argv[2]])
    elif mode == 'character_fs_skin':
        wi.check_character_fs_skin(wi.wiki.pages[sys.argv[2]])
    elif mode == 'npc':
        wi.check_npc(wi.wiki.pages[sys.argv[2]])
    elif mode == 'skin':
        wi.check_skin(wi.wiki.pages[sys.argv[2]])
    elif (mode == 'characters') or (mode == 'chars'):
        category = sys.argv[2]
        resume_from = sys.argv[3] if len(sys.argv) > 3 else ''
        wi.delay = 50
        wi.check_characters(category, resume_from)
    elif mode == 'class':
        wi.check_class(wi.wiki.pages[sys.argv[2]])
        pass
    elif mode == 'classes':
        #wi.class_images()
        pass
    elif mode == 'skill_icons':
        if len(sys.argv) < 3:
            print('Please supply a page name containing {{Character}} template.')
            return
        page_name = sys.argv[2]
        wi.check_skill_icons(wi.wiki.pages[page_name])
    elif mode == 'banner':
        if len(sys.argv) < 3:
            print('Please supply a gacha banner identifier.')
            return
        banner_identifier = sys.argv[2]
        max_index = sys.argv[3] if len(sys.argv) > 3 else None
        wi.upload_gacha_banners(banner_identifier, max_index)
    elif mode == 'status':
        if len(sys.argv) < 3:
            print('Please supply a status identifier.')
            return
        status_identifier = sys.argv[2]
        max_index = sys.argv[3] if len(sys.argv) > 3 else None
        wi.upload_status_icons(status_identifier, max_index)
    elif mode == 'item':
        if len(sys.argv) < 3:
            print('Please supply a page name containing {{Item}} templates.')
            return
        page_name = sys.argv[2]
        wi.upload_item_article_images(wi.wiki.pages[page_name])
    elif mode == 'singleitem':
        if len(sys.argv) < 5:
            print('Usage: python images.py singleitem <item_type> <item_id> <item_name>')
            print(
                'Supported item types: '
                + ', '.join(sorted(wi.ITEM_SINGLE_TYPE_PATHS.keys()))
            )
            return
        item_type = sys.argv[2].lower()
        if item_type not in wi.ITEM_SINGLE_TYPE_PATHS:
            print(
                f'Unsupported item type "{item_type}". '
                f'Supported types: {", ".join(sorted(wi.ITEM_SINGLE_TYPE_PATHS.keys()))}'
            )
            return
        item_id = sys.argv[3]
        item_name = ' '.join(sys.argv[4:]).strip()
        if not item_id or not item_name:
            print('Item id and item name are required for single item upload.')
            return
        wi.upload_single_item_images(item_type, item_id, item_name)
    elif mode == 'summon':
        wi.check_summon(wi.wiki.pages[sys.argv[2]])
    elif mode == 'summons':
        category = sys.argv[2]
        resume_from = sys.argv[3] if len(sys.argv) > 3 else ''
        wi.delay = 25
        wi.check_summons(category, resume_from)
    elif mode == 'weapon':
        wi.check_weapon(wi.wiki.pages[sys.argv[2]])
    elif mode == 'weapons':
        category = sys.argv[2]
        resume_from = sys.argv[3] if len(sys.argv) > 3 else ''
        wi.delay = 25
        wi.check_weapons(category, resume_from)
        pass
    elif mode == 'artifact':
        wi.check_artifact(wi.wiki.pages[sys.argv[2]])
    elif mode == 'rucksack':
        wi.check_rucksack(wi.wiki.pages[sys.argv[2]])

main()
