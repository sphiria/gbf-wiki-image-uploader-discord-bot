import os
import mwclient
import mwparserfromhell
import configparser
import codecs
import sqlite3

class GBFWiki:
    @staticmethod
    def login():
        # 1. Try env vars
        username = os.environ.get("WIKI_USERNAME")
        password = os.environ.get("WIKI_PASSWORD")

        # 2. Fallback: try config file
        if not username or not password:
            conf = configparser.ConfigParser()
            conf.read("login and pass.txt")
            if conf.has_section("GBFWikiLogin"):
                username = conf.get("GBFWikiLogin", "username", fallback=None)
                password = conf.get("GBFWikiLogin", "password", fallback=None)

        if not username or not password:
            raise RuntimeError("Wiki credentials missing.")

        # 3. Connect to the wiki with a custom User-Agent
        site = mwclient.Site(
            ("https", "gbf.wiki"),
            path="/",
            clients_useragent="DiscordImageUploaderAdlaiBot/1.0"
        )

        # 4. Perform login
        site.login(username, password)
        return site

    @staticmethod
    def mitmpath():
        return os.environ.get("MITM_ROOT", "")

    @staticmethod
    def mitm_game_path():
        return GBFWiki.mitmpath() + 'game.granbluefantasy.jp\\'

    @staticmethod
    def mitm_game_a1_assets():
        return GBFWiki.mitmpath() + 'game-a1.granbluefantasy.jp\\assets_en\\'

    @staticmethod
    def scriptpath():
        return os.path.dirname(os.path.realpath(__file__))

    @staticmethod
    def get_name_map():
        cache_path = GBFWiki.scriptpath() + '\\gamewith_cache'
        name_map_path = cache_path + '\\name_map.txt'

        name_map = {}
        with codecs.open(name_map_path, 'r', 'utf-8') as txt:
            for name in txt:
                name = name.strip()
                name_en, name_gw = name.split('|', 2)
                name_map[name_en] = name_gw
                name_map[name_gw] = name_en

        return name_map

    @staticmethod
    def get_template(pagetext, template_name):
        templates = GBFWiki.get_templates(pagetext, template_name)
        if len(templates) > 0:
            return templates[0]
        return None

    @staticmethod
    def get_templates(pagetext, template_name):
        if isinstance(pagetext, str):
            wikicode = mwparserfromhell.parse(pagetext)
        else:
            wikicode = pagetext
        templates = wikicode.filter_templates()
        return [template for template in templates if template.name.matches(template_name)]

