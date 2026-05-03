## Plan A: Add Email Notifications

**Goal:** Send email notifications when tasks are assigned.

**Architecture Summary:** Reads notification preferences from `src/config.py` (SMTP settings). Adds email sending to the task assignment workflow.

**Files:**
| File | Purpose |
|------|---------|
| `src/notifications/email.py` | Email sending logic |
| `src/tasks/assignment.py` | Modified — adds notification trigger |
| `tests/test_email.py` | Email tests |

**Tasks:**
1. Create email.py with SMTP integration — reads SMTP_HOST, SMTP_PORT from `src/config.py` (Dependencies: none)
2. Add notification trigger to assignment.py (Dependencies: Task 1)
3. Write tests (Dependencies: Task 1, 2)

---

## Plan B: Add Slack Notifications

**Goal:** Send Slack notifications when deadlines are approaching.

**Architecture Summary:** Reads Slack webhook URL from `src/config.py`. Adds deadline check cron job that sends Slack messages.

**Files:**
| File | Purpose |
|------|---------|
| `src/notifications/slack.py` | Slack webhook client |
| `src/tasks/deadline_checker.py` | Cron job checking upcoming deadlines |
| `tests/test_slack.py` | Slack notification tests |

**Tasks:**
1. Create slack.py with webhook client — reads SLACK_WEBHOOK_URL from `src/config.py` (Dependencies: none)
2. Create deadline_checker.py cron job (Dependencies: Task 1)
3. Write tests (Dependencies: Task 1, 2)

---

## Project Context

Both plans reference `src/config.py` but only for **reading** configuration values. Neither plan modifies `src/config.py` — the SMTP and Slack settings are assumed to already be environment variables loaded by the existing config module. The plans have zero file overlap in their write sets.

New environment variables (SMTP_HOST, SMTP_PORT, SLACK_WEBHOOK_URL) are added to `.env.example` by each plan independently — these are additive, non-conflicting changes to different lines of the same file.
