# Ground-truth fork files (test fixtures)

Files here are **extracted, not authored**. Each is a real file from the
Proton-patched Chatwoot tree, pulled out by applying the patch stack to the
pinned upstream image inside a throwaway container:

```sh
docker run --rm --user root -v <patches>:/tmp/pp:ro chatwoot/chatwoot:v4.15.1 sh -c '
  cd /app && git init -q . && git add -A
  git -c user.email=a@b -c user.name=c commit -qm base
  for p in /tmp/pp/0*.patch; do
    # stop BEFORE the patch this fixture is the pre-image for
    case "$(basename $p)" in 005[5-9]*|006*) break;; esac
    git apply --whitespace=fix "$p" &&
      git add -A && git -c user.email=a@b -c user.name=c commit -qm "$p"; done
  cat app/javascript/dashboard/components/widgets/WootWriter/ReplyTopPanel.vue'
```

The `break` matters: applying the *whole* stack and then `cat`-ing the file
gives the post-`0060` file, not the post-`0054` pre-image `0055` needs.
`--user root` is what lets `git init` write inside `/app`.

They exist so a patch test can apply a patch to the **real** pre-image rather
than to a hand-reconstructed stand-in. Reconstructions are how patches 0055
and 0056 came to fail on the first real Cloud Build: both assumed a `useAlert`
import at the top of `ReplyTopPanel.vue` that does not exist, and the assumed
line 14 (`useStore`) is really `useUISettings`. A fixture cannot make that
class of mistake.

Nothing in this directory is copied into the image — the Dockerfile only
`COPY patches/`.

| File | What it is |
|---|---|
| `ReplyTopPanel.post-0054.vue` | `app/javascript/dashboard/components/widgets/WootWriter/ReplyTopPanel.vue` as it exists after patches `0001`–`0054` apply to `chatwoot/chatwoot:v4.15.1`. The pre-image for `0055`, and (via `0055`) for `0056`. |

## Refreshing a fixture

Re-extract it whenever an earlier patch starts touching the same file, or the
pinned upstream version in `UPSTREAM_VERSION` changes.

**The "a stale fixture fails loudly" claim this section used to make is not
true, and 2026-08-11 proved it.** Patch `0002` changed its `ReplyTopPanel.vue`
mapping (structured assist messages, +13 lines), which shifts every line below
it. Both `0055` and `0056` still applied cleanly to the *unrefreshed* fixture —
`git apply` matches on context, not line numbers, and neither patch's hunks
overlap the changed region. The stale fixture passed silently. Treat "an
earlier patch touched this file" as the trigger to refresh; do not wait for a
test to tell you.

### The 2026-08-11 refresh was a delta — and it was later re-extracted and confirmed

When the refresh was first done no upstream image was reachable, so
`ReplyTopPanel.post-0054.vue` was updated by replacing the three-line
`const messages = …` mapping with the sixteen lines patch `0002` now emits,
extracted programmatically from the patch file itself.

That is safe here for one specific reason, and the reason matters: those lines
are `0002`'s **own added output**, not upstream content. The failure this
directory exists to prevent is *guessing what upstream looks like* — which is
how `0055` and `0056` came to assert a `useAlert` import that was never there.
A delta confined to lines our own patch authored cannot make that mistake.

Applying a delta to any region NOT authored by our patches is exactly the
reconstruction anti-pattern. Re-extract instead.

**Loop closed the same day.** Once `chatwoot/chatwoot:v4.15.1` was reachable
again the file was re-extracted with the command at the top of this README
(patches `0001`–`0054`, committing after each) and diffed against the
delta-refreshed fixture: **byte-identical**, 279 lines. The delta was correct,
but that is a result, not a licence — the next refresh still starts with a
re-extraction.
