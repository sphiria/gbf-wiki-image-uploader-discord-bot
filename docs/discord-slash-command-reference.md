**Wiki Image Upload Bot - Discord Slash Command Reference**

__General Rules__
- Commands (except `/synccommands`) require one of the allowed roles (`Wiki Editor`, `Wiki Admin`, `Wiki Discord Moderator`, `Verified Editor` by default) or the server owner; responses are ephemeral when the check fails.
- Every user has a 5s cooldown per upload-style command, and the bot only runs one upload at a time, so kick off the next request after the previous status message completes.
- Progress pings land every ~15s; final summaries include key counts and wiki links. If the bot runs in dry-run mode you will see a `[DRY RUN]` prefix.

**/imgupload**
Usage: `/imgupload page_type:<character|weapon|summon|class|skin|npc|artifact|item> page_name:<Wiki Page Title>`
- Purpose: Pull every image the upload scripts expect for a wiki page and push them to the correct file titles.
- Inputs:
  - `page_type` - pick the asset family; determines which CDN paths are scanned.
  - `page_name` - target wiki page (1-100 chars; letters, numbers, spaces, `- ( ) ' " .` only). The bot trims whitespace before running.
- Checks & Limits: role requirement, cooldown, and single-upload lock. Invalid names are rejected before any scripts run.
- Output: background task reports "Downloading/Processing/Downloaded" states and ends with counts for images downloaded, uploaded, duplicated, failed, plus total URLs scanned. Wiki errors are echoed back in a code block.

**/statusupload**
Usage: `/statusupload status_id:<1438|status_1438|status_1438#> max_index:<1-100 (defaults 10)>`
- Purpose: Upload small/large status effect icons in bulk.
- Inputs:
  - `status_id` - accept raw numeric IDs, prefixed IDs (`status_1438`), or add a trailing `#` to iterate sequential IDs. When `#` is present the command walks up to `max_index` consecutive identifiers.
  - `max_index` - only used when `status_id` ends with `#`; choose 1-100 (default 10) to define the upper bound.
- Checks & Limits: same role/cooldown/lock rules; IDs must be alphanumeric/underscore with an optional trailing `#`.
- Output: progress callouts show which icon number is active. The summary lists processed/uploaded/failed counts and wiki links for every file created; embed previews are auto-suppressed to keep the post tidy.

**/bannerupload**
Usage: `/bannerupload banner_id:<campaign id> max_index:<1-50 (defaults 12)>`
- Purpose: Upload rotating gacha banner variants by hitting `banner_<id>_<index>.jpg` on the CDN until an index fails.
- Inputs:
  - `banner_id` - the part between `banner_` and the trailing index (letters/numbers/underscores only). You may paste a full `banner_<id>` string; the command strips `banner_` automatically.
  - `max_index` - highest numeric suffix to try, 1-50 with a default of 12.
- Checks & Limits: role/cooldown/lock apply; invalid IDs are rejected up front.
- Output: shows which banner slug/index it is processing, then reports processed/uploaded/failed counts and wiki links for every successful upload.

**/itemupload**
Usage: `/itemupload item_type:<Article|Normal|Recycling|Skillplus|Evolution|Npcaugment> item_id:<CDN id> item_name:<Display Name>`
- Purpose: Upload the square/icon pair for a single item along with canonical redirects for the supplied display name.
- Inputs:
  - `item_type` - choose which CDN subfolder to query (`article`, `normal`, `recycling`, `skillplus`, `evolution`, `npcaugment`).
  - `item_id` - path fragment straight from the asset URL (1-48 chars; letters, numbers, `_`, `-` only).
  - `item_name` - wiki-facing name used for redirect files (same validation as page names).
- Checks & Limits: role/cooldown/lock plus validation for every field before the upload worker starts.
- Output: progress mentions current variant, then the summary lists variants processed, uploads, duplicates, total URLs checked, and direct wiki links for canonical/redirect targets (`Item_<type>_s/m_<id>`, `<Name> square/icon`).

**/synccommands**
Usage: `/synccommands`
- Purpose: Force-register all slash commands when Discord falls out of sync.
- Requirements: Must be run in a server by an administrator (the bot rejects DMs and non-admin roles). No cooldown/lock applies.
- Output: replies ephemerally with whether the sync happened at the guild or global scope, total commands now registered, and the previous error if it had to fall back to a global sync.
