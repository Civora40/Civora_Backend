# Phase 6 — WhatsApp and Telegram External Channel Integrations

## 1. Executive Overview & Architecture

Phase 6 provides Civora with a provider-independent, zero-trust external messaging gateway. It allows field engineers, supervisors, laboratory technicians, and site operatives to submit observations, test results, daily logs, and evidence media directly from **WhatsApp Business Platform (Cloud API)** and **Telegram Field Bots** into Civora's core engineering ledger.

```
+─────────────────────────────────────────────────────────────────────────────+
│                            FIELD MESSAGING CHANNELS                         │
│                                                                             │
│   ┌────────────────────────────────────┐ ┌────────────────────────────────┐ │
│   │   WhatsApp Business Cloud API      │ │       Telegram Bot API         │ │
│   │  - Meta Graph API v21.0            │ │  - Telegram Bot Update webhook │ │
│   │  - HMAC-SHA256 signature check     │ │  - Secret-Token header check   │ │
│   └─────────────────┬──────────────────┘ └────────────────┬───────────────┘ │
+─────────────────────┼─────────────────────────────────────┼─────────────────+
                      │ HTTPS POST                          │ HTTPS POST
                      ▼                                     ▼
+─────────────────────────────────────────────────────────────────────────────+
│                        CIVORA INGESTION GATEWAY                             │
│                                                                             │
│   1. Cryptographic Handshake & Signature Validation                         │
│      - Meta hub.challenge GET / X-Hub-Signature-256 POST                    │
│      - Telegram X-Telegram-Bot-Api-Secret-Token POST validation             │
│                                                                             │
│   2. Idempotency & Raw Event Storage                                        │
│      - Check (channel, provider_message_id) DB uniqueness                   │
│      - Duplicate -> log duplicate_blocked & return HTTP 200 ACK             │
│      - New -> Persist immutable InboundWebhookEvent audit trail             │
│                                                                             │
│   3. Project Binding & Governance Verification                              │
│      - Resolve Project via ChannelBinding (chat_id / phone mapping)         │
│      - Fallback: parse hashtags (e.g., #LRMT-01) or route to triage         │
│      - Governance Gate: Project must be active & Platform Admin approved    │
│                                                                             │
│   4. Sender Identity Resolution                                             │
│      - Phone normalization to E.164 (+234...) / Telegram @handle matching   │
│      - Matched: attribute submitted_by User, mark 'verified'                │
│      - Unmapped: retain submitter_identifier, mark 'needs_verification'     │
│                                                                             │
│   5. Media Streaming & SHA-256 Evidence Storage                             │
│      - Stream binary from Meta Graph / Telegram CDN into private storage    │
│      - Compute cryptographic SHA-256 digest & validate MIME safety          │
│      - Create SubmissionAttachment linked to parent Submission              │
│                                                                             │
│   6. Field Submission Generation & Heuristic Advisory                       │
│      - Create Submission in 'pending_review' status                         │
│      - Classify category (Testing, Materials, Safety, Progress)             │
│                                                                             │
│   7. Telemetry & Dead-Letter Queue (DLQ)                                    │
│      - Monitor channel operational status, 24h volume, error rates          │
│      - Admin DLQ retry mechanics for unmapped senders or failed downloads   │
+─────────────────────────────────────────────────────────────────────────────+
```

---

## 2. Critical Provider Capability Analysis

In accordance with Section 7 of the Civora Implementation Roadmap, group and conversation capabilities differ fundamentally between providers:

1. **Meta WhatsApp Business Cloud API:**
   * **Scope & Restrictions:** The standard Meta Cloud API is fundamentally oriented towards 1-to-1 interactions between an external customer/operative and an official WhatsApp Business Account (WABA) phone number. Standard WhatsApp consumer groups are **not** accessible via the Cloud API without specialized on-premise Business Solution Providers (BSPs) or official co-existence partners.
   * **Civora Architectural Pattern:** Field personnel submit directly to the organization's verified WhatsApp business number (or interact via prompt-response bots). Messages are attributed via the sender's E.164 phone number.
2. **Telegram Bot API:**
   * **Group Capabilities:** Telegram bots can be added to project groups and channels. By disabling privacy mode (`/setprivacy -> Disable` in BotFather), the bot receives all messages in the group, extracting the `chat_id` and routing submissions to that group's bound Project and Department.
