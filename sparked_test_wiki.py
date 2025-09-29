import os
import mwclient
import sys
print("🔎 Testing connection to gbf.wiki...")
sys.stdout.flush()

try:
    site = mwclient.Site(
        ("https", "gbf.wiki"),
        path="/",
        clients_useragent="DiscordImageUploaderAdlaiBot/1.0 (contact: your-discord#tag)"
    )

    site.login(os.environ["WIKI_USERNAME"], os.environ["WIKI_PASSWORD"])
    userinfo = site.get('query', meta='userinfo')

    print("✅ Connected as:", userinfo['query']['userinfo']['name'])

except Exception as e:
    print("❌ Connection failed:", e)
