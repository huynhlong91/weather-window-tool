# Versioning & Deployment Guide

**Internal document — not part of the client deliverable.**

This describes how to run a stable version and a current version side by side, so EirGrid can compare outputs and we keep a rollback point.

The end state is two URLs:

| | Branch | Streamlit app | Purpose |
|---|---|---|---|
| **Stable** | `v1.0` | app-v1-0 | Frozen. What the client has already reviewed. Never changes. |
| **Current** | `main` | app (existing) | Active development. Redeploys on every push. |

---

## One-time setup

### Step 1 — Freeze the current deployed state as `v1.0`

Do this **before** pushing any v1.1 files, so the branch captures what EirGrid has actually been using.

```bash
git checkout main
git pull
git checkout -b v1.0
git push -u origin v1.0
git checkout main
```

The `v1.0` branch now holds the reviewed state permanently. Leave it alone from here.

### Step 2 — Tag it

```bash
git tag -a v1.0 -m "v1.0 — initial release reviewed by EirGrid"
git push origin v1.0
```

A tag is a permanent, dated marker. Unlike a branch it cannot drift, so it is the authoritative record of exactly what the client had on a given date.

### Step 3 — Deploy the frozen version as a second app

1. Go to [share.streamlit.io](https://share.streamlit.io) → **New app**
2. Repository: the same repo
3. **Branch: `v1.0`** ← the only field that differs from the existing app
4. Main file path: `app.py`
5. Under **Advanced settings**, set a custom subdomain such as `wwt-v1-0`
6. Deploy

Add the same `[users]` secrets block to this app so the login works — secrets are per-app, not per-repo.

### Step 4 — Publish v1.1

```bash
git checkout main
# copy in the new app.py, engine.py, README.md
git add .
git commit -m "v1.1 — monthly operability matrix, Min Window sync, 1M iterations"
git push
git tag -a v1.1 -m "v1.1 — monthly operability matrix"
git push origin v1.1
```

The existing app redeploys automatically within a minute or two.

### Step 5 — Write the release notes

On GitHub: **Releases → Draft a new release → choose tag `v1.1`**. Paste the v1.1 section from the README's Version History. This gives a dated, readable record against the code itself.

---

## Routine use afterwards

**Releasing v1.2:**

```bash
git checkout main
# make changes
git commit -am "v1.2 — <summary>"
git push
git tag -a v1.2 -m "v1.2 — <summary>"
git push origin v1.2
```

Then add the changes to the Version History section of `README.md` so they appear in the app's Instructions tab.

**Rolling back a bad release:**

```bash
git revert <commit-hash>
git push
```

Or, in Streamlit Cloud, repoint the app's branch to the last good tag while you investigate.

**Retiring an old version:** once the client confirms they are happy with the new version, delete the frozen app from Streamlit Cloud to free a slot. The branch and tag stay in the repo regardless, so nothing is lost.

---

## Notes

- Keep only two live apps. A third frozen version is rarely useful and consumes quota.
- Secrets are configured per app. A newly deployed app has none until you add them.
- Both apps read `README.md` from their own branch, so the frozen app correctly shows the v1.0 documentation and the current app shows v1.1.
- `secrets_example.toml` is safe to keep in the repo. Never commit a real `.streamlit/secrets.toml` — add it to `.gitignore`.
