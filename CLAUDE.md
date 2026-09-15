# OYB bot — deployment & sync notes

## Repos
- `doyler34/test-bot` — the real repo. All dev happens here; branch work is
  fast-forwarded onto `main`.
- `Gazlagom/Arma-bot` — a throwaway mirror so friends don't see the real
  account or any AI involvement. It is a scrubbed copy of `main` (doyler34
  refs rewritten to Gazlagom).

## The Gazlagom sync — THE canonical command
Run on the VPS (`srv1669502` / bos2.hostingervps.com). The mirror lives at
`/root/Arma-bot-upload.StfK1D` — NOT `~/Arma-bot` (that path is the friend's
box). Never send the user anywhere else.

```
cd /root/Arma-bot-upload.StfK1D
git fetch origin && git reset --hard origin/main
git -C /root/test-bot pull --ff-only origin main
git -C /root/test-bot archive HEAD | tar -x -C .
grep -rIlE --exclude-dir=.git 'doyler34|test-bot' . | xargs -r sed -i 's#doyler34/test-bot#Gazlagom/Arma-bot#g; s#doyler34#Gazlagom#g; s#test-bot#Arma-bot#g'
git add -A
git -c user.name='Gazlagom' -c user.email='327911930+Gazlagom@users.noreply.github.com' commit -m '<describe the changes>'
git push origin HEAD
```

- `origin` in the upload folder points at Gazlagom.
- The scrub must catch the bare repo name too: docs and `dev/` scripts say
  `cd /root/test-bot` without any `doyler34` next to it, so a grep for
  `doyler34` alone never even opens those files. Verify a change to the scrub
  by exporting `git archive HEAD` to a temp dir, running it, then grepping the
  result for `doyler|test-bot|claude|anthropic` — all four must come back empty.
- The push may prompt for a token (username `Gazlagom`, password = a
  fine-grained token with Contents: Read/Write on Arma-bot). The user is fine
  entering it each time — do not push a stored-token script unless they ask.
- "nothing to commit" means Gazlagom is already current.

## After syncing — restart the bot
- Friend's box, the only one running a live bot: `cd ~/Arma-bot && git pull && oyb restart`
- Do NOT tell the user to restart anything on the VPS.

## Live bot paths
- `/root/test-bot` on the VPS is a plain checkout used to feed the Gazlagom
  sync. No bot runs from it — nothing to restart, no live data there.
- Friend's bot: `~/Arma-bot` (data at `~/Arma-bot/data`). This is production.

## House rules
- The user is on a mobile terminal: give single-line commands, no multi-line
  `for/do/done` loops and no shell wildcards that the paste can mangle.
- Do not add AI-looking or filler comments; write code like a human.
- Do not propose or make changes the user did not ask for.
