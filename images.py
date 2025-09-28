import os
import sys
import requests
import mwclient
import mwparserfromhell
import urllib.request
import re
import time
import hashlib
from io import BytesIO
from gbfwiki import GBFWiki, GBFDB

# optional for local development only
try:
    from dotenv import load_dotenv
    load_dotenv()
except Exception:
    # python-dotenv not installed or not desired — that's fine in production
    pass

# read credentials from env
WIKI_USERNAME = os.environ.get("WIKI_USERNAME")
WIKI_PASSWORD = os.environ.get("WIKI_PASSWORD")
MITM_ROOT = os.environ.get("MITM_ROOT")

class WikiImages(object):
    def __init__(self):
        # 1) login: prefer env vars; fallback to existing behavior
        if WIKI_USERNAME and WIKI_PASSWORD:
            # Try to pass credentials into GBFWiki.login if it accepts them
            try:
                # common pattern: GBFWiki.login(username, password)
                self.wiki = GBFWiki.login(WIKI_USERNAME, WIKI_PASSWORD)
            except TypeError:
                # If GBFWiki.login() doesn't accept args, try fallback strategies.
                # First try calling login() then performing instance login (some libs expose .login())
                try:
                    self.wiki = GBFWiki.login()
                    # many wrappers have an instance method to authenticate; try it if present
                    if hasattr(self.wiki, "login"):
                        try:
                            self.wiki.login(WIKI_USERNAME, WIKI_PASSWORD)
                        except Exception:
                            # last resort: if login method signature differs, ignore and continue
                            pass
                except Exception:
                    # final fallback - call original no-arg login (as before)
                    self.wiki = GBFWiki.login()
        else:
            # no env vars set -> keep old behavior (reads from file)
            self.wiki = GBFWiki.login()

        # 2) db unchanged
        self.db = GBFDB()

        # 3) mitm root: prefer MITM_ROOT env var, otherwise fall back to GBFWiki.mitmpath()
        self.mitm_root = MITM_ROOT or GBFWiki.mitmpath()

        # 4) other settings
        self.delay = 25


    def get_image(self, url):
        print('Downloading {0}...'.format(url))
        req = requests.get(url, stream=True) # was originally stream=True
        if req.status_code != 200:
            print('Download failed for: {0}'.format(url))
            return False, "", 0, False

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

    def check_image(self, name, sha1, size, io, other_names):
        true_name = name.capitalize()
        file_name = 'File:' + true_name
        wiki_duplicates = list(self.wiki.allimages(minsize=size, maxsize=size, sha1=sha1))

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
            dupe.move(file_name, reason='Batch upload file name')
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
                        page.move(file_name, 'Batch upload file name (sha1 not found)')

                        self.investigate_backlinks(backlinks, page.name, file_name)

            # upload image
            print('Uploading "{0}"...'.format(file_name))
            io.seek(0)
            response = self.wiki.upload(io, filename=true_name, ignore=True)
            print(response['result'] + ': ' + name)
            if response['result'] == 'Warning':
                return False
            return true_name

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
                        if section == 'wsp':
                            url = (
                                'http://prd-game-a-granbluefantasy.akamaized.net/assets_en/'
                                'img/sp/cjs/{0}{1}.{2}'
                            ).format(
                                asset_id,
                                params[2][version],
                                params[0]
                            )
                            section = 'sp'
                            
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
                            section = 'f_skin'
                            
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
        class_id = ''
        class_name = ''

        pagetext = page.text()
        wikicode = mwparserfromhell.parse(pagetext)
        templates = wikicode.filter_templates()
        for template in templates:
            template_name = template.name.strip()
            if template_name != 'Class':
                continue

            for param in template.params:
                param_name = param.name.strip()
                if param_name == 'id':
                    class_id = param.value.strip()
                    class_id_num = class_id.split('_')[0]
                elif param_name == 'class':
                    class_name = param.value.strip()
                elif param_name == 'family':
                    class_family = param.value.strip()
                    if class_family == 'swordmaster':
                        class_family = 'Sword Master'
                    elif class_family == 'drummaster':
                        class_family = 'Drum Master'
                    elif class_id == '140401':
                        class_family = 'Street King'
                    else:
                        class_family = 'Street King' #class_family.capitalize()

                elif param_name == 'row':
                    family_evo = int(param.value.strip()) % 10

        if (class_id != '') and (class_name != ''):
            paths = {
                # 'a': ['png', '_square'],
                
                's': ['jpg', '_square'],
                'sd': ['png', '_sprite'],
                'raid_normal': ['jpg', '_profile'],
                'jobm': ['jpg', '_icon'],
                'job_change': ['png', ''],
                'my': ['png', '_homescreen'],
            }

            url = (
                'http://prd-game-a-granbluefantasy.akamaized.net/assets_en/'
                'img/sp/ui/icon/job/{0}.png'
                # 'img/sp/assets/leader/sd/{0}.png'
            ).format(
                class_id_num
            )
            success, sha1, size, io = self.get_image(url)
            if success:
                true_name = 'Class {0} icon.png'.format(class_id)
                roman = ['0','I','II','III','IV','V']
                other_names = [
                    'Icon_{0}.png'.format(class_name),
                    'Icon_{0}_{1}.png'.format(class_family, family_evo),
                    'Icon_{0}_{1}.png'.format(class_family, roman[family_evo])
                ]

                check_image_result = self.check_image(true_name, sha1, size, io, other_names)
                if check_image_result == True:
                    pass
                elif check_image_result == False:
                    print('Checking image {0} failed! Skipping...'.format(true_name))
                else:
                    true_name = check_image_result

                for other_name in other_names:
                    self.check_file_redirect(true_name, other_name)

            #/assets_en/img/sp/assets/leader/s/300301_01.jpg
            url = (
                'http://prd-game-a-granbluefantasy.akamaized.net/assets_en/'
                'img/sp/assets/leader/s/{0}_01.jpg'
            ).format(
                class_id_num
            )
            success, sha1, size, io = self.get_image(url)
            if success:
                true_name = 'Class {0} square.jpg'.format(class_id)
                roman = ['0','I','II','III','IV','V']
                other_names = [
                    '{0}_square.jpg'.format(class_name),
                    '{0}_{1}_square.jpg'.format(class_family, family_evo),
                    '{0}_{1}_square.jpg'.format(class_family, roman[family_evo])
                ]

                check_image_result = self.check_image(true_name, sha1, size, io, other_names)
                if check_image_result == True:
                    pass
                elif check_image_result == False:
                    print('Checking image {0} failed! Skipping...'.format(true_name))
                else:
                    true_name = check_image_result

                for other_name in other_names:
                    self.check_file_redirect(true_name, other_name)
                    
                    
            #/assets_en/img/sp/assets/leader/jobtree/300301.png
            url = (
                'http://prd-game-a-granbluefantasy.akamaized.net/assets_en/'
                'img/sp/assets/leader/jobtree/{0}.png'
            ).format(
                class_id_num
            )
            success, sha1, size, io = self.get_image(url)
            if success:
                true_name = 'Class {0} jobtree.png'.format(class_id)
                roman = ['0','I','II','III','IV','V']
                other_names = [
                    '{0}_jobtree.png'.format(class_name),
                    '{0}_{1}_jobtree.png'.format(class_family, family_evo),
                    '{0}_{1}_jobtree.png'.format(class_family, roman[family_evo])
                ]

                check_image_result = self.check_image(true_name, sha1, size, io, other_names)
                if check_image_result == True:
                    pass
                elif check_image_result == False:
                    print('Checking image {0} failed! Skipping...'.format(true_name))
                else:
                    true_name = check_image_result

                for other_name in other_names:
                    self.check_file_redirect(true_name, other_name)

            #/assets/img/sp/ui/icon/job/300201.png
            for section, params in paths.items():
                for step in range(0, 2):
                    time.sleep(self.delay)
                    url = (
                        'http://prd-game-a-granbluefantasy.akamaized.net/assets_en/'
                        'img/sp/assets/leader/{0}/{1}_{2}_01.{3}'
                    ).format(
                        section, class_id, step, params[0]
                    )

                    success, sha1, size, io = self.get_image(url)
                    if success:
                        true_name = '{0}_{1}{2}.{3}'.format(
                            class_name,
                            'gran' if step == 0 else "djeeta",
                            params[1],
                            params[0]
                        )
                        other_names = []

                        check_image_result = self.check_image(true_name, sha1, size, io, other_names)
                        if check_image_result == True:
                            pass
                        elif check_image_result == False:
                            print('Checking image {0} failed! Skipping...'.format(true_name))

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