class GBFDB():
    def __init__(self):
        self.db = sqlite3.connect('wiki.db')
        self.db.row_factory = sqlite3.Row
        self.verify_db()
        self.debug = False

    def verify_db(self):
        self.verify_table('pages', """
            CREATE TABLE pages (
                name     TEXT PRIMARY KEY,
                pagetext TEXT,
                revision INTEGER
            );
        """)
        self.verify_table("images", """
            CREATE TABLE images (
                url     TEXT PRIMARY KEY,
                size    INTEGER
                sha1    TEXT
            )
        """)
        self.verify_table("image_data", """
            CREATE TABLE image_data (
                size    INTEGER,
                sha1    TEXT,
                data    BLOB,
                PRIMARY KEY (size, sha1)
            );
        """)
        self.verify_table('wiki_weapons', """
            CREATE TABLE wiki_weapons (
                name         TEXT PRIMARY KEY,
                id           INTEGER,
                `group`      TEXT,
                element      TEXT,
                type         TEXT,
                rarity       TEXT,
                skill11_id   INTEGER,
                skill11_icon TEXT,
                skill11_name TEXT,
                skill11_desc TEXT,
                skill11_lvl  INTEGER,
                skill12_id   INTEGER,
                skill12_icon TEXT,
                skill12_name TEXT,
                skill12_desc TEXT,
                skill12_lvl  INTEGER,
                skill21_id   INTEGER,
                skill21_icon TEXT,
                skill21_name TEXT,
                skill21_desc TEXT,
                skill21_lvl  INTEGER,
                skill22_id   INTEGER,
                skill22_icon TEXT,
                skill22_name TEXT,
                skill22_desc TEXT,
                skill22_lvl  INTEGER,
                revision     INTEGER
            );
        """)
        self.verify_table('wiki_characters', """
            CREATE TABLE wiki_characters (
            pagename TEXT PRIMARY KEY,
                id INTEGER,
                charid TEXT,
                jpname TEXT,
                jptitle TEXT,
                jpva TEXT,
                name TEXT,
                release_date TEXT,
                gender TEXT,
                obtain TEXT,
                title TEXT,
                `5star` TEXT,
                base_evo INTEGER,
                max_evo INTEGER,
                art1 TEXT,
                art2 TEXT,
                art3 TEXT,
                sprite1 TEXT,
                sprite2 TEXT,
                sprite3 TEXT,
                rarity TEXT,
                element TEXT,
                type TEXT,
                race TEXT,
                va TEXT,
                `join` TEXT,
                join_weapon TEXT,
                weapon TEXT,
                min_atk INTEGER,
                max_atk INTEGER,
                flb_atk INTEGER,
                bonus_atk INTEGER,
                min_hp INTEGER,
                max_hp INTEGER,
                flb_hp INTEGER,
                bonus_hp INTEGER,
                a_tags TEXT,
                abilitycount TEXT,
                a1_icon TEXT,
                a1_name TEXT,
                a1_cd TEXT,
                a1_dur TEXT,
                a1_oblevel TEXT,
                a1_effdesc TEXT,
                a2_icon TEXT,
                a2_name TEXT,
                a2_cd TEXT,
                a2_dur TEXT,
                a2_oblevel TEXT,
                a2_effdesc TEXT,
                a3_icon TEXT,
                a3_name TEXT,
                a3_cd TEXT,
                a3_dur TEXT,
                a3_oblevel TEXT,
                a3_effdesc TEXT,
                a4_icon TEXT,
                a4_name TEXT,
                a4_cd TEXT,
                a4_dur TEXT,
                a4_oblevel TEXT,
                a4_effdesc TEXT,
                s_abilitycount TEXT,
                sa_name TEXT,
                sa_level TEXT,
                sa_desc TEXT,
                sa2_name TEXT,
                sa2_level TEXT,
                sa2_desc TEXT,
                sa_emp_desc TEXT,
                ougi_count TEXT,
                ougi_name TEXT,
                ougi_desc TEXT,
                ougi2_name TEXT,
                ougi2_desc TEXT,
                ougi3_name TEXT,
                ougi3_desc TEXT,
                ougi4_name TEXT,
                ougi4_desc TEXT,
                perk1 TEXT,
                perk2 TEXT,
                perk3 TEXT,
                perk4 TEXT,
                perk5 TEXT,
                perk6 TEXT,
                perk7 TEXT,
                perk8 TEXT,
                perk9 TEXT,
                perk10 TEXT,
                perk11 TEXT,
                perk12 TEXT,
                perk13 TEXT,
                perk14 TEXT,
                perk15 TEXT,
                perk16 TEXT,
                perk17 TEXT,
                perk18 TEXT,
                perk19 TEXT,
                perk20 TEXT,
                profile TEXT
            );
        """)

    def verify_table(self, name, create):
        c = self.cursor()
        c.execute('SELECT * FROM sqlite_master WHERE name = :name;', {'name': name})
        row = c.fetchone()
        if row == None:
            c.execute(create)
            self.db.commit()

    def cursor(self):
        return self.db.cursor()

    def update_row(self, table_name, pk_name, row, item):
        c = self.cursor()
        columns = item.keys()
        if row == None:
            cols1 = '`,`'.join(columns)
            cols2 = ',:'.join(columns)
            c.execute('INSERT INTO {0} (`{1}`) VALUES (:{2});'.format(
                table_name,
                cols1,
                cols2
            ), item)
        else:
            row_keys = row.keys()
            diff = {}
            for k, v in item.items():
                if isinstance(row[k], int):
                    temp = int(item[k])
                    if row[k] != temp:
                        diff[k] = item[k]
                elif row[k] != item[k]:
                    diff[k] = item[k]
            if len(diff) > 0:
                cols = []
                for k in diff.keys():
                    cols.append('{0} = :{1}'.format(k, k))
                diff[pk_name] = item[pk_name]
                cols = '`, `'.join(cols)

                c.execute('UPDATE {0} SET `{1}` WHERE {2} = :{2}'.format(
                    table_name,
                    cols,
                    pk_name
                ), item)

        self.db.commit()

    def pagetext(self, wiki, pagename, revision=-1):
        c = self.cursor()
        try:
            c.execute('SELECT name, pagetext, revision FROM pages WHERE name = :name;', {'name': pagename})
            row = c.fetchone()
            if row != None:
                if row[2] == revision:
                    if self.debug:
                        print('pagetext: Page "{0}" revision {1} up to date.'.format(pagename, revision))
                    return row[1]

            page = wiki.pages[pagename]
            if page.exists:
                pagetext = page.text()
                c.execute(
                    'INSERT OR REPLACE INTO pages (name, pagetext, revision) VALUES (:name, :pagetext, :revision);', {
                        'name': page.name,
                        'pagetext': pagetext,
                        'revision': page.revision
                })
                self.db.commit()
                if self.debug:
                    print('pagetext: Page "{0}" updated to revision {1}.'.format(pagename, page.revision))

                return pagetext

            if self.debug:
                print('pagetext: Page "{0}" not found.'.format(pagename))

            return None
        finally:
            c.close()

    def update_cache(self, wiki):
        self.update_characters(wiki)
        self.update_summons(wiki)
        self.update_weapons(wiki)

    def update_characters(self, wiki):
        pages = wiki.categories['Characters']
        for page in pages:
            self.pagetext(wiki, page.name, page.revision)

    def update_summons(self, wiki):
        pages = wiki.categories['Summons']
        for page in pages:
            self.pagetext(wiki, page.name, page.revision)

    def update_weapons(self, wiki):
        pages = wiki.categories['Weapons']
        for page in pages:
            self.pagetext(wiki, page.name, page.revision)