3. **Provider-Independent Abstraction:**
   * Business logic never couples to raw provider structures. All payloads are normalized through `BaseMessagingAdapter` into `NormalizedInboundMessage` objects before ingestion.

---

## 3. Data Modeling & Database Schema

The integrations subsystem introduces four database models in `apps/integrations/models.py`:

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                            InboundWebhookEvent                              │
│  - id: UUID (PK)                                                            │
│  - channel: 'whatsapp' | 'telegram'                                         │
│  - provider_event_id: string (db_index)                                     │
│  - provider_message_id: string (db_index)                                   │
│  - sender_raw: string (E.164 phone or Telegram handle)                      │
│  - external_chat_id: string (group/chat identifier)                         │
│  - raw_payload: JSONField (immutable audit trail)                           │
│  - status: 'received' | 'processed' | 'duplicate_blocked' |                 │
│            'needs_verification' | 'unresolved_project' | 'failed'           │
│  - error_message: text                                                      │
│  - organization: FK(Organization, null=True)                                │
│  - project: FK(Project, null=True)                                          │
│  - submission: FK(Submission, null=True)                                    │
│  - processed_at: datetime                                                   │
│  - created_at, updated_at: datetime                                         │
│  [UniqueConstraint: (channel, provider_message_id)]                         │
└──────────────────────────────────────┬──────────────────────────────────────┘
                                       │ 0..1
                                       ▼
┌─────────────────────────────────────────────────────────────────────────────┐
│                                Submission                                   │
│  - id, reference_code, organization, project, status, channel               │
│  - submitter_identifier, submitted_by, raw_text, attachments                │
└─────────────────────────────────────────────────────────────────────────────┘

┌──────────────────────────────────────┐  ┌───────────────────────────────────┐
│            ChannelBinding            │  │       VerifiedContactMethod       │
│  - id: UUID (PK)                     │  │  - id: UUID (PK)                  │
│  - organization: FK(Organization)    │  │  - user: FK(User)                 │
│  - project: FK(Project)              │  │  - contact_type: 'whatsapp' |     │
│  - department: FK(Dept, null=True)   │  │                  'telegram' |     │
│  - channel: 'whatsapp' | 'telegram'  │  │                  'phone' | 'email'│
│  - external_identifier: string       │  │  - identifier: string (E.164)     │
│  - label: string                     │  │  - is_active: boolean             │
│  - is_active: boolean                │  │  - is_primary: boolean            │
│  - config: JSONField                 │  │  - verified_at: datetime          │
│  [Unique: (channel, ext_identifier)] │  │  [Unique: (type, identifier)]     │
└──────────────────────────────────────┘  └───────────────────────────────────┘

