import os
import sys
import requests
import mwclient
from mwclient.errors import APIError
import mwparserfromhell
import urllib.request
import re
import time
import hashlib
import asyncio
import aiohttp
from io import BytesIO
from gbfwiki import GBFWiki, GBFDB

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

class WikiImages(object):
    # Map of item upload types to CDN path segments
    ITEM_SINGLE_TYPE_PATHS = {
        "article": "article",
        "normal": "normal",
        "recycling": "recycling",
        "skillplus": "skillplus",
        "evolution": "evolution",
        "npcaugment": "npcaugment",
    }

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
        self.delay = 25

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

    def get_image(self, url, max_retries=3):
        print('Downloading {0}...'.format(url))
        headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/141.0.0.0 Safari/537.36'
        }
        proxy_url = os.environ.get("PROXY_URL")
        proxies = {}
        if proxy_url:
            proxies = {"http": proxy_url, "https": proxy_url}
            
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
                    return False, "", 0, False
                    
                elif req.status_code in [407, 429, 500, 502, 503, 504]:
                    if attempt < max_retries:
                        wait_time = (2 ** attempt)  # Exponential backoff: 1s, 2s, 4s
                        print(f'Download failed ({req.status_code}), retrying in {wait_time}s... (attempt {attempt + 1}/{max_retries})')
                        time.sleep(wait_time)
                        continue
                    else:
                        print(f'Download failed ({req.status_code}) after {max_retries} retries: {url}')
                        return False, "", 0, False
                else:
                    print(f'Download failed ({req.status_code}): {url}')
                    return False, "", 0, False
                    
            except requests.exceptions.RequestException as e:
                if attempt < max_retries:
                    wait_time = (2 ** attempt)
                    print(f'Download error, retrying in {wait_time}s... (attempt {attempt + 1}/{max_retries}): [network error]')
                    time.sleep(wait_time)
                    continue
                else:
                    print(f'Download failed after {max_retries} retries: [network error]')
                    return False, "", 0, False
                    
        return False, "", 0, False

    async def get_images_concurrent(self, urls):
        """Download multiple images concurrently from GBF CDN using proxy"""
        headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/141.0.0.0 Safari/537.36'
        }
        
        proxy_url = os.environ.get("PROXY_URL")
        
        async def download_single(session, url, max_retries=3):
            for attempt in range(max_retries + 1):
                try:
                    print(f'Downloading {url}...' + (f' (retry {attempt})' if attempt > 0 else ''))
                    kwargs = {'headers': headers}
                    if proxy_url:
                        kwargs['proxy'] = proxy_url
                    
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
                                return url, False, "", 0, False
                        else:
                            # Other HTTP errors - don't retry
                            print(f'Download failed ({response.status}): {url}')
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
                        return url, False, "", 0, False
                        
            return url, False, "", 0, False
        
        timeout = aiohttp.ClientTimeout(total=30)
        connector = aiohttp.TCPConnector()
        async with aiohttp.ClientSession(connector=connector, timeout=timeout) as session:
            tasks = [download_single(session, url) for url in urls]
            return await asyncio.gather(*tasks)

    def check_image(self, name, sha1, size, io, other_names):
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
            if not ('/archive/' in wiki_duplicate.imageinfo['url']):
                duplicates.append(wiki_duplicate)

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
                    url = (
                        'http://prd-game-a-granbluefantasy.akamaized.net/assets_en/'
                        'img/sp/assets/{0}/{1}/{2}.{3}'
                    ).format(
                        'summon' if dupe_match.group(1) == 'Summon' else 'weapon',
                        dupe_match.group(2),
                        dupe_match.group(3),
                        dupe_match.group(4)
                    )

                    success, dupe_sha1, dupe_size, dupe_io = self.get_image(url)
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
                response = self.wiki.upload(io, filename=true_name, ignore=True)
                print(response['result'] + ': ' + name)
            except Exception as e:
                print(f'Upload failed for {file_name}: {e}')
                return False
            if response['result'] == 'Warning':
                return False
            return True

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
        # doesn't work!
        image = self.wiki.images[name]
        if image.exists and not image.redirect:
            pagetext = image.text()
            new_text = pagetext
            for category in categories:
                category_text = '[[Category:{0}]]'.format(category)
                if not (category_text in new_text):
                    new_text = new_text + category_text
            if pagetext != new_text:
                print('Updating categories for {0}...'.format(name))
                image.save(new_text, summary='Batch image categories')

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

            indices = range(1, max_index + 1)
        else:
            if max_index is not None:
                print('Ignoring extra max index argument for single status upload.')
            indices = [None]

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
            check_image_result = self.check_image(true_name, sha1, size, io, other_names)

            if check_image_result is False:
                print(f'Checking image {true_name} failed! Skipping...')
                return False, None
            elif check_image_result is not True:
                true_name = check_image_result

            for other_name in other_names:
                self.check_file_redirect(true_name, other_name)

            time.sleep(self.delay)
            self.check_file_double_redirect(true_name)
            return True, true_name

        total = len(indices)
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

        for index in indices:
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

        emit_status(
            "processing",
            processed=processed,
            uploaded=uploaded,
            failed=failed,
            total=total,
            current_identifier=None,
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

            processed += 1

            emit_kwargs = {
                "processed": processed,
                "uploaded": uploaded,
                "failed": failed,
                "total": total,
                "current_identifier": current_identifier,
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
        )

    def _process_item_variant(self, item_type, item_id, item_name, variant, redirect_suffix):
        """
        Download and upload a single item variant image for the given CDN item type.

        Returns:
            tuple[str, int, int]: Final image name (or attempted canonical name on failure),
            upload count increment, duplicate count increment.
        """
        item_type = item_type.lower()
        path_segment = self.ITEM_SINGLE_TYPE_PATHS.get(item_type)
        if not path_segment:
            raise ValueError(f'Unsupported single-item upload type "{item_type}".')

        url = (
            'https://prd-game-a-granbluefantasy.akamaized.net/assets_en/'
            f'img/sp/assets/item/{path_segment}/{variant}/{item_id}.jpg'
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

        total_variants = len(items) * 2  # s and m variants
        processed_variants = 0
        successful_uploads = 0
        duplicate_matches = 0

        for item in items:
            item_id = item['id']
            item_name = item['name']
            item_type = item['type']

            for variant, redirect_suffix in [('s', 'square'), ('m', 'icon')]:
                current_image, uploaded_increment, duplicate_increment = self._process_item_variant(
                    item_type, item_id, item_name, variant, redirect_suffix
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
        if item_type not in self.ITEM_SINGLE_TYPE_PATHS:
            print(f'Unsupported item type "{item_type}" for single item upload.')
            return

        item_id = str(item_id).strip()
        item_name = mwparserfromhell.parse(item_name).strip_code().strip()

        if not item_id:
            print('Item id is required for single item upload.')
            return

        if not item_name:
            print('Item name is required for single item upload.')
            return

        print(f'Processing single item "{item_name}" (type: {item_type}, ID: {item_id})...')

        total_variants = 2  # s and m variants
        processed_variants = 0
        successful_uploads = 0
        duplicate_matches = 0

        for variant, redirect_suffix in [('s', 'square'), ('m', 'icon')]:
            current_image, uploaded_increment, duplicate_increment = self._process_item_variant(
                item_type, item_id, item_name, variant, redirect_suffix
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

    def check_character(self, page):
        paths = {
            'zoom':          ['png', '', ['_01', '_01_1', '_01_101', '_01_102', '_01_103', '_02', '_02_1', '_02_101', '_02_102', '_02_103', '_03', '_03_1', '_03_101', '_03_102', '_03_103', '_04', 
            '_81', '_82', '_88', '_91', '_91_0', '_91_1', 
            '_01_01', '_01_02', '_01_03', '_01_04', '_01_05', '_01_06', 
            '_02_01', '_02_02', '_02_03', '_02_04', '_02_05', '_02_06', 
            '_03_01', '_03_02', '_03_03', '_03_04', '_03_05', '_03_06'], 
            
            ['A', 'A2', 'A101', 'A102', 'A103', 'B', 'B2',  'B101', 'B102', 'B103', 'C', 'C2',  'C101', 'C102', 'C103', 'D', 'ST', 'ST2', 'ST8', 'EX', 'EX1', 'EX2',
            'A01', 'A02', 'A03', 'A04', 'A05', 'A06', 
            'B01', 'B02', 'B03', 'B04', 'B05', 'B06', 
            'C01', 'C02', 'C03', 'C04', 'C05', 'C06'], 
            ['Character Images', 'Full Character Images'  ]],
            
            'f_skin':             ['jpg', '_tall',   
            ['_01_s1', '_01_s2', '_01_s3', '_01_s4', '_01_s5', '_01_s6',
            '_01_101_s1', '_01_101_s2', '_01_101_s3', '_01_101_s4', '_01_101_s5', '_01_101_s6',
            '_01_102_s1', '_01_102_s2', '_01_102_s3', '_01_102_s4', '_01_102_s5', '_01_102_s6',
            '_01_103_s1', '_01_103_s2', '_01_103_s3', '_01_103_s4', '_01_103_s5', '_01_103_s6',
            '_02_s1', '_02_s2', '_02_s3', '_02_s4', '_02_s5', '_02_s6',
            '_02_1_s1', '_02_1_s2', '_02_1_s3', '_02_1_s4', '_02_1_s5', '_02_1_s6',
            '_02_101_s1', '_02_101_s2', '_02_101_s3', '_02_101_s4', '_02_101_s5', '_02_101_s6',
            '_02_102_s1', '_02_102_s2', '_02_102_s3', '_02_102_s4', '_02_102_s5', '_02_102_s6',
            '_02_103_s1', '_02_103_s2', '_02_103_s3', '_02_103_s4', '_02_103_s5', '_02_103_s6',
            '_03_s1', '_03_s2', '_03_s3', '_03_s4', '_03_s5', '_03_s6',
            '_03_101_s1', '_03_101_s2', '_03_101_s3', '_03_101_s4', '_03_101_s5', '_03_101_s6',
            '_03_102_s1', '_03_102_s2', '_03_102_s3', '_03_102_s4', '_03_102_s5', '_03_102_s6',
            '_03_103_s1', '_03_103_s2', '_03_103_s3', '_03_103_s4', '_03_103_s5', '_03_103_s6',
            '_04_s1', '_04_s2', '_04_s3', '_04_s4', '_04_s5', '_04_s6',
            '_81_s1', '_81_s2', '_81_s3', '_81_s4', '_81_s5', '_81_s6',
            '_82_s1', '_82_s2', '_82_s3', '_82_s4', '_82_s5', '_82_s6',
            '_91_s1', '_91_s2', '_91_s3', '_91_s4', '_91_s5', '_91_s6',

            '_01_01', '_01_02', '_01_03', '_01_04', '_01_05', '_01_06', 
            '_02_01', '_02_02', '_02_03', '_02_04', '_02_05', '_02_06', 
            '_03_01', '_03_02', '_03_03', '_03_04', '_03_05', '_03_06'], 
            ['A_fire', 'A_water', 'A_earth', 'A_wind', 'A_light', 'A_dark',
            'A101_fire', 'A101_water', 'A101_earth', 'A101_wind', 'A101_light', 'A101_dark',
            'A102_fire', 'A102_water', 'A102_earth', 'A102_wind', 'A102_light', 'A102_dark',
            'A103_fire', 'A103_water', 'A103_earth', 'A103_wind', 'A103_light', 'A103_dark',
            'B_fire', 'B_water', 'B_earth', 'B_wind', 'B_light', 'B_dark',
            'B2_fire', 'B2_water', 'B2_earth', 'B2_wind', 'B2_light', 'B2_dark',
            'B101_fire', 'B101_water', 'B101_earth', 'B101_wind', 'B101_light', 'B101_dark',
            'B102_fire', 'B102_water', 'B102_earth', 'B102_wind', 'B102_light', 'B102_dark',
            'B103_fire', 'B103_water', 'B103_earth', 'B103_wind', 'B103_light', 'B103_dark',
            'C_fire', 'C_water', 'C_earth', 'C_wind', 'C_light', 'C_dark',
            'C101_fire', 'C101_water', 'C101_earth', 'C101_wind', 'C101_light', 'C101_dark',
            'C102_fire', 'C102_water', 'C102_earth', 'C102_wind', 'C102_light', 'C102_dark',
            'C103_fire', 'C103_water', 'C103_earth', 'C103_wind', 'C103_light', 'C103_dark',
            'D_fire', 'D_water', 'D_earth', 'D_wind', 'D_light', 'D_dark',
            'ST_fire', 'ST_water', 'ST_earth', 'ST_wind', 'ST_light', 'ST_dark',
            'ST2_fire', 'ST2_water', 'ST2_earth', 'ST2_wind', 'ST2_light', 'ST2_dark',
            'EX_fire', 'EX_water', 'EX_earth', 'EX_wind', 'EX_light', 'EX_dark',

            'A01_fire', 'A01_water', 'A01_earth', 'A01_wind', 'A01_light', 'A01_dark',
            'A02_fire', 'A02_water', 'A02_earth', 'A02_wind', 'A02_light', 'A02_dark',
            'A03_fire', 'A03_water', 'A03_earth', 'A03_wind', 'A03_light', 'A03_dark',],
            ['Character Images', 'Tall Skin Character Images' ]],
            
            'f':             ['jpg', '_tall', ['_01', '_01_1', '_01_101', '_01_102', '_01_103', '_02', '_02_1', '_02_101', '_02_102', '_02_103', '_03', '_03_1', '_03_101', '_03_102', '_03_103', '_04', '_81', '_82', '_91', '_91_0', '_91_1',
            '_01_01', '_01_02', '_01_03', '_01_04', '_01_05', '_01_06', 
            '_02_01', '_02_02', '_02_03', '_02_04', '_02_05', '_02_06', 
            '_03_01', '_03_02', '_03_03', '_03_04', '_03_05', '_03_06'], 
            ['A', 'A2', 'A101', 'A102', 'A103', 'B', 'B2',  'B101', 'B102', 'B103', 'C', 'C2',  'C101', 'C102', 'C103', 'D', 'ST', 'ST2', 'EX', 'EX1', 'EX2',
            'A01', 'A02', 'A03', 'A04', 'A05', 'A06', 
            'B01', 'B02', 'B03', 'B04', 'B05', 'B06', 
            'C01', 'C02', 'C03', 'C04', 'C05', 'C06'], 
            ['Character Images', 'Tall Character Images'  ]],
            
            'm':             ['jpg', '_icon',   ['_01', '_01_1', '_01_101', '_01_102', '_01_103', '_02', '_02_1', '_02_101', '_02_102', '_02_103', '_03', '_03_1', '_03_101', '_03_102', '_03_103', '_04', 
            '_81', '_82', '_88', '_91', '_91_0', '_91_1',
            '_01_01', '_01_02', '_01_03', '_01_04', '_01_05', '_01_06', 
            '_02_01', '_02_02', '_02_03', '_02_04', '_02_05', '_02_06', 
            '_03_01', '_03_02', '_03_03', '_03_04', '_03_05', '_03_06'], 
            ['A', 'A2', 'A101', 'A102', 'A103', 'B', 'B2',  'B101', 'B102', 'B103', 'C', 'C2',  'C101', 'C102', 'C103', 'D', 'ST', 'ST2', 'ST8', 'EX', 'EX1', 'EX2',
            'A01', 'A02', 'A03', 'A04', 'A05', 'A06', 
            'B01', 'B02', 'B03', 'B04', 'B05', 'B06', 
            'C01', 'C02', 'C03', 'C04', 'C05', 'C06'], 
            
            ['Character Images', 'Icon Character Images'  ]],
            
            's':             ['jpg', '_square', 
            ['_01', '_01_1', '_01_101', '_01_102', '_01_103', '_02',  '_02_1', '_02_101', '_02_102', '_02_103',   '_03', '_03_1', '_03_101', '_03_102', '_03_103', '_04', '_81', '_82', '_88', '_91', '_91_0', '_91_1',
            '_01_01', '_01_02', '_01_03', '_01_04', '_01_05', '_01_06', 
            '_02_01', '_02_02', '_02_03', '_02_04', '_02_05', '_02_06', 
            '_03_01', '_03_02', '_03_03', '_03_04', '_03_05', '_03_06'], 
            ['A', 'A2', 'A101', 'A102', 'A103', 'B', 'B2', 'B101', 'B102', 'B103', 'C', 'C2', 'C101', 'C102', 'C103', 'D', 'ST', 'ST2', 'ST8', 'EX', 'EX1', 'EX2',
            'A01', 'A02', 'A03', 'A04', 'A05', 'A06', 
            'B01', 'B02', 'B03', 'B04', 'B05', 'B06', 
            'C01', 'C02', 'C03', 'C04', 'C05', 'C06'], 
            ['Character Images', 'Square Character Images']],
            
            'sd':            ['png', '_SD',     ['_01', '_01_1', '_01_101', '_01_102', '_01_103', '_02', '_02_1', '_02_101', '_02_102', '_02_103', '_03', '_03_1', '_03_101', '_03_102', '_03_103', '_04', '_81', '_82', '_91', '_91_0', '_91_1'], ['A', 'A2', 'A101', 'A102', 'A103', 'B', 'B2',  'B101', 'B102', 'B103', 'C', 'C2',  'C101', 'C102', 'C103', 'D', 'ST', 'ST2', 'EX', 'EX1', 'EX2'], ['Character Images', 'Sprite Character Images']],
            
            'cutin_special': ['jpg', '_cutin',     ['_01', '_01_1', '_01_101', '_01_102', '_01_103', '_02', '_02_1', '_02_101', '_02_102', '_02_103', '_03', '_03_1', '_03_101', '_03_102', '_03_103', '_04', '_81', '_82', '_91', '_91_0', '_91_1'], ['A', 'A2', 'A101', 'A102', 'A103', 'B', 'B2',  'B101', 'B102', 'B103', 'C', 'C2',  'C101', 'C102', 'C103', 'D', 'ST', 'ST2', 'EX', 'EX1', 'EX2'], ['Character Images', 'Cutin Character Images']],
            
            'raid_chain': ['jpg', '_chain',     ['_01', '_01_1', '_01_101', '_01_102', '_01_103', '_02', '_02_1', '_02_101', '_02_102', '_02_103', '_03', '_03_1', '_03_101', '_03_102', '_03_103', '_04', '_81', '_82', '_91', '_91_0', '_91_1'], ['A', 'A2', 'A101', 'A102', 'A103', 'B', 'B2',  'B101', 'B102', 'B103', 'C', 'C2',  'C101', 'C102', 'C103', 'D', 'ST', 'ST2', 'EX', 'EX1', 'EX2'], ['Character Images', 'Chain Burst Character Images']],
            
            't': ['png', '_babyl',     ['_01', '_01_1', '_01_101', '_01_102', '_01_103', '_02', '_02_1', '_02_101', '_02_102', '_02_103', '_03', '_03_1', '_03_101', '_03_102', '_03_103', '_04', '_81', '_82', '_91', '_91_0', '_91_1'], ['A', 'A2', 'A101', 'A102', 'A103', 'B', 'B2',  'B101', 'B102', 'B103', 'C', 'C2',  'C101', 'C102', 'C103', 'D', 'ST', 'ST2', 'EX', 'EX1', 'EX2'], ['Character Images', 'Babyl Character Images']],
            
            'detail': ['png', '_detail',     ['_01', '_01_1', '_01_101', '_01_102', '_01_103', '_02', '_02_1', '_02_101', '_02_102', '_02_103', '_03', '_03_1', '_03_101', '_03_102', '_03_103', '_04', '_81', '_82', '_91', '_91_0', '_91_1'], ['A', 'A2', 'A101', 'A102', 'A103', 'B', 'B2',  'B101', 'B102', 'B103', 'C', 'C2',  'C101', 'C102', 'C103', 'D', 'ST', 'ST2', 'EX', 'EX1', 'EX2'], ['Character Images', 'Detail Character Images']],
            
            'raid_normal': ['jpg', '_raid',     ['_01', '_01_1', '_01_101', '_01_102', '_01_103', '_02', '_02_1', '_02_101', '_02_102', '_02_103', '_03', '_03_1', '_03_101', '_03_102', '_03_103', '_04', '_81', '_82', '_91', '_91_0', '_91_1'], ['A', 'A2', 'A101', 'A102', 'A103', 'B', 'B2',  'B101', 'B102', 'B103', 'C', 'C2',  'C101', 'C102', 'C103', 'D', 'ST', 'ST2', 'EX', 'EX1', 'EX2'], ['Character Images', 'Raid Character Images']],
            
            'quest': ['jpg', '_quest',     ['_01', '_01_1', '_01_101', '_01_102', '_01_103', '_02', '_02_1', '_02_101', '_02_102', '_02_103', '_03', '_03_1', '_03_101', '_03_102', '_03_103', '_04', '_81', '_82', '_91', '_91_0', '_91_1'], ['A', 'A2', 'A101', 'A102', 'A103', 'B', 'B2',  'B101', 'B102', 'B103', 'C', 'C2',  'C101', 'C102', 'C103', 'D', 'ST', 'ST2', 'EX', 'EX1', 'EX2'], ['Character Images', 'Quest Character Images']],
            
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

    def check_summon(self, page):
        paths = {
            'b':  ['png', '',        ['', '_02', '_03', '_04'], ['A', 'B', 'C', 'D'], ['Summon Images', 'Full Summon Images'  ]],
            'ls': ['jpg', '_tall',   ['', '_02', '_03', '_04'], ['A', 'B', 'C', 'D'], ['Summon Images', 'Tall Summon Images'  ]],
            'm':  ['jpg', '_icon',   ['', '_02', '_03', '_04'], ['A', 'B', 'C', 'D'], ['Summon Images', 'Icon Summon Images'  ]],
            's':  ['jpg', '_square', ['', '_02', '_03', '_04'], ['A', 'B', 'C', 'D'], ['Summon Images', 'Square Summon Images']],
            'party_main':  ['jpg', '_party_main', ['', '_02', '_03', '_04'], ['A', 'B', 'C', 'D'], ['Summon Images', 'Party Main Summon Images']],
            'party_sub':  ['jpg', '_party_sub', ['', '_02', '_03', '_04'], ['A', 'B', 'C', 'D'], ['Summon Images', 'Party Sub Summon Images']],
            'detail':  ['png', '_detail', ['', '_02', '_03', '_04'], ['A', 'B', 'C', 'D'], ['Summon Images', 'Detail Summon Images']],
            #http://prd-game-a-granbluefantasy.akamaized.net/assets_en/img/sp/assets/summon/b/1010200300.png
            #http://prd-game-a-granbluefantasy.akamaized.net/assets_en/img/sp/assets/summon/ls/1010200300.jpg
            #http://prd-game-a-granbluefantasy.akamaized.net/assets_en/img/sp/assets/summon/m/1010200300.jpg
            #http://prd-game-a-granbluefantasy.akamaized.net/assets_en/img/sp/assets/summon/s/1010200300.jpg
        }
        self.check_sp_asset(page, 'summon', 'Summon', paths, False)

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

    def check_npc(self, page):
        paths = {
            'zoom':          ['png', '',        ['_01'], [''], ['NPC Images', 'Full NPC Images'  ]],
            'm':             ['jpg', '_icon',   ['_01'], [''], ['NPC Images', 'Icon NPC Images'  ]],
        }
        self.check_sp_asset(page, 'npc', 'Non-party Character', paths, False)

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

            # 'f_skin': [
            #     'jpg', 
            #     '_tall',
            #     [
            #         # Shorthand variants (uncap → elements 1–6)
            #         '_01_s1', '_01_s2', '_01_s3', '_01_s4', '_01_s5', '_01_s6',
            #         '_81_s1', '_81_s2', '_81_s3', '_81_s4', '_81_s5', '_81_s6',

            #         # Expanded variants for _01 (male then female, fire–dark order)
            #         '_01_0_s1', '_01_0_s2', '_01_0_s3', '_01_0_s4', '_01_0_s5', '_01_0_s6',
            #         '_01_1_s1', '_01_1_s2', '_01_1_s3', '_01_1_s4', '_01_1_s5', '_01_1_s6',

            #         # Expanded variants for _81 (male then female, fire–dark order)
            #         '_81_0_s1', '_81_0_s2', '_81_0_s3', '_81_0_s4', '_81_0_s5', '_81_0_s6',
            #         '_81_1_s1', '_81_1_s2', '_81_1_s3', '_81_1_s4', '_81_1_s5', '_81_1_s6'
            #     ],
            #     [
            #         # Shorthand element tags for _01 and _81
            #         '_fire', '_water', '_earth', '_wind', '_light', '_dark',
            #         '_alt_fire', '_alt_water', '_alt_earth', '_alt_wind', '_alt_light', '_alt_dark',
                    
            #         # Gendered element tags for _01
            #         '_alt0_fire', '_alt0_water', '_alt0_earth', '_alt0_wind', '_alt0_light', '_alt0_dark',
            #         '_alt1_fire', '_alt1_water', '_alt1_earth', '_alt1_wind', '_alt1_light', '_alt1_dark',

            #         # Gendered element tags for _81
            #         '_alt0_fire_81', '_alt0_water_81', '_alt0_earth_81', '_alt0_wind_81', '_alt0_light_81', '_alt0_dark_81',
            #         '_alt1_fire_81', '_alt1_water_81', '_alt1_earth_81', '_alt1_wind_81', '_alt1_light_81', '_alt1_dark_81'
            #     ],
            #     ['Outfit Images', 'Full F_Skin Images']
            # ],

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

            for param in template.params:
                param_name = param.name.strip()
                if param_name == 'id':
                    asset_id = param.value.strip()
                    asset_match = re.match(r'^{{{id\|([A-Za-z0-9_]+)}}}', asset_id)
                    if asset_match != None:
                        asset_id = asset_match.group(1)
                    asset_ids.append(asset_id)
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
                        if section == 'wsp':
                            url = (
                                'http://prd-game-a-granbluefantasy.akamaized.net/assets_en/'
                                'img/sp/cjs/{0}{1}.{2}'
                            ).format(
                                asset_id,
                                params[2][version],
                                params[0]
                            )
                            section_label = 'sp'
                        elif section == 'f_skin':
                            url = (
                                'http://prd-game-a-granbluefantasy.akamaized.net/assets_en/'
                                'img/sp/assets/{0}/{1}/{2}{3}.{4}'
                            ).format(
                                asset_type,
                                'f/skin',
                                asset_id,
                                params[2][version],
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
                                params[2][version],
                                params[0]
                            )
                            section_label = section

                        true_name = "{0} {1} {2}{3}.{4}".format(
                            asset_type.capitalize(),
                            section_label,
                            asset_id,
                            params[2][version],
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
                            other_names.append(
                                '{0}{1}{2}.{3}'.format(
                                    asset_name,
                                    params[1],
                                    (' ' if (params[1] == '' and params[3][version] != '') else '') + params[3][version],
                                    params[0]
                                )
                            )
                        
                        download_tasks.append({
                            'url': url,
                            'true_name': true_name,
                            'other_names': other_names,
                            'categories': params[4]
                        })
                        total_urls_generated += 1
        
        # Download all images concurrently
        if download_tasks:
            urls = [task['url'] for task in download_tasks]
            print(f"Starting concurrent download of {len(urls)} images...")
            print(f"Sample URLs: {urls[:2]}...")
            
            try:
                # Run async function in current thread
                loop = asyncio.new_event_loop()
                asyncio.set_event_loop(loop)
                try:
                    # Add timeout to prevent hanging
                    download_task = self.get_images_concurrent(urls)
                    download_results = loop.run_until_complete(asyncio.wait_for(download_task, timeout=300))  # 5 minute timeout
                finally:
                    loop.close()
                
                print(f"Download results received: {len(download_results)} results")
                
                successful = sum(1 for _, success, _, _, _ in download_results if success)
                failed = len(download_results) - successful
                print(f"Download Status Report:")
                print(f"  Successful (200): {successful}")
                print(f"  Failed (404/errors): {failed}")
                print(f"  Total attempted: {len(download_results)}")
                
                if hasattr(self, '_status_callback'):
                    self._status_callback("downloaded", successful=successful, failed=failed, total=len(download_results))
            except Exception as e:
                print(f"Concurrent download failed: {e}")
                print("Falling back to sequential downloads...")
                
                # Fallback to original sequential method
                for task in download_tasks:
                    success, sha1, size, io_obj = self.get_image(task['url'])
                    if success:
                        check_image_result = self.check_image(task['true_name'], sha1, size, io_obj, task['other_names'])
                        if check_image_result == True:
                            pass
                        elif check_image_result == False:
                            print('Checking image {0} failed! Skipping...'.format(task['true_name']))
                            continue
                        else:
                            task['true_name'] = check_image_result
                        
                        self.check_image_categories(task['true_name'], task['categories'])
                        
                        for other_name in task['other_names']:
                            self.check_file_redirect(task['true_name'], other_name)
                        
                        time.sleep(self.delay)  # Keep wiki delay
                        
                        self.check_file_double_redirect(task['true_name'])
                return
            
            # Process results with wiki operations (keep delays for wiki politeness)
            images_processed = 0
            images_uploaded = 0
            images_duplicate = 0
            images_failed = 0
            
            for i, (url, success, sha1, size, io_obj) in enumerate(download_results):
                if success:
                    images_processed += 1
                    task = download_tasks[i]
                    print(f"Processing image {images_processed}/{successful}: {task['true_name']}")
                    
                    if hasattr(self, '_status_callback'):
                        self._status_callback("processing", processed=images_processed, total=successful, current_image=task['true_name'])
                    
                    check_image_result = self.check_image(task['true_name'], sha1, size, io_obj, task['other_names'])
                    
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
                                'http://prd-game-a-granbluefantasy.akamaized.net/assets_en/'
                                'img/sp/assets/{0}/f/skin/{1}{2}.{3}'
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

                        success, sha1, size, io = self.get_image(url)
                        if success:
                            true_name = "{0} {1} {2}{3}.{4}".format(
                                asset_type.capitalize(),
                                section,
                                asset_id,
                                params[2][version],
                                params[0]
                            )
                            other_names = []

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

        uploads_attempted = 0
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
                total=uploads_attempted,
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
            nonlocal uploads_attempted
            any_success = False

            for variant in variants:
                variant_success = False
                for gender in genders:
                    uploads_attempted += 1
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
                        variant_success = True
                        any_success = True
                if adjust_attempts_for_failures and not variant_success:
                    uploads_attempted -= len(genders)

            return any_success

        def process_single_asset(label_text, url, canonical_name, other_names, extra_categories=None):
            nonlocal uploads_attempted
            uploads_attempted += 1
            return process_asset(label_text, url, canonical_name, other_names, extra_categories)

        def emit_status(stage, **kwargs):
            if hasattr(self, '_status_callback'):
                self._status_callback(stage, **kwargs)

        if has_class_fields('id', 'id_num', 'abbr'):
            sprite_variants = build_variants('class sprite')
            process_gendered_assets(
                sprite_variants,
                lambda variant, gender: (
                    'https://prd-game-a-granbluefantasy.akamaized.net/assets_en/'
                    f'img/sp/assets/leader/sd/{variant["id"]}_{variant["abbr"]}_{gender}_01.png'
                ),
                lambda variant, gender: (
                    f'leader_sd_{variant["id_num"]}_{variant["abbr"]}_{gender}_01.png'
                ),
                lambda variant, gender, alias: [
                    f'leader_sd_{variant["id_num"]}_{gender}_01.png',
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
            total_urls=uploads_attempted,
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
