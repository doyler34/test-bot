# OYB bot — deployment & sync notes

## What the user wants — read this first, every session
Then read HANDOVER.md: where everything is and what state it's in.

- **You never touch anything live. You give the command; the user runs it.**
  Live means the panel (`/root/oyb-panel`, service `oyb-panel`), the friend's
  bot (`~/Arma-bot`), the game server and AMP. Don't `git pull`, edit, restart
  or stop any of them, not even "just the code". Reading (status, logs,
  `git log`) is fine.
- **Never push to Gazlagom.** The user runs the sync themselves, with their
  token.
- **Never store a token or password**, for any repo: no credential helper, no
  token in a remote URL, no stored-token script.
- **Never install anything** (packages, browsers, pip modules) without asking.
- **When you ask "want me to?", stop and wait for the answer.** Don't carry on
  and do it in the same turn.
- **Only do what was asked.** Mention anything else you notice in one line;
  don't fix it unasked.
- **Keep it short and plain.** The user is on a phone. Give one step at a
  time and one single-line command per step. No walls of text.
- **Say what's proven and what isn't.** Don't call anything tested or working
  unless you saw it work. Mod code can only be compiled and tested by the user
  in Workbench.

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
- **HTTPS and a token. Never SSH, never a deploy key, never a credential
  helper.** The remote stays `https://github.com/Gazlagom/Arma-bot.git`. Do not
  offer an alternative auth method when a push fails — a 403 means the token
  needs Contents: Read/Write on Arma-bot, and the fix is a token, nothing else.
  If the remote has been switched to SSH, put it back:
  `git remote set-url origin https://github.com/Gazlagom/Arma-bot.git`
- "nothing to commit" means Gazlagom is already current.
- Give it to the user as one line, with a commit message describing what
  actually changed since the last sync:
  `cd /root/Arma-bot-upload.StfK1D && git fetch origin && git reset --hard origin/main && git -C /root/test-bot pull --ff-only origin main && git -C /root/test-bot archive HEAD | tar -x -C . && grep -rIlE --exclude-dir=.git 'doyler34|test-bot' . | xargs -r sed -i 's#doyler34/test-bot#Gazlagom/Arma-bot#g; s#doyler34#Gazlagom#g; s#test-bot#Arma-bot#g' && git add -A && git -c user.name='Gazlagom' -c user.email='327911930+Gazlagom@users.noreply.github.com' commit -m '<describe the changes>' && git push origin HEAD`

## After syncing — the user updates what's live
- Friend's bot: `cd ~/Arma-bot && git pull && oyb restart`
- Panel: `/root/oyb-panel` is on branch `panel-test` with no upstream, so a
  plain `git pull` there does nothing. Give the user:
  `cd /root/oyb-panel && git pull --ff-only origin main && systemctl restart oyb-panel`
- Nothing in `/root/test-bot` ever needs restarting.

## Live bot paths
- `/root/test-bot` on the VPS is a plain checkout used to feed the Gazlagom
  sync. No bot runs from it — nothing to restart, no live data there.
- Friend's bot: `~/Arma-bot` (data at `~/Arma-bot/data`). This is production.

## House rules
- The user is on a mobile terminal: give single-line commands, no multi-line
  `for/do/done` loops and no shell wildcards that the paste can mangle.
- Do not add AI-looking or filler comments; write code like a human.
- Do not propose or make changes the user did not ask for.