┌─────────────────────────────────────────────────────────────────────────────┐
│                                IngestionRun                                 │
│  - id: UUID (PK)                                                            │
│  - batch_reference: string (unique, e.g. BATCH-WA-20261122-001)             │
│  - channel: 'whatsapp' | 'telegram'                                         │
│  - status: 'completed' | 'in_progress' | 'failed' | 'partial_failure'       │
│  - total_messages_ingested, duplicates_blocked, unresolved_senders          │
│  - media_processing_failures, retry_attempts: integer                       │
│  - duration_ms: integer                                                     │
│  - error_log_snippet: text                                                  │
│  - started_at, completed_at: datetime                                       │
└─────────────────────────────────────────────────────────────────────────────┘
```

---

## 4. Cryptographic Security & Webhook Endpoints

### 4.1. WhatsApp Business Platform
* **GET `/api/v1/integrations/webhooks/whatsapp/` (Challenge Verification):**
  * Verifies `hub.mode == 'subscribe'` and `hub.verify_token == settings.WHATSAPP_WEBHOOK_VERIFY_TOKEN`.
  * Returns `hub.challenge` as plain text (HTTP 200). Invalid token returns HTTP 403 Forbidden.
* **POST `/api/v1/integrations/webhooks/whatsapp/` (Message Ingestion):**
  * Reads header `X-Hub-Signature-256: sha256=<signature>`.
  * Computes `hmac.new(key=WHATSAPP_APP_SECRET, msg=raw_body, digestmod=sha256).hexdigest()`.
  * Uses `hmac.compare_digest` to prevent timing attacks. Tampered or unsigned requests return HTTP 403 Forbidden.

### 4.2. Telegram API & MTProto Gateway
* **Credentials & Configuration:**
  * `TELEGRAM_APP_API_ID`: `30212373`
  * `TELEGRAM_APP_API_HASH`: `89fffb75ebbd496fef7da246bfbb849e`
  * `TELEGRAM_DC_PROD_IP`: `149.154.167.50:443` (Data Center 2 Production)
  * `TELEGRAM_DC_TEST_IP`: `149.154.167.40:443` (Data Center 2 Test Configuration)
  * RSA 2048-bit Public Keys for production and test DC transport security.
* **POST `/api/v1/integrations/webhooks/telegram/` (Update Ingestion):**
  * Reads header `X-Telegram-Bot-Api-Secret-Token`.
  * Validates constant-time match against `settings.TELEGRAM_WEBHOOK_SECRET_TOKEN`.
  * Rejects mismatched requests with HTTP 403 Forbidden.
* **GET `/api/v1/integrations/telegram/config/`:**
  * Returns sanitized Telegram App API configuration (App API ID, masked API Hash, active DC endpoint, and RSA key status).
* **POST `/api/v1/integrations/telegram/test-connectivity/`:**
  * Performs an active TCP socket probe to the Telegram Data Center and measures network latency.

### 4.3. Simulation Endpoint (Testing & CI/CD)
* **POST `/api/v1/integrations/simulate/`:**
  * Protected by Civora staff authentication (`IsTenantMember` or dedicated admin).
  * Accepts direct normalized payload matching frontend integration test runner.

---

## 5. Ingestion Pipeline & Execution Logic

1. **Idempotency Guard:**
   * Queries `InboundWebhookEvent.objects.filter(channel=channel, provider_message_id=msg_id).exists()`.
   * If present: Logs event with `status='duplicate_blocked'`, returns HTTP 200 `{ "status": "duplicate_blocked" }`. Prevents duplicate DB records while satisfying webhook provider ACK requirements.
2. **Project & Department Resolution:**
   * Matches `ChannelBinding` on `(channel, external_chat_id)`.
   * Fallback: Parses text for explicit project tags (e.g., `#LCR-01`, `[LRMT-01]`).
   * Fallback: Resolves sender's assigned project via `User.assigned_projects`.
   * Unresolved: Flags event as `unresolved_project` for admin routing.
3. **Project Governance Gate:**
   * Invokes `ensure_project_allows_field_submissions(project)`.
   * If project is in `draft` or `pending_approval` without Platform Admin approval, blocks ingestion with code `project_approval_required`.
4. **Sender Identity Resolution:**
   * Cleans phone number with E.164 normalization (e.g. `+2348031234567`).
   * Queries `VerifiedContactMethod`.
   * If found: Sets `submitted_by = user`, `senderVerificationStatus = 'verified'`.
   * If unmapped: Sets `submitter_identifier = raw_sender`, `senderVerificationStatus = 'needs_verification'`. Data is **never dropped**.
5. **Media Streaming & Evidence Ingestion:**
   * Streams binary media from Meta Cloud API (`/v21.0/{media_id}`) or Telegram (`/file/bot{token}/{path}`).
   * Calls `apps.evidence.services.save_evidence_file` to compute SHA-256 and store under private storage key.
6. **Heuristic AI Advisory Classification:**
   * Analyzes message text for civil engineering domain patterns (break test, slump, rebar, heat, safety, inclinometer).
   * Generates compliance flag (`spec_compliant`, `advisory_discrepancy`, `unverified`).

---

## 6. Admin Telemetry & DLQ Monitoring Endpoints

* `GET /api/v1/integrations/telemetry/`: Real-time operational status, 24h processed count, blocked duplicates count, and error rate.
* `GET /api/v1/integrations/runs/`: Paginated batch ingestion runs and failure counts.
* `POST /api/v1/integrations/runs/<id>/retry/`: Re-triggers failed media downloads or re-evaluates unmapped senders against updated verified contacts.
* `GET /api/v1/integrations/bindings/`: Manage chat group and channel bindings.
* `GET /api/v1/integrations/contacts/`: Manage verified phone numbers and Telegram handles.
