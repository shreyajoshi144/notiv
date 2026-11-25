# Notiv — Go-live checklist

Release readiness for the public demo on Railway, launched **2026-11-22**. Each item links to its evidence. The
reusable version for future releases is at the end.

## 1. Before deploying

| ✓ | Item | Evidence |
|---|---|---|
| ✅ | All automated tests pass (636 passed, 2 skipped) | [Results §1](../RESULTS_AND_TESTING.md#1-scoreboard) |
| ✅ | Browser end-to-end passes (18 / 18) | [Results §6](../RESULTS_AND_TESTING.md#6-browser-end-to-end) |
| ✅ | Docker image passes the deployment smoke test behind an HTTPS proxy (41 / 41) | [Results §6b](../RESULTS_AND_TESTING.md#6b-docker-deployment-smoke-test) |
| ✅ | Pre-release audit: unused code removed, env files excluded from the image | [Changelog 2026-11-17](../CHANGELOG.md) |
| ✅ | Release merged to `main` through a pull request (PR #2) | git `112ee00` |
| ✅ | Secrets stored only as platform variables, never in the repository or the image | [Deployment §3](../DEPLOYMENT.md#3-environment-single-server) |
| ✅ | Known gaps written down before launch | [QA report §6](QA_TEST_REPORT.md#6-not-verified) |

## 2. Deploying

| ✓ | Item | Note |
|---|---|---|
| ✅ | Service built from the GitHub repository's `Dockerfile` | `VOLUME` line removed first (`3288aeb`) |
| ✅ | Persistent volume attached at `/data` | accounts, uploads and the speech model survive redeploys |
| ✅ | `PORT=8080` and the public domain pointed at 8080 | a mismatch caused 502 errors at first |
| ✅ | Production variables set and **deployed** | single-instance overrides, AI key, `https://` public URL and CORS origin |
| ✅ | Speech model sized to the volume (`NOTIV_WHISPER_MODEL=base`) | the larger model's cache filled the disk |

## 3. After deploying

| ✓ | Item | Result |
|---|---|---|
| ✅ | `/readyz` reports ready | ok |
| ✅ | Accounts, security, PDF / PPTX with live AI | 29 / 29 |
| ✅ | MP3 / MP4 with live AI | 25 / 25 |
| ✅ | Pages, notebook PDF download, course delete, password-reset response | 22 / 22 |
| ✅ | Another account gets 404 | included above |
| ⬜ | Email delivery for password reset | not configured (B-05) |
| ⬜ | Metrics and alerts | not enabled (B-13) |
| ⬜ | Automatic backups | none yet (B-06) |

## 4. Rollback plan

Railway keeps previous deployments: redeploy the last good one from the dashboard. Data is on the volume, so a rollback
does not lose accounts or uploads. There is no automatic backup of the volume yet, so a damaged volume is not
recoverable today: that is backlog item B-06 and risk R6.

## 5. Reusable checklist

```text
[ ] pytest core passes                      [ ] browser e2e passes
[ ] format matrix + offline AI scorecard     [ ] Docker smoke test (--as-proxy) passes
[ ] merged to main through a PR              [ ] variables set AND deployed; secrets not in the repo or image
[ ] volume at /data; port and domain match   [ ] /readyz ok
[ ] smoke_deploy.py + smoke_live_pages.py against the live URL
[ ] results and any new gaps logged in docs/RESULTS_AND_TESTING.md
```
