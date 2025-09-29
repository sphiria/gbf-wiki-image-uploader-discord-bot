import os, sys
import mwclient

print("🔎 Starting test script...", flush=True)

try:
    username = os.environ.get("WIKI_USERNAME")
    password = os.environ.get("WIKI_PASSWORD")

    if not username or not password:
        print("❌ Missing WIKI_USERNAME or WIKI_PASSWORD env vars", flush=True)
        sys.exit(1)

    print(f"📛 Loaded username: {username}", flush=True)

    print("🌐 Connecting to gbf.wiki...", flush=True)
    site = mwclient.Site(
        ("https", "gbf.wiki"),
        path="/",
        clients_useragent="DiscordImageUploaderAdlaiBot/1.0 (contact: your-discord#tag)"
    )

    print("🔑 Attempting login...", flush=True)
    site.login(username, password)

    print("📥 Fetching userinfo...", flush=True)
    userinfo = site.get('query', meta='userinfo')

    print("✅ Connected as:", userinfo['query']['userinfo']['name'], flush=True)

except Exception as e:
    print("❌ Connection failed with exception:", repr(e), flush=True)