# Maintaining the-long-fork

## How the tracker runs

`.github/workflows/tracker.yml` runs `tools/tracker.py` twice a day (03:17 and
15:17 UTC) and on demand from the Actions tab. Every job in it is guarded with
`if: github.repository == 'Londopy/the-long-fork'`, so the copy each fork
inherits never runs.

Each run:

1. walks the fork network through the REST API, forks of forks, with every
   response ETag-cached (an unchanged listing costs nothing against the limit);
2. fetches every repo in the network into one bare git repo kept in the Actions
   cache (git fetches don't count against the API rate limit);
3. checks every fork against the fork it was made from, builds the tree and
   picks the tip;
4. writes `STATUS.txt`, `tree.txt`, `badge.json`, `CANVAS.txt`,
   `docs/data.json` and the marked blocks in `README.md`;
5. commits only when something changed, plus one heartbeat a day so "last
   checked" stays true;
6. makes any milestone release that's due and announces a new tip.

`docs/data.json` is also the tracker's memory. A link it has seen stays in the
tree as LOST if its fork disappears, and the links after it keep counting.
Don't delete that file.

## Settings

All optional. Variables and secrets live under Settings, then Secrets and
variables, then Actions.

| Name | Kind | Effect |
| --- | --- | --- |
| `ANNOUNCE_DISCUSSION` | variable | Number of the pinned discussion to comment on when there's a new tip. The `@mention` notifies the new tip's owner. |
| `DISCORD_WEBHOOK` | secret | Discord webhook URL for the same announcement. |
| `MASTODON_INSTANCE` | variable | Your instance, for example `https://mastodon.social`. |
| `MASTODON_TOKEN` | secret | Access token with the `write:statuses` scope. |
| `STALL_DAYS` | variable | Days without a new link before CHAIN STALLED shows. Default 7. |
| `TRACKER_TOKEN` | secret | A fine-grained token with public read access, used only for the network walk. Raises the rate limit from 1,000 to 5,000 requests an hour. |

## Moderation

Add `<username> | <reason>` to `EXCLUDED.txt`. That link stops counting, and
the links after it still count. Notes with URLs are rejected automatically.

## Local commands

```bash
python -m unittest discover -s tests          # the test suite
python tests/demo.py /tmp/demo                # made-up network, full outputs
python -m http.server -d /tmp/demo/docs 8000  # preview the site with it
GITHUB_TOKEN=$(gh auth token) python tools/tracker.py --dry-run --cache /tmp/lf-cache
```

The last one walks the real network and prints what the tracker would write,
without writing anything.

## Rate limits

A walk costs one request per repo that has forks, and nothing for listings
that haven't changed. If a walk does run out of requests, the tracker keeps
the last status and resumes on the next run, with its caches still warm.

Why the walk isn't GraphQL: the tracker reads every file and diff through git,
so the API is only used to list forks. REST listings are ETag-cached and a 304
is free, while GraphQL queries can't be conditional and always cost points. A
network shaped like a chain also gains little from nested GraphQL queries,
since each level still depends on the one before. If the network ever becomes
wide and bushy enough that cold walks hurt, a `TRACKER_TOKEN` is the cheaper
fix, and the walk is isolated in `walk()` in `tools/longfork/github.py`.

## Announcements discussion

The tracker comments on the discussion whose number is in `ANNOUNCE_DISCUSSION`.
Pin that discussion in the Discussions tab (the API can't pin), so newcomers
see the latest tip first.

## Renaming

The root repo's name is in `tools/longfork/config.py`, in the `if:` guards of
the root-only workflows (a test checks that they match), and in links in
`README.md` and `docs/`. The root line of `CHAIN.txt` can't change once anyone
has forked.
