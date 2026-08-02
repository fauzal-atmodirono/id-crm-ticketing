# Native SAML SSO Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Ship native, license-clean SAML SSO for the Chatwoot fork — per-account IdP
configuration, JIT user provisioning, and an optional SSO-enforcement toggle —
wrapped in an extensible "Security" settings section, replacing the dead
Enterprise SAML paywall.

**Architecture:** Login/callback flow and settings storage live in Chatwoot's
own Rails app (new migration, new controllers, no `enterprise/` code touched);
the settings page's nav/visibility reuses the existing RBAC `/authz` system
(backend `security.manage` permission) the same way the SLA/Audit/Roles pages
already do. Three phases, each independently shippable: (1) core login
capability with no UI, (2) the settings UI + un-hiding the nav, (3) the
enforce-SSO toggle.

**Tech Stack:** Ruby on Rails (Chatwoot fork), `omniauth-saml`/`ruby-saml`
(already in `Gemfile.lock` — no new gem needed), Devise Token Auth, Pundit,
Vue 3 (fork SPA), the existing Proton `backend/` FastAPI RBAC service
(`chatbot.features.authz`).

**Companion docs:** `docs/superpowers/specs/2026-08-02-native-saml-sso-security-design.md`
(design spec — read first for the *why*). Supersedes/extends
`deploy/chatwoot-fork/patches/0032-hide-security-settings-nav.patch`.

## Global Constraints

- Never modify anything under Chatwoot's `enterprise/` directory (licensing —
  see design spec Problem section). All new code is original, outside that tree.
- SP-initiated login only — no IdP-initiated flow.
- The account context for a SAML login is carried **only** via SAML
  `RelayState`, never a trusted client-supplied param on the callback route —
  this is what prevents a crafted request from signing a user into the wrong
  tenant's account.
- JIT-provisioned users default to the `agent` role unless `role_mapping`
  explicitly resolves to `administrator` for the asserted attribute value.
- `enforce_sso` break-glass is ops-only (Rails console on the VM), never an
  in-app UI bypass.
- All gem dependencies used must be MIT-licensed; `omniauth-saml` (2.2.4) /
  `ruby-saml` (1.18.1) already satisfy this and are already vendored — verify
  no unpatched CVE affects 1.18.1 before Task 5's manual IdP test (check
  https://github.com/advisories for `ruby-saml`; if a newer patched version
  exists, bump it as part of Task 1 instead of leaving it pinned).
- Every new/modified file under `app/`, `config/`, or `db/migrate/` in the
  Chatwoot fork **must** also be added to the Dockerfile's runtime-stage COPY
  list (Task 1) — the runtime image is pristine Chatwoot plus only
  `public/vite` and one explicit file today; anything else silently has zero
  effect at runtime. This is the single most important trap in this plan.
- `permission_key` strings in the backend RBAC registry follow the existing
  `<domain>.manage` convention (`sla.manage`, `roles.manage` → this plan adds
  `security.manage`).
- Follow existing patterns exactly: `Api::V1::Accounts::BaseController` +
  `check_authorization` + `ApplicationPolicy` for admin-only Rails endpoints
  (mirrors `WebhooksController`/`WebhookPolicy`); `ApiClient` + `{accountScoped:
  true}` for the frontend API client; `useProtonPermissions`/`protonAdmin.js`
  are already generic — do not modify them, just consume `hasPermission('security.manage')`.

## Fork patch workflow (use for every task below that touches the Chatwoot fork)

Every Chatwoot-fork change in this plan ships as a `deploy/chatwoot-fork/patches/NNNN-*.patch`
file, applied in sequence at image build time. To produce a correct patch for
task *N*:

```bash
# One-time per work session: get a clean checkout at the pinned upstream tag.
UPSTREAM=$(cat deploy/chatwoot-fork/UPSTREAM_VERSION)   # v4.15.1
git clone --depth 1 --branch "$UPSTREAM" https://github.com/chatwoot/chatwoot /tmp/cw-work
cd /tmp/cw-work && git checkout -b work

# Apply every existing patch in order, then commit as your baseline.
for p in /path/to/repo/deploy/chatwoot-fork/patches/00*.patch; do
  git apply --whitespace=nowarn "$p" || { echo "FAILED: $p"; exit 1; }
done
git add -A && git -c user.email=scratch@local -c user.name=scratch commit -q -m baseline

# Make your edits (Edit tool or hand-write files), then commit them separately
# so you can diff just your change:
git add -A && git -c user.email=scratch@local -c user.name=scratch commit -q -m "task N"

# Extract ONLY your task's diff and save it as the new patch file:
git diff HEAD~1 HEAD > /path/to/repo/deploy/chatwoot-fork/patches/NNNN-description.patch

# Verify the FULL chain (0001..NNNN) still applies cleanly from pristine:
cd /tmp/cw-work && git checkout "$UPSTREAM" -- . && git reset --hard "$UPSTREAM" -q
# ...git init fresh throwaway repo, git add -A, commit, then re-apply all
# patches 0001..NNNN in order exactly as the Dockerfile does. Any failure
# means your patch doesn't compose with what's already merged — fix and retry.
```

This is exactly the recipe already used for `0032-hide-security-settings-nav.patch`
— re-run it per task rather than trying to hand-edit `.patch` files directly.

---

## Phase 1 — Core auth capability (no UI, curl/console-testable)

### Task 1: Fix the Dockerfile so Ruby-side patches actually run at runtime

**Files:**
- Modify: `deploy/chatwoot-fork/Dockerfile:65-82`

**Interfaces:**
- Produces: a runtime image that reflects **all** patched `app/controllers/`,
  `app/models/`, `app/policies/`, `config/`, and `db/migrate/` files, not just
  `public/vite` and `vueapp.html.erb`. Every later task in this plan depends
  on this.

Today, Stage 2 (`runtime`) is built from the *pristine* upstream image and
only copies two things in from the patched builder stage: the compiled
`public/vite` bundle, and one explicit Rails view
(`app/views/layouts/vueapp.html.erb`). Every other file the builder patched —
including anything this plan adds under `app/controllers`, `app/models`,
`app/policies`, `config/`, `db/migrate/` — is silently discarded, because the
runtime stage never sees the builder's patched Ruby source tree. This has
been invisible so far because every existing patch (0001-0032) only touches
`app/javascript/**/*.{js,vue}` (compiled into `public/vite`) or that one ERB
file — this plan is the fork's first patch to touch Ruby.

- [ ] **Step 1: Write a build-time check that would have caught this today**

There's no Ruby test to write yet (no Ruby code exists), so the "test" for
this task is a build-and-inspect check. Save this as a one-off script,
`deploy/chatwoot-fork/verify-runtime-copies.sh` (new file, not a patch — it's
a build/CI helper, not a Chatwoot source change):

```bash
#!/usr/bin/env bash
# Fails if any file changed by our patches (relative to upstream) is missing
# from the *runtime* stage's final image filesystem. Run after `docker build`.
set -euo pipefail
IMAGE="${1:?usage: verify-runtime-copies.sh <image tag>}"
UPSTREAM=$(cat "$(dirname "$0")/UPSTREAM_VERSION")

# Diff the patched builder tree against a pristine clone to get the exact
# list of files our patches touch (excluding pure-frontend paths, which are
# handled correctly today via public/vite).
CHANGED=$(git -C /tmp/cw-work diff --name-only "$UPSTREAM" HEAD -- \
  'app/controllers' 'app/models' 'app/policies' 'config' 'db/migrate' || true)

FAIL=0
for f in $CHANGED; do
  if ! docker run --rm --entrypoint sh "$IMAGE" -c "test -f /app/$f"; then
    echo "MISSING AT RUNTIME: $f"
    FAIL=1
  fi
done
exit $FAIL
```

- [ ] **Step 2: Run it against today's image to confirm it fails (once Task 2+ exist)**

This step is a placeholder assertion you'll actually execute after Task 2
lands a real Ruby file — skip running it now, come back to it once
`app/models/saml_settings.rb` exists (Task 2), then:

```bash
docker build --build-arg UPSTREAM_VERSION=$(cat deploy/chatwoot-fork/UPSTREAM_VERSION) \
  -t proton-chatwoot:test-precopy deploy/chatwoot-fork/
bash deploy/chatwoot-fork/verify-runtime-copies.sh proton-chatwoot:test-precopy
```
Expected (before this task's Dockerfile fix): `MISSING AT RUNTIME:
app/models/saml_settings.rb` and a nonzero exit code.

- [ ] **Step 3: Fix the Dockerfile**

Edit `deploy/chatwoot-fork/Dockerfile`, replacing lines 65-77 with:

```dockerfile
# ── Stage 2: runtime (reuse the same base, copy rebuilt assets) ─────
FROM chatwoot/chatwoot:${UPSTREAM_VERSION} AS runtime

# Most patches only change compiled frontend output, which Vite emits to
# public/vite — replace it with the rebuilt bundle from the builder stage.
# (Knowledge patches 0009-0011 are pure app/javascript, so they land here too.)
COPY --from=builder /app/public/vite /app/public/vite

# Ruby-side patches (SAML SSO, patch 0033+) touch these directories directly
# — the runtime image is otherwise pristine upstream Ruby source, so every
# patched controller/model/policy/config/migration must be copied explicitly.
# If a future patch adds a new top-level directory under app/ (e.g.
# app/services), add it here too — patched files not listed here are
# silently reverted to upstream at runtime.
COPY --from=builder /app/app/controllers /app/app/controllers
COPY --from=builder /app/app/models /app/app/models
COPY --from=builder /app/app/policies /app/app/policies
COPY --from=builder /app/app/views /app/app/views
COPY --from=builder /app/config /app/config
COPY --from=builder /app/db/migrate /app/db/migrate
```

- [ ] **Step 4: Re-run the verify script and confirm it now passes**

```bash
docker build --build-arg UPSTREAM_VERSION=$(cat deploy/chatwoot-fork/UPSTREAM_VERSION) \
  -t proton-chatwoot:test-postcopy deploy/chatwoot-fork/
bash deploy/chatwoot-fork/verify-runtime-copies.sh proton-chatwoot:test-postcopy
```
Expected: exits 0, no `MISSING AT RUNTIME` lines.

- [ ] **Step 5: Commit**

```bash
git add deploy/chatwoot-fork/Dockerfile deploy/chatwoot-fork/verify-runtime-copies.sh
git commit -m "fix(chatwoot-fork): copy patched Ruby dirs into the runtime image

Runtime stage only copied public/vite + one ERB file from the patched
builder; every other Ruby-side change (controllers/models/policies/
config/migrations) was silently discarded. Needed before any Ruby patch
(starting with native SAML SSO) can take effect."
```

---

### Task 2: `saml_settings` migration + model

**Files:**
- Create: `db/migrate/<TIMESTAMP>_create_saml_settings.rb` (use a timestamp
  later than the latest existing migration, `20260611184600`)
- Create: `app/models/saml_settings.rb`
- Test: `spec/models/saml_settings_spec.rb`

**Interfaces:**
- Produces: `SamlSettings` model — `belongs_to :account`; columns
  `account_id` (unique), `idp_entity_id`, `idp_sso_target_url`, `idp_cert`,
  `name_identifier_format`, `role_attribute_name`, `role_mapping` (jsonb, `{}`
  default), `default_role` (string enum `agent`/`administrator`, default
  `agent`), `enabled` (boolean, default `false`), `enforce_sso` (boolean,
  default `false`). Class method `SamlSettings.resolve_role(settings,
  attribute_value)` → `'agent'` or `'administrator'`.

- [ ] **Step 1: Write the failing model spec**

```ruby
# spec/models/saml_settings_spec.rb
require 'rails_helper'

RSpec.describe SamlSettings, type: :model do
  let(:account) { create(:account) }

  it 'belongs to an account, one row per account' do
    create(:saml_settings, account: account)
    duplicate = build(:saml_settings, account: account)
    expect(duplicate).not_to be_valid
    expect(duplicate.errors[:account_id]).to be_present
  end

  it 'defaults to disabled, agent role, no enforcement' do
    settings = create(:saml_settings, account: account)
    expect(settings.enabled).to be(false)
    expect(settings.enforce_sso).to be(false)
    expect(settings.default_role).to eq('agent')
  end

  describe '.resolve_role' do
    let(:settings) do
      create(:saml_settings, account: account,
             role_attribute_name: 'department',
             role_mapping: { 'IT-Admins' => 'administrator' },
             default_role: 'agent')
    end

    it 'maps a matching attribute value to the configured role' do
      expect(described_class.resolve_role(settings, 'IT-Admins')).to eq('administrator')
    end

    it 'falls back to default_role for an unmapped value' do
      expect(described_class.resolve_role(settings, 'Sales')).to eq('agent')
    end

    it 'falls back to default_role when the attribute is missing' do
      expect(described_class.resolve_role(settings, nil)).to eq('agent')
    end
  end
end
```

Add the factory, `spec/factories/saml_settings.rb` (new file):

```ruby
FactoryBot.define do
  factory :saml_settings do
    account
    idp_entity_id { 'https://idp.example.com/metadata' }
    idp_sso_target_url { 'https://idp.example.com/sso' }
    idp_cert { 'placeholder-cert' } # real spec fixture cert added in Task 5
    name_identifier_format { 'urn:oasis:names:tc:SAML:1.1:nameid-format:emailAddress' }
  end
end
```

- [ ] **Step 2: Run it to confirm it fails**

Run: `bundle exec rspec spec/models/saml_settings_spec.rb`
Expected: FAIL — `uninitialized constant SamlSettings` (model doesn't exist yet).

- [ ] **Step 3: Write the migration**

```ruby
# db/migrate/<TIMESTAMP>_create_saml_settings.rb
class CreateSamlSettings < ActiveRecord::Migration[7.0]
  def change
    create_table :saml_settings do |t|
      t.references :account, null: false, foreign_key: true, index: { unique: true }
      t.string :idp_entity_id
      t.string :idp_sso_target_url
      t.text :idp_cert
      t.string :name_identifier_format, default: 'urn:oasis:names:tc:SAML:1.1:nameid-format:emailAddress'
      t.string :role_attribute_name
      t.jsonb :role_mapping, null: false, default: {}
      t.string :default_role, null: false, default: 'agent'
      t.boolean :enabled, null: false, default: false
      t.boolean :enforce_sso, null: false, default: false
      t.timestamps
    end
  end
end
```

- [ ] **Step 4: Write the model**

```ruby
# app/models/saml_settings.rb
class SamlSettings < ApplicationRecord
  belongs_to :account

  validates :account_id, uniqueness: true
  validates :default_role, inclusion: { in: %w[agent administrator] }

  # Resolves the Chatwoot role for a JIT-provisioned user from the IdP's
  # asserted attribute value, falling back to default_role when the
  # attribute is absent or not present in role_mapping.
  def self.resolve_role(settings, attribute_value)
    return settings.default_role if attribute_value.blank?

    settings.role_mapping.fetch(attribute_value, settings.default_role)
  end
end
```

- [ ] **Step 5: Run migration + spec, confirm pass**

Run: `bundle exec rails db:migrate RAILS_ENV=test && bundle exec rspec spec/models/saml_settings_spec.rb`
Expected: PASS (4 examples).

- [ ] **Step 6: Commit inside the fork-patch workflow**

Follow the "Fork patch workflow" above to turn this task's diff into
`deploy/chatwoot-fork/patches/0033-saml-settings-model.patch`, then in the
main repo:

```bash
git add deploy/chatwoot-fork/patches/0033-saml-settings-model.patch
git commit -m "feat(chatwoot-fork): add saml_settings model + migration"
```

---

### Task 3: SP metadata endpoint

**Files:**
- Create: `app/controllers/saml/metadata_controller.rb`
- Modify: `config/routes.rb` (add one route, near the other top-level custom
  routes — not nested under `resources :accounts`, since this must be
  reachable by an external IdP with no Chatwoot session)
- Test: `spec/requests/saml/metadata_spec.rb`

**Interfaces:**
- Consumes: `SamlSettings` (Task 2) — reads by `account_id` param.
- Produces: `GET /accounts/:account_id/saml/metadata` → SP metadata XML.
  No auth required (matches Chatwoot's own approach — SP metadata is not a
  secret).

- [ ] **Step 1: Write the failing request spec**

```ruby
# spec/requests/saml/metadata_spec.rb
require 'rails_helper'

RSpec.describe 'GET /accounts/:account_id/saml/metadata', type: :request do
  let(:account) { create(:account) }

  it 'returns SP metadata XML for a configured account' do
    create(:saml_settings, account: account)
    get "/accounts/#{account.id}/saml/metadata"

    expect(response).to have_http_status(:ok)
    expect(response.media_type).to eq('application/samlmetadata+xml')
    expect(response.body).to include('<EntityDescriptor')
    expect(response.body).to include("/accounts/#{account.id}/saml/callback")
  end

  it 'returns 404 for an account with no SAML settings' do
    get "/accounts/#{account.id}/saml/metadata"
    expect(response).to have_http_status(:not_found)
  end
end
```

- [ ] **Step 2: Run it to confirm it fails**

Run: `bundle exec rspec spec/requests/saml/metadata_spec.rb`
Expected: FAIL — routing error (no route matches).

- [ ] **Step 3: Add the route**

In `config/routes.rb`, near the top level (outside the `namespace :api`
block — this is a plain Rails-served XML endpoint, not a JSON API route),
add:

```ruby
get '/accounts/:account_id/saml/metadata', to: 'saml/metadata#show'
```

- [ ] **Step 4: Write the controller**

```ruby
# app/controllers/saml/metadata_controller.rb
class Saml::MetadataController < ActionController::Base
  def show
    settings = SamlSettings.find_by(account_id: params[:account_id])
    return head :not_found if settings.nil?

    saml_settings = OneLogin::RubySaml::Settings.new(
      sp_entity_id: sp_entity_id(settings.account_id),
      assertion_consumer_service_url: callback_url(settings.account_id),
      name_identifier_format: settings.name_identifier_format
    )

    render xml: OneLogin::RubySaml::Metadata.new.generate(saml_settings), content_type: 'application/samlmetadata+xml'
  end

  private

  def sp_entity_id(account_id)
    "#{root_url.chomp('/')}/accounts/#{account_id}/saml/metadata"
  end

  def callback_url(account_id)
    "#{root_url.chomp('/')}/accounts/#{account_id}/saml/callback"
  end
end
```

Note: the ACS URL referenced here (`/accounts/:id/saml/callback`) is a
**documentation/metadata convenience URL**, not the actual login callback —
the real callback used by the OmniAuth flow is `/auth/saml/callback` (Task 4),
because `devise_token_auth` mounts omniauth at a fixed `/auth/:provider`
path, not an account-scoped one. Task 4 threads the account id through
`RelayState` instead. Document this explicitly in the metadata XML's
`AssertionConsumerService` — **correction applied in Step 4a below.**

- [ ] **Step 4a: Correct the ACS URL to match the real callback route**

Re-edit the controller's `callback_url` method:

```ruby
  def callback_url(_account_id)
    "#{root_url.chomp('/')}auth/saml/callback"
  end
```

The real ACS endpoint is the single fixed `/auth/saml/callback` route Task 4
relies on (shared across all accounts on this install) — `account_id` is
carried via `RelayState`, not the URL, so every account's SP metadata
correctly points IdPs at the same ACS URL. Re-run Step 1's spec's third
assertion — update it to `expect(response.body).to include('auth/saml/callback')`.

- [ ] **Step 5: Run migration/spec, confirm pass**

Run: `bundle exec rspec spec/requests/saml/metadata_spec.rb`
Expected: PASS (2 examples).

- [ ] **Step 6: Fork-patch + commit**

`deploy/chatwoot-fork/patches/0034-saml-metadata-endpoint.patch`, then:
```bash
git add deploy/chatwoot-fork/patches/0034-saml-metadata-endpoint.patch
git commit -m "feat(chatwoot-fork): add SP metadata endpoint for SAML SSO"
```

---

### Task 4: Dynamic per-account `:saml` OmniAuth provider + JIT provisioning

**Files:**
- Modify: `config/initializers/omniauth.rb`
- Modify: `app/controllers/devise_overrides/omniauth_callbacks_controller.rb`
- Test: `spec/requests/saml_login_spec.rb`

**Interfaces:**
- Consumes: `SamlSettings` (Task 2), `SamlSettings.resolve_role`.
- Produces: a live `GET /auth/saml?account_id=<id>` (SP-initiated login
  start) and `POST /auth/saml/callback` (ACS) — both already routed
  generically by `devise_token_auth`'s `mount_devise_token_auth_for`, since
  `User` already declares `:saml` in `omniauth_providers` (verified in
  `app/models/user.rb:68` — this predates our patch, it's stock Chatwoot).
  This task only has to (a) register the strategy with dynamic per-account
  options, and (b) branch the existing callback handler for the `saml`
  provider so it joins an **existing** account instead of Google OAuth's
  "create a brand-new account for this email" signup flow.

`User` (`app/models/user.rb:68`) and `config/routes.rb:8`
(`omniauth_callbacks: 'devise_overrides/omniauth_callbacks'`) already wire up
generic omniauth routing for any provider — confirmed by reading the
upstream source; no route changes needed for login/callback themselves.

- [ ] **Step 1: Write the failing request spec**

This spec constructs a real signed SAML response using `ruby-saml`'s own
test fixtures (the gem ships a `OneLogin::RubySaml` test IdP keypair used in
its own suite — generate one locally instead of depending on gem internals,
so the fixture is ours and stable):

```ruby
# spec/support/saml_fixtures.rb (new file)
require 'ruby-saml'

module SamlFixtures
  IDP_CERT_FIXTURE_PATH = Rails.root.join('spec/fixtures/files/saml_idp_test.crt')
  IDP_KEY_FIXTURE_PATH = Rails.root.join('spec/fixtures/files/saml_idp_test.key')

  # Builds a signed SAMLResponse for the given account/email, using a
  # self-signed test IdP cert generated once and checked in (Step 1a).
  def self.build_signed_response(account_id:, email:, attribute_name: nil, attribute_value: nil)
    settings = OneLogin::RubySaml::Settings.new(
      idp_entity_id: 'https://idp.example.com/metadata',
      idp_cert: File.read(IDP_CERT_FIXTURE_PATH),
      assertion_consumer_service_url: 'http://test.host/auth/saml/callback',
      sp_entity_id: 'http://test.host/'
    )

    request = OneLogin::RubySaml::Response.new('') # placeholder, response built below via a Response fixture builder
    # ruby-saml doesn't ship a "build a response" helper for specs — sign one
    # manually with a minimal SAML XML template + xmlenc, matching the shape
    # its own test suite's fixture responses use.
    xml = SamlResponseXmlBuilder.build(
      account_id: account_id, email: email,
      attribute_name: attribute_name, attribute_value: attribute_value,
      destination: 'http://test.host/auth/saml/callback',
      audience: 'http://test.host/'
    )
    Base64.strict_encode64(SamlResponseXmlBuilder.sign(xml, IDP_KEY_FIXTURE_PATH))
  end
end
```

Building a hand-rolled signed-XML test builder is real, non-trivial work —
flag it explicitly rather than hand-wave it:

- [ ] **Step 1a: Generate the test IdP keypair (one-time, checked in)**

```bash
mkdir -p spec/fixtures/files
openssl req -x509 -newkey rsa:2048 -keyout spec/fixtures/files/saml_idp_test.key \
  -out spec/fixtures/files/saml_idp_test.crt -days 3650 -nodes \
  -subj "/CN=saml-test-idp.example.com"
```

- [ ] **Step 1b: Write `SamlResponseXmlBuilder` (test-only helper)**

```ruby
# spec/support/saml_response_xml_builder.rb (new file)
require 'openssl'
require 'base64'

# Minimal signed SAML Response builder for specs. NOT a production SAML
# implementation — production signature validation is entirely ruby-saml's
# job (Task 4's controller never parses XML by hand). This only exists so
# request specs can simulate what a real IdP sends, without a live IdP.
module SamlResponseXmlBuilder
  def self.build(account_id:, email:, destination:, audience:, attribute_name: nil, attribute_value: nil)
    now = Time.now.utc.strftime('%Y-%m-%dT%H:%M:%SZ')
    not_on_or_after = (Time.now.utc + 300).strftime('%Y-%m-%dT%H:%M:%SZ')
    response_id = "_#{SecureRandom.hex(16)}"
    assertion_id = "_#{SecureRandom.hex(16)}"
    relay_state_attr = attribute_name.present? ? <<~XML : ''
      <saml:AttributeStatement>
        <saml:Attribute Name="#{attribute_name}">
          <saml:AttributeValue>#{attribute_value}</saml:AttributeValue>
        </saml:Attribute>
      </saml:AttributeStatement>
    XML

    <<~XML
      <samlp:Response xmlns:samlp="urn:oasis:names:tc:SAML:2.0:protocol"
                       xmlns:saml="urn:oasis:names:tc:SAML:2.0:assertion"
                       ID="#{response_id}" Version="2.0" IssueInstant="#{now}"
                       Destination="#{destination}">
        <saml:Issuer>https://idp.example.com/metadata</saml:Issuer>
        <samlp:Status><samlp:StatusCode Value="urn:oasis:names:tc:SAML:2.0:status:Success"/></samlp:Status>
        <saml:Assertion ID="#{assertion_id}" IssueInstant="#{now}" Version="2.0">
          <saml:Issuer>https://idp.example.com/metadata</saml:Issuer>
          <saml:Subject>
            <saml:NameID Format="urn:oasis:names:tc:SAML:1.1:nameid-format:emailAddress">#{email}</saml:NameID>
            <saml:SubjectConfirmation Method="urn:oasis:names:tc:SAML:2.0:cm:bearer">
              <saml:SubjectConfirmationData NotOnOrAfter="#{not_on_or_after}" Recipient="#{destination}"/>
            </saml:SubjectConfirmation>
          </saml:Subject>
          <saml:Conditions NotBefore="#{now}" NotOnOrAfter="#{not_on_or_after}">
            <saml:AudienceRestriction><saml:Audience>#{audience}</saml:Audience></saml:AudienceRestriction>
          </saml:Conditions>
          <saml:AuthnStatement AuthnInstant="#{now}">
            <saml:AuthnContext><saml:AuthnContextClassRef>urn:oasis:names:tc:SAML:2.0:ac:classes:Password</saml:AuthnContextClassRef></saml:AuthnContext>
          </saml:AuthnStatement>
          #{relay_state_attr}
        </saml:Assertion>
      </samlp:Response>
    XML
  end

  # Signs with XML-DSig (enveloped signature on the Assertion), using
  # ruby-saml's own XML security helper so the signature shape matches what
  # the strategy's validator expects.
  def self.sign(xml, key_path)
    doc = Xmlenc::Builder.new # not used; see note below
    # ruby-saml's OneLogin::RubySaml::Response validates via xmlsec; for a
    # spec fixture, sign using the `xmlsec1` CLI (already a ruby-saml runtime
    # dependency in the base image) rather than reimplementing XML-DSig:
    require 'tempfile'
    Tempfile.create(['unsigned', '.xml']) do |unsigned|
      unsigned.write(xml)
      unsigned.flush
      signed = `xmlsec1 --sign --privkey-pem #{key_path} --id-attr:ID Assertion #{unsigned.path}`
      raise "xmlsec1 signing failed" if signed.blank?

      signed
    end
  end
end
```

Register the support file in `spec/rails_helper.rb`'s existing
`Dir[Rails.root.join('spec/support/**/*.rb')].sort.each { |f| require f }`
line (already present in Chatwoot's stock `rails_helper.rb` — no change
needed there, just confirm the glob picks up the new files under
`spec/support/`).

- [ ] **Step 1c: Write the actual failing login spec**

```ruby
# spec/requests/saml_login_spec.rb
require 'rails_helper'

RSpec.describe 'SAML SSO login', type: :request do
  let(:account) { create(:account) }
  let!(:saml_settings) do
    create(:saml_settings, account: account, enabled: true,
           idp_cert: File.read(SamlFixtures::IDP_CERT_FIXTURE_PATH),
           idp_entity_id: 'https://idp.example.com/metadata',
           idp_sso_target_url: 'https://idp.example.com/sso',
           role_attribute_name: 'department',
           role_mapping: { 'IT-Admins' => 'administrator' })
  end

  it 'JIT-provisions a new user as agent by default and signs them in' do
    saml_response = SamlFixtures.build_signed_response(
      account_id: account.id, email: 'newhire@example.com'
    )

    post '/auth/saml/callback', params: { SAMLResponse: saml_response, RelayState: "account_id:#{account.id}" }

    expect(response).to have_http_status(:redirect) # redirects to login_page_url with sso_auth_token
    user = User.find_by(email: 'newhire@example.com')
    expect(user).to be_present
    expect(user.account_users.find_by(account: account).role).to eq('agent')
  end

  it 'maps the configured IdP attribute to administrator' do
    saml_response = SamlFixtures.build_signed_response(
      account_id: account.id, email: 'admin@example.com',
      attribute_name: 'department', attribute_value: 'IT-Admins'
    )

    post '/auth/saml/callback', params: { SAMLResponse: saml_response, RelayState: "account_id:#{account.id}" }

    user = User.find_by(email: 'admin@example.com')
    expect(user.account_users.find_by(account: account).role).to eq('administrator')
  end

  it 'signs in an existing AccountUser without duplicating them' do
    existing = create(:user, email: 'existing@example.com')
    create(:account_user, account: account, user: existing, role: 'agent')
    saml_response = SamlFixtures.build_signed_response(account_id: account.id, email: 'existing@example.com')

    expect {
      post '/auth/saml/callback', params: { SAMLResponse: saml_response, RelayState: "account_id:#{account.id}" }
    }.not_to change(User, :count)
  end

  it 'rejects a response for a disabled account' do
    saml_settings.update!(enabled: false)
    saml_response = SamlFixtures.build_signed_response(account_id: account.id, email: 'x@example.com')

    post '/auth/saml/callback', params: { SAMLResponse: saml_response, RelayState: "account_id:#{account.id}" }
    expect(User.find_by(email: 'x@example.com')).to be_nil
  end
end
```

- [ ] **Step 2: Run it to confirm it fails**

Run: `bundle exec rspec spec/requests/saml_login_spec.rb`
Expected: FAIL (currently 404/error — no `:saml` strategy registered, so
`devise_token_auth`'s passthru has nothing to redirect the callback into).

- [ ] **Step 3: Register the dynamic `:saml` provider**

```ruby
# config/initializers/omniauth.rb
OmniAuth.config.full_host = ENV.fetch('FRONTEND_URL', 'http://localhost:3000')

Rails.application.config.middleware.use OmniAuth::Builder do
  provider :google_oauth2, ENV.fetch('GOOGLE_OAUTH_CLIENT_ID', nil), ENV.fetch('GOOGLE_OAUTH_CLIENT_SECRET', nil), {
    provider_ignores_state: true
  }

  # Per-account SAML: options are resolved at request time from that
  # account's SamlSettings row, not statically at boot (Chatwoot serves many
  # accounts' IdP configs from one process). account_id comes from the
  # `account_id` query param on the REQUEST phase (the SP-initiated login
  # link an admin gives their users, e.g. "/auth/saml?account_id=42"); the
  # strategy carries it forward as RelayState so it round-trips back
  # unmodified on the CALLBACK phase per the SAML spec (a client can't forge
  # RelayState into a *different* account without also forging the IdP's
  # signature, since RelayState land alongside the signed assertion in the
  # same POST the browser can't tamper with once it's signed by the IdP).
  provider :saml, setup: lambda { |env|
    request = Rack::Request.new(env)
    account_id = request.params['account_id'] || parse_account_id_from_relay_state(request.params['RelayState'])
    settings = account_id.present? ? SamlSettings.find_by(account_id: account_id, enabled: true) : nil

    strategy = env['omniauth.strategy']
    if settings.nil?
      strategy.options[:idp_sso_target_url] = nil # forces omniauth-saml to fail closed, no assertion possible
      next
    end

    strategy.options[:idp_entity_id] = settings.idp_entity_id
    strategy.options[:idp_sso_target_url] = settings.idp_sso_target_url
    strategy.options[:idp_cert] = settings.idp_cert
    strategy.options[:name_identifier_format] = settings.name_identifier_format
    strategy.options[:sp_entity_id] = "#{ENV.fetch('FRONTEND_URL', 'http://localhost:3000')}/"
    strategy.options[:assertion_consumer_service_url] = "#{ENV.fetch('FRONTEND_URL', 'http://localhost:3000')}/auth/saml/callback"
    strategy.options[:request_attributes] = []
    if account_id.present?
      strategy.options[:idp_sso_service_url_runtime_params] = {}
      env['omniauth.params'] ||= {}
      env['omniauth.params']['RelayState'] = "account_id:#{account_id}"
    end
  }
end

def parse_account_id_from_relay_state(relay_state)
  return nil if relay_state.blank?

  match = relay_state.match(/\Aaccount_id:(\d+)\z/)
  match && match[1]
end
```

- [ ] **Step 4: Branch the callback controller for SAML's account-scoped JIT flow**

```ruby
# app/controllers/devise_overrides/omniauth_callbacks_controller.rb
class DeviseOverrides::OmniauthCallbacksController < DeviseTokenAuth::OmniauthCallbacksController
  include EmailHelper

  def omniauth_success
    if auth_hash['provider'] == 'saml'
      handle_saml_success
      return
    end

    get_resource_from_auth_hash
    @resource.present? ? sign_in_user : sign_up_user
  end

  private

  # SAML never creates a brand-new Account (unlike Google OAuth's
  # create_account_for_user signup flow below) — it always JIT-provisions
  # into the SPECIFIC account whose IdP config produced this assertion,
  # identified via RelayState, never trusted from any other source.
  def handle_saml_success
    account_id = relay_state_account_id
    settings = account_id.present? ? SamlSettings.find_by(account_id: account_id, enabled: true) : nil
    return redirect_to(login_page_url(error: 'saml-not-configured')) if settings.nil?

    email = auth_hash.dig('info', 'email')
    return redirect_to(login_page_url(error: 'saml-no-email')) if email.blank?

    account = Account.find(account_id)
    @resource = User.from_email(email) || create_saml_user(email)
    ensure_account_user(account, settings)

    sign_in_user
  end

  def relay_state_account_id
    relay_state = request.params['RelayState']
    match = relay_state&.match(/\Aaccount_id:(\d+)\z/)
    match && match[1]
  end

  def create_saml_user(email)
    User.create!(
      name: auth_hash.dig('info', 'name').presence || email.split('@').first,
      email: email,
      password: "#{SecureRandom.hex(16)}aA1!",
      confirmed_at: Time.current
    )
  end

  def ensure_account_user(account, settings)
    return if AccountUser.exists?(account: account, user: @resource)

    role = SamlSettings.resolve_role(settings, auth_hash.dig('info', 'attributes', settings.role_attribute_name))
    AccountUser.create!(account: account, user: @resource, role: role)
  end

  def sign_in_user
    needs_password_reset = oauth_user_needs_password_reset?
    @resource.skip_confirmation! if confirmable_enabled?
    set_random_password_if_oauth_user if needs_password_reset

    encoded_email = ERB::Util.url_encode(@resource.email)
    redirect_to login_page_url(email: encoded_email, sso_auth_token: @resource.generate_sso_auth_token)
  end

  # ... sign_in_user_on_mobile, sign_up_user, login_page_url,
  # account_signup_allowed?, resource_class, get_resource_from_auth_hash,
  # validate_signup_email_is_business_domain?, create_account_for_user,
  # oauth_user_needs_password_reset?, set_random_password_if_oauth_user
  # remain UNCHANGED from the existing file — only omniauth_success gained
  # the SAML branch, and three new private methods were added above.
end
```

- [ ] **Step 5: Run the spec, confirm pass**

Run: `bundle exec rspec spec/requests/saml_login_spec.rb`
Expected: PASS (4 examples). If `xmlsec1` isn't on the spec-running machine,
install it first (`apk add xmlsec` on the Alpine base image / `apt-get install
xmlsec1` in CI) — it's already a transitive runtime dependency of `ruby-saml`,
so it exists in the built image, but may be missing on a bare dev machine.

- [ ] **Step 6: Fork-patch + commit**

`deploy/chatwoot-fork/patches/0035-saml-login-jit-provisioning.patch`:
```bash
git add deploy/chatwoot-fork/patches/0035-saml-login-jit-provisioning.patch
git commit -m "feat(chatwoot-fork): dynamic per-account SAML login + JIT provisioning"
```

---

### Task 5: Manual verification against a real IdP (pre-ship gate for Phase 1)

**Files:** none (manual QA task, no code changes)

- [ ] **Step 1: Stand up a free test IdP**

Create a free developer tenant on Okta or Auth0 (either works — `ruby-saml`
is IdP-agnostic). Configure a SAML 2.0 app pointing its ACS URL at
`https://<a tunneled or staging FRONTEND_URL>/auth/saml/callback`, SP entity
ID `https://<FRONTEND_URL>/`.

- [ ] **Step 2: Configure a test tenant's `saml_settings` row**

Via `rails console` on a staging deploy (not production):
```ruby
account = Account.find(<test account id>)
SamlSettings.create!(account: account, enabled: true,
  idp_entity_id: '<from IdP metadata>',
  idp_sso_target_url: '<from IdP metadata>',
  idp_cert: '<IdP signing cert, from IdP metadata>')
```

- [ ] **Step 3: Walk the login flow in a real browser**

Visit `https://<FRONTEND_URL>/auth/saml?account_id=<id>`, authenticate at the
IdP, confirm redirect back lands the browser on a logged-in Chatwoot session
for the correct account, and that a new `User`/`AccountUser` was created with
role `agent` (or the mapped role, if you configured an attribute mapping in
the IdP app).

- [ ] **Step 4: Record the result**

Note the outcome (pass/fail, any gem-version or IdP-quirk findings) in this
plan file or a follow-up note before Phase 2 work begins — this is the one
step in Phase 1 that can't be automated and is the actual proof the feature
works, not just that the code compiles.

---

## Phase 2 — Settings UI (depends on Phase 1)

### Task 6: Backend RBAC permission — `security.manage`

**Files:**
- Modify: `backend/apps/backend/src/chatbot/features/authz/seed.py`
- Test: `backend/apps/backend/src/chatbot/features/authz/test_seed.py`

**Interfaces:**
- Produces: `security.manage` registered in `PERMISSION_REGISTRY`,
  auto-granted to the `administrator` role (existing seeding loop already
  grants every `PERMISSION_REGISTRY` key to `administrator` — no new logic
  needed), not granted to `agent`.

- [ ] **Step 1: Read the existing seed test to match its pattern**

Run: `cd backend/apps/backend && .venv/bin/pytest src/chatbot/features/authz/test_seed.py -v`
to see current passing tests and their fixture shape before adding to them.

- [ ] **Step 2: Write the failing test**

Add to `test_seed.py` (matching whatever fixture/repo pattern the existing
tests use — read the file first; the shape below assumes an in-memory repo
fixture named `repo`, adjust to match):

```python
async def test_security_manage_registered_and_granted_to_administrator(repo):
    await seed_defaults(repo)

    assert "security.manage" in [p.key for p in await repo.list_permissions()]
    assert "security.manage" in await repo.role_permissions("administrator")
    assert "security.manage" not in await repo.role_permissions("agent")
```

- [ ] **Step 3: Run it to confirm it fails**

Run: `.venv/bin/pytest src/chatbot/features/authz/test_seed.py::test_security_manage_registered_and_granted_to_administrator -v`
Expected: FAIL — assertion error, `security.manage` not in permissions list.

- [ ] **Step 4: Add the permission**

In `seed.py`, add one line to `PERMISSION_REGISTRY`:

```python
PERMISSION_REGISTRY: dict[str, str] = {
    "knowledge.edit": "Edit Knowledge Base content",
    "kb.ingest": "Trigger KB document ingestion",
    "persona.edit": "Edit assistant persona/instructions",
    "sla.manage": "Manage SLA policies",
    "audit.view": "View the audit log",
    "roles.manage": "Manage roles and permission assignments",
    "security.manage": "Manage Security settings (SAML SSO, etc.)",
}
```

No other change needed — the existing `seed_defaults` loop already grants
every `PERMISSION_REGISTRY` key to `administrator`, and `_AGENT_PERMISSIONS`
is untouched so `agent` doesn't get it.

- [ ] **Step 5: Run the test, confirm pass**

Run: `.venv/bin/pytest src/chatbot/features/authz/test_seed.py -v`
Expected: PASS, including the new test.

- [ ] **Step 6: Commit**

```bash
git add backend/apps/backend/src/chatbot/features/authz/seed.py \
        backend/apps/backend/src/chatbot/features/authz/test_seed.py
git commit -m "feat(authz): register security.manage permission for the Security settings page"
```

---

### Task 7: Rails CRUD API for SAML settings (admin-only)

**Files:**
- Create: `app/controllers/api/v1/accounts/security/saml_settings_controller.rb`
- Create: `app/policies/security_saml_settings_policy.rb`
- Modify: `config/routes.rb`
- Test: `spec/requests/api/v1/accounts/security/saml_settings_spec.rb`
- Test: `spec/policies/security_saml_settings_policy_spec.rb`

**Interfaces:**
- Consumes: `SamlSettings` (Task 2).
- Produces: `GET/POST/PUT/DELETE /api/v1/accounts/:account_id/security_saml_settings`,
  administrator-only via Pundit.

- [ ] **Step 1: Write the failing policy spec**

```ruby
# spec/policies/security_saml_settings_policy_spec.rb
require 'rails_helper'

RSpec.describe SecuritySamlSettingsPolicy, type: :policy do
  subject(:policy) { described_class }

  let(:account) { create(:account) }
  let(:administrator) { create(:user, :administrator, account: account) }
  let(:agent) { create(:user, account: account) }
  let(:record) { create(:saml_settings, account: account) }

  let(:administrator_context) { { user: administrator, account: account, account_user: account.account_users.first } }
  let(:agent_context) { { user: agent, account: account, account_user: account.account_users.last } }

  permissions :show?, :create?, :update?, :destroy? do
    it { expect(policy).to permit(administrator_context, record) }
    it { expect(policy).not_to permit(agent_context, record) }
  end
end
```

- [ ] **Step 2: Run it, confirm it fails**

Run: `bundle exec rspec spec/policies/security_saml_settings_policy_spec.rb`
Expected: FAIL — `uninitialized constant SecuritySamlSettingsPolicy`.

- [ ] **Step 3: Write the policy**

```ruby
# app/policies/security_saml_settings_policy.rb
class SecuritySamlSettingsPolicy < ApplicationPolicy
  def show?
    @account_user.administrator?
  end

  def create?
    @account_user.administrator?
  end

  def update?
    @account_user.administrator?
  end

  def destroy?
    @account_user.administrator?
  end
end
```

- [ ] **Step 4: Run policy spec, confirm pass**

Run: `bundle exec rspec spec/policies/security_saml_settings_policy_spec.rb`
Expected: PASS.

- [ ] **Step 5: Write the failing request spec**

```ruby
# spec/requests/api/v1/accounts/security/saml_settings_spec.rb
require 'rails_helper'

RSpec.describe 'Security::SamlSettings API', type: :request do
  let(:account) { create(:account) }
  let(:administrator) { create(:user, :administrator, account: account) }
  let(:agent) { create(:user, account: account) }

  describe 'GET /api/v1/accounts/:account_id/security_saml_settings' do
    it 'returns 404 when unconfigured, as an admin' do
      get "/api/v1/accounts/#{account.id}/security_saml_settings", headers: administrator.create_new_auth_token
      expect(response).to have_http_status(:not_found)
    end

    it 'is forbidden for a non-admin agent' do
      get "/api/v1/accounts/#{account.id}/security_saml_settings", headers: agent.create_new_auth_token
      expect(response).to have_http_status(:unauthorized).or have_http_status(:forbidden)
    end
  end

  describe 'POST /api/v1/accounts/:account_id/security_saml_settings' do
    it 'creates settings as an admin' do
      post "/api/v1/accounts/#{account.id}/security_saml_settings",
        params: { security_saml_settings: { idp_entity_id: 'https://idp.example.com', idp_sso_target_url: 'https://idp.example.com/sso', idp_cert: 'cert-data' } },
        headers: administrator.create_new_auth_token
      expect(response).to have_http_status(:success)
      expect(account.reload.saml_settings).to be_present
    end
  end

  describe 'PUT /api/v1/accounts/:account_id/security_saml_settings' do
    it 'updates existing settings' do
      create(:saml_settings, account: account)
      put "/api/v1/accounts/#{account.id}/security_saml_settings",
        params: { security_saml_settings: { enabled: true } },
        headers: administrator.create_new_auth_token
      expect(response).to have_http_status(:success)
      expect(account.reload.saml_settings.enabled).to be(true)
    end
  end

  describe 'DELETE /api/v1/accounts/:account_id/security_saml_settings' do
    it 'removes settings' do
      create(:saml_settings, account: account)
      delete "/api/v1/accounts/#{account.id}/security_saml_settings", headers: administrator.create_new_auth_token
      expect(response).to have_http_status(:success)
      expect(account.reload.saml_settings).to be_nil
    end
  end
end
```

Add `has_one :saml_settings` to `app/models/account.rb` if it's not already
implied by the `belongs_to :account` on `SamlSettings` — Rails does not infer
the inverse automatically for `account.saml_settings`. This is a one-line
addition; add it as part of this task's diff:

```ruby
# app/models/account.rb — add alongside the other has_one/has_many declarations
has_one :saml_settings, dependent: :destroy
```

- [ ] **Step 6: Run it, confirm it fails**

Run: `bundle exec rspec spec/requests/api/v1/accounts/security/saml_settings_spec.rb`
Expected: FAIL — routing error.

- [ ] **Step 7: Add the route**

In `config/routes.rb`, inside the existing `resources :accounts do ... end`
block that already contains `resource :saml_settings` (line ~95) — add a
**sibling** resource with a different name so it doesn't collide with the
enterprise-bound `saml_settings` route:

```ruby
resource :security_saml_settings, only: [:show, :create, :update, :destroy],
  controller: 'security/saml_settings'
```

- [ ] **Step 8: Write the controller**

```ruby
# app/controllers/api/v1/accounts/security/saml_settings_controller.rb
class Api::V1::Accounts::Security::SamlSettingsController < Api::V1::Accounts::BaseController
  before_action :check_authorization
  before_action :fetch_settings, only: [:show, :update, :destroy]

  def show
    return head :not_found if @settings.nil?
  end

  def create
    @settings = Current.account.build_saml_settings(saml_settings_params)
    @settings.save!
  end

  def update
    return head :not_found if @settings.nil?

    @settings.update!(saml_settings_params)
  end

  def destroy
    return head :not_found if @settings.nil?

    @settings.destroy!
    head :ok
  end

  private

  def fetch_settings
    @settings = Current.account.saml_settings
  end

  def saml_settings_params
    params.require(:security_saml_settings).permit(
      :idp_entity_id, :idp_sso_target_url, :idp_cert, :name_identifier_format,
      :role_attribute_name, :default_role, :enabled, :enforce_sso,
      role_mapping: {}
    )
  end

  def check_authorization
    authorize(@settings || SamlSettings.new(account: Current.account), policy_class: SecuritySamlSettingsPolicy)
  end
end
```

Note `Current.account.build_saml_settings` — Rails' `has_one` association
provides `build_saml_settings` automatically once Step 5's `has_one
:saml_settings` is added to `Account`.

- [ ] **Step 9: Add a `show.json.jbuilder` view (or confirm one isn't needed)**

Check `app/views/api/v1/accounts/webhooks/` for the existing convention (likely
plain `render json:` in the controller works fine without a Jbuilder view for
a singular resource) — if Chatwoot's `ActiveModelSerializers`/Jbuilder
convention requires an explicit view for this controller path, add:

```ruby
# app/views/api/v1/accounts/security/saml_settings/show.json.jbuilder
json.id @settings.id
json.idp_entity_id @settings.idp_entity_id
json.idp_sso_target_url @settings.idp_sso_target_url
json.name_identifier_format @settings.name_identifier_format
json.role_attribute_name @settings.role_attribute_name
json.role_mapping @settings.role_mapping
json.default_role @settings.default_role
json.enabled @settings.enabled
json.enforce_sso @settings.enforce_sso
# idp_cert deliberately omitted from the response after the first create —
# treat it as write-only in the API to avoid echoing a large PEM blob on
# every settings fetch; the UI shows "configured" vs not, not the raw cert.
```

Reuse this same `show.json.jbuilder` for `create`/`update` by rendering it
explicitly at the end of those actions if Chatwoot's Jbuilder convention
doesn't auto-render the same-named partial for non-`show` actions — verify
against `webhooks_controller`'s corresponding views directory and mirror
whatever it does.

- [ ] **Step 10: Run both specs, confirm pass**

Run: `bundle exec rspec spec/requests/api/v1/accounts/security/saml_settings_spec.rb spec/policies/security_saml_settings_policy_spec.rb`
Expected: PASS (all examples).

- [ ] **Step 11: Fork-patch + commit**

`deploy/chatwoot-fork/patches/0036-security-saml-settings-api.patch`:
```bash
git add deploy/chatwoot-fork/patches/0036-security-saml-settings-api.patch
git commit -m "feat(chatwoot-fork): admin CRUD API for Security > SAML settings"
```

---

### Task 8: Frontend API client + Security settings page

**Files:**
- Create: `app/javascript/dashboard/api/securitySamlSettings.js`
- Modify: `app/javascript/dashboard/routes/dashboard/settings/security/Index.vue`
  (replace the paywall-check body with our native form — this route already
  exists and is already wired at `/accounts/:accountId/settings/security`,
  we're changing what it renders, not adding a new route)
- Create: `app/javascript/dashboard/routes/dashboard/settings/security/components/SamlSettingsForm.vue`
- Modify: `app/javascript/dashboard/components-next/sidebar/Sidebar.vue` (re-add
  the nav entry removed by patch `0032`, this time gated by `security.manage`)
- Modify: `app/javascript/dashboard/i18n/locale/en/settings.json` (add form
  labels; leave the existing `PAYWALL.*` keys — no longer referenced, harmless
  to leave, or remove if you prefer — not required either way)

**Interfaces:**
- Consumes: `useProtonPermissions` (existing, unchanged) for
  `hasPermission('security.manage')`; `ApiClient` (existing, unchanged) as
  the base class for the new client.
- Produces: a working Settings → Security page for administrators; hidden
  from anyone without `security.manage`.

- [ ] **Step 1: Write the API client (no test framework exists for these thin
  ApiClient subclasses in this codebase — mirror `webhooks.js` exactly, which
  also has no dedicated spec file; correctness is covered by the Vue
  component test in Step 3)**

```javascript
// app/javascript/dashboard/api/securitySamlSettings.js
import ApiClient from './ApiClient';

class SecuritySamlSettings extends ApiClient {
  constructor() {
    super('security_saml_settings', { accountScoped: true });
  }

  get() {
    return axios.get(this.url);
  }

  create(payload) {
    return axios.post(this.url, { security_saml_settings: payload });
  }

  update(payload) {
    return axios.put(this.url, { security_saml_settings: payload });
  }

  delete() {
    return axios.delete(this.url);
  }
}

export default new SecuritySamlSettings();
```

- [ ] **Step 2: Replace `Index.vue`'s paywall body**

The current file (verified in-repo) is:

```vue
<script setup>
import { computed } from 'vue';
import BaseSettingsHeader from '../components/BaseSettingsHeader.vue';
import SettingsLayout from '../SettingsLayout.vue';
import SamlSettings from './components/SamlSettings.vue';
import SamlPaywall from './components/SamlPaywall.vue';

import { usePolicy } from 'dashboard/composables/usePolicy';
import { INSTALLATION_TYPES } from 'dashboard/constants/installationTypes';
import { FEATURE_FLAGS } from 'dashboard/featureFlags';
const { shouldShow, shouldShowPaywall } = usePolicy();
/* ...shouldShowSaml / showPaywall computed properties... */
</script>

<template>
  <SettingsLayout :loading-message="$t('ATTRIBUTES_MGMT.LOADING')">
    <template #header>
      <BaseSettingsHeader :title="$t('SECURITY_SETTINGS.TITLE')" ... />
    </template>
    <template #body>
      <SamlPaywall v-if="showPaywall" />
      <SamlSettings v-else-if="shouldShowSaml" />
      <div v-else>{{ $t('SECURITY_SETTINGS.SAML_DISABLED_MESSAGE') }}</div>
    </template>
  </SettingsLayout>
</template>
```

Replace it with a permission-gated render of our own form, keeping the same
`SettingsLayout`/`BaseSettingsHeader` shell (both are plain, non-enterprise
layout components — safe to keep using):

```vue
<!-- app/javascript/dashboard/routes/dashboard/settings/security/Index.vue -->
<script setup>
import BaseSettingsHeader from '../components/BaseSettingsHeader.vue';
import SettingsLayout from '../SettingsLayout.vue';
import SamlSettingsForm from './components/SamlSettingsForm.vue';
import { useProtonPermissions } from 'dashboard/composables/useProtonPermissions';

const { hasPermission } = useProtonPermissions();
</script>

<template>
  <SettingsLayout :loading-message="$t('ATTRIBUTES_MGMT.LOADING')">
    <template #header>
      <BaseSettingsHeader
        :title="$t('SECURITY_SETTINGS.TITLE')"
        :description="$t('SECURITY_SETTINGS.DESCRIPTION')"
      />
    </template>
    <template #body>
      <SamlSettingsForm v-if="hasPermission('security.manage')" />
      <div v-else class="mt-6 text-sm text-slate-600">
        {{ $t('SECURITY_SETTINGS.NO_PERMISSION') }}
      </div>
    </template>
  </SettingsLayout>
</template>
```

`usePolicy`, `INSTALLATION_TYPES`, `FEATURE_FLAGS`, `SamlPaywall`, and the
enterprise `SamlSettings` import are all dropped — none of Chatwoot's
Enterprise gating machinery is referenced anymore in this file.

- [ ] **Step 3: Write the SAML settings form component**

The enterprise `SamlSettings.vue` we're replacing already establishes which
design-system components this exact page uses (verified in-repo:
`WithLabel`, `TextInput`, `TextArea`, `Switch`, `NextButton`) — reuse the same
ones for visual consistency with the rest of Settings:

```vue
<!-- app/javascript/dashboard/routes/dashboard/settings/security/components/SamlSettingsForm.vue -->
<script setup>
import { ref, onMounted } from 'vue';
import { useI18n } from 'vue-i18n';
import { useAlert } from 'dashboard/composables';
import securitySamlSettings from 'dashboard/api/securitySamlSettings';
import WithLabel from 'v3/components/Form/WithLabel.vue';
import TextInput from 'next/input/Input.vue';
import TextArea from 'next/textarea/TextArea.vue';
import Switch from 'next/switch/Switch.vue';
import NextButton from 'next/button/Button.vue';

const { t } = useI18n();
const loading = ref(true);
const saving = ref(false);
const exists = ref(false);
const form = ref({
  idp_entity_id: '',
  idp_sso_target_url: '',
  idp_cert: '',
  role_attribute_name: '',
  default_role: 'agent',
  enabled: false,
  enforce_sso: false,
});

const load = async () => {
  loading.value = true;
  try {
    const { data } = await securitySamlSettings.get();
    exists.value = true;
    form.value = { ...form.value, ...data, idp_cert: '' }; // cert is write-only, never echoed back
  } catch (error) {
    if (error?.response?.status !== 404) throw error;
    exists.value = false;
  } finally {
    loading.value = false;
  }
};

const save = async () => {
  saving.value = true;
  try {
    if (exists.value) {
      await securitySamlSettings.update(form.value);
    } else {
      await securitySamlSettings.create(form.value);
      exists.value = true;
    }
    useAlert(t('SECURITY_SETTINGS.SAML.SAVE_SUCCESS'));
  } catch (error) {
    useAlert(t('SECURITY_SETTINGS.SAML.SAVE_ERROR'));
  } finally {
    saving.value = false;
  }
};

onMounted(load);
</script>

<template>
  <div v-if="!loading" class="flex flex-col gap-4 max-w-2xl">
    <WithLabel :label="$t('SECURITY_SETTINGS.SAML.IDP_ENTITY_ID')">
      <TextInput v-model="form.idp_entity_id" />
    </WithLabel>
    <WithLabel :label="$t('SECURITY_SETTINGS.SAML.IDP_SSO_URL')">
      <TextInput v-model="form.idp_sso_target_url" />
    </WithLabel>
    <WithLabel :label="$t('SECURITY_SETTINGS.SAML.IDP_CERT')">
      <TextArea v-model="form.idp_cert" :rows="6" :placeholder="$t('SECURITY_SETTINGS.SAML.IDP_CERT_PLACEHOLDER')" />
    </WithLabel>
    <WithLabel :label="$t('SECURITY_SETTINGS.SAML.ROLE_ATTRIBUTE')">
      <TextInput v-model="form.role_attribute_name" />
    </WithLabel>
    <Switch v-model="form.enabled" :label="$t('SECURITY_SETTINGS.SAML.ENABLED')" />
    <Switch v-model="form.enforce_sso" :label="$t('SECURITY_SETTINGS.SAML.ENFORCE_SSO')" />
    <NextButton :is-loading="saving" @click="save">
      {{ $t('SECURITY_SETTINGS.SAML.SAVE') }}
    </NextButton>
  </div>
</template>
```

`role_mapping` (the attribute-value → role table) is deliberately left out of
this first form — Task 2's model already supports it via the `role_mapping`
jsonb column and `securitySamlSettings.js`'s payload passes through whatever
`form.value` contains, so adding a mapping-table sub-component later is
additive, not a breaking change to this task's shape.

- [ ] **Step 4: Add the i18n keys**

In `app/javascript/dashboard/i18n/locale/en/settings.json`, under the
existing `SECURITY_SETTINGS` key (alongside the existing `PAYWALL` object),
add:

```json
"SECURITY_SETTINGS": {
  "TITLE": "Security",
  "NO_PERMISSION": "You don't have permission to manage security settings.",
  "SAML": {
    "IDP_ENTITY_ID": "IdP Entity ID",
    "IDP_SSO_URL": "IdP Single Sign-On URL",
    "IDP_CERT": "IdP Certificate (PEM)",
    "ROLE_ATTRIBUTE": "Role mapping attribute (optional)",
    "ENABLED": "Enable SAML SSO for this account",
    "ENFORCE_SSO": "Require SSO (disable password login)",
    "SAVE": "Save",
    "SAVE_SUCCESS": "SAML settings saved",
    "SAVE_ERROR": "Could not save SAML settings",
    "IDP_CERT_PLACEHOLDER": "Leave blank to keep the existing certificate"
  }
}
```

- [ ] **Step 5: Re-add the gated nav entry**

In `Sidebar.vue`, in the same `menuItems` list patch `0032` removed the
Security entry from, add it back gated on the permission — mirroring exactly
how `sla.manage`/`roles.manage` gate their own entries elsewhere in this same
computed property (read those existing conditional blocks in the file for
the exact `...(protonHasPermission('...') ? [...] : [])` spread shape and
match it):

```javascript
...(protonHasPermission('security.manage')
  ? [
      {
        name: 'Settings Security',
        label: t('SIDEBAR.SECURITY'),
        icon: 'i-lucide-shield',
        to: accountScopedRoute('security_settings_index'),
      },
    ]
  : []),
```

Insert this at the same position patch `0032` removed it from (right after
"Conversation Workflow", before "Settings Billing").

- [ ] **Step 6: Manual smoke test**

No automated frontend test framework step is prescribed here (this codebase's
existing `security/` route has no Vue component test file to mirror) — smoke
test manually: `pnpm exec vite build` succeeds, then in a running dev/staging
instance, confirm (a) an account administrator sees "Security" in the sidebar
and can fill in/save the form, (b) a non-admin agent does not see the nav
entry, (c) `git apply --check` for the full patch chain 0001-0037 succeeds
from a clean upstream checkout (per the Fork patch workflow above).

- [ ] **Step 7: Fork-patch + commit**

`deploy/chatwoot-fork/patches/0037-security-settings-ui.patch`:
```bash
git add deploy/chatwoot-fork/patches/0037-security-settings-ui.patch
git commit -m "feat(chatwoot-fork): native Security settings page with SAML SSO form

Supersedes 0032 — the nav entry returns, gated by the security.manage
RBAC permission instead of Chatwoot's dead Enterprise paywall."
```

---

## Phase 3 — Enforce-SSO toggle (depends on Phase 1; independent of Phase 2's UI existing, but the toggle is unreachable without it)

### Task 9: Block password login when `enforce_sso` is set

**Files:**
- Modify: `app/controllers/devise_overrides/sessions_controller.rb`
- Test: `spec/requests/devise_overrides/sessions_spec.rb` (create if it
  doesn't already exist covering this controller — check first)

**Interfaces:**
- Consumes: `SamlSettings` (Task 2), via `user.accounts`.
- Produces: `find_user_for_authentication` returns `nil` (falls through to
  the existing generic "invalid credentials" path) for a user whose **any**
  account has `enforce_sso: true`. Documented simplification: this blocks
  password login account-wide per user, not per-account-context, because
  Devise Token Auth authenticates the user before an account is selected.
  Given this platform's one-tenant-per-Chatwoot-install deployment model
  (CLAUDE.md: "each customer gets its own isolated Chatwoot + Zammad + agent
  stack"), a user having accounts split across enforced/unenforced SSO on the
  *same install* is an edge case, not the common path — acceptable for a
  first version, called out explicitly rather than silently assumed.

- [ ] **Step 1: Write the failing request spec**

```ruby
# spec/requests/devise_overrides/sessions_spec.rb
require 'rails_helper'

RSpec.describe 'POST /auth/sign_in', type: :request do
  let(:account) { create(:account) }
  let(:user) { create(:user, password: 'Password1!') }

  before { create(:account_user, account: account, user: user) }

  it 'allows password login when SSO is not enforced' do
    post '/auth/sign_in', params: { email: user.email, password: 'Password1!' }
    expect(response).to have_http_status(:success)
  end

  it 'rejects password login when the account enforces SSO' do
    create(:saml_settings, account: account, enabled: true, enforce_sso: true)

    post '/auth/sign_in', params: { email: user.email, password: 'Password1!' }
    expect(response).to have_http_status(:unauthorized)
  end
end
```

- [ ] **Step 2: Run it to confirm the second example fails**

Run: `bundle exec rspec spec/requests/devise_overrides/sessions_spec.rb`
Expected: first example passes today, second FAILS (password login currently
always succeeds regardless of `enforce_sso`, since nothing checks it yet).

- [ ] **Step 3: Add the enforcement check**

In `app/controllers/devise_overrides/sessions_controller.rb`, modify
`find_user_for_authentication`:

```ruby
  def find_user_for_authentication
    return nil unless params[:email].present? && params[:password].present?

    normalized_email = params[:email].strip.downcase
    user = User.from_email(normalized_email)
    return nil unless user&.valid_password?(params[:password])
    return nil unless user.active_for_authentication?
    return nil if sso_enforced_for?(user)

    user
  end

  # See Task 9's Interfaces note: enforcement is account-wide per user, not
  # scoped to a single account context, because no account is selected yet
  # at this point in the login flow.
  def sso_enforced_for?(user)
    user.accounts.joins(:saml_settings).merge(SamlSettings.where(enforce_sso: true)).exists?
  end
```

This requires `Account has_one :saml_settings` (added in Task 7, Step 5 —
already in place by this point in the plan) and relies on
`User#accounts` already existing (it does — standard Chatwoot association via
`account_users`).

- [ ] **Step 4: Run the spec, confirm pass**

Run: `bundle exec rspec spec/requests/devise_overrides/sessions_spec.rb`
Expected: PASS (both examples).

- [ ] **Step 5: Fork-patch + commit**

`deploy/chatwoot-fork/patches/0038-enforce-sso-password-block.patch`:
```bash
git add deploy/chatwoot-fork/patches/0038-enforce-sso-password-block.patch
git commit -m "feat(chatwoot-fork): block password login when an account enforces SSO"
```

---

### Task 10: Ops break-glass runbook entry

**Files:**
- Modify: `README.md` (or wherever the existing deploy runbook lives — check
  for a "Troubleshooting"/"Operations" section first; add a new subsection
  there rather than creating a new top-level doc)

**Interfaces:** none (documentation only).

- [ ] **Step 1: Write the runbook entry**

Add a short section:

```markdown
### Break-glass: an account admin is locked out by "Enforce SSO"

If a tenant enables "Require SSO" (`enforce_sso`) with a broken IdP
configuration, password login is blocked for every user on that account and
there is deliberately no in-app bypass (see
`docs/superpowers/specs/2026-08-02-native-saml-sso-security-design.md`,
Security considerations). To recover:

\`\`\`bash
docker compose -p <tenant> exec chatwoot-rails bundle exec rails console
\`\`\`
\`\`\`ruby
SamlSettings.find_by(account_id: <id>).update!(enforce_sso: false)
\`\`\`

Password login is immediately available again; SAML login remains available
throughout (only password login was blocked).
```

- [ ] **Step 2: Commit**

```bash
git add README.md   # or the actual runbook file location found in Step 1
git commit -m "docs: break-glass runbook entry for SAML enforce-SSO lockouts"
```

---

## Self-review notes (already applied above, recorded for the executor)

- **Spec coverage:** every section of the design spec has a task — data
  model (Task 2), login flow (Task 4), SP metadata (Task 3), enforce-SSO
  (Task 9), settings UI + RBAC gating (Tasks 6-8), break-glass (Task 10).
  The one thing the design spec left as "phase-2 implementation detail" (the
  Security page's module-registration mechanism, D3) is resolved concretely
  here: it's just a permission-gated `v-if` inside `Index.vue`, no framework
  needed yet since there's still only one module.
- **New finding not in the original design spec, folded in as Task 1:** the
  Dockerfile only copies `public/vite` + one ERB file into the runtime image;
  every prior patch was frontend-only so this was never exposed. Task 1 fixes
  it and is a hard prerequisite for every other task in this plan.
- **New finding, simplifies Task 4:** `omniauth-saml`/`ruby-saml` are already
  in `Gemfile.lock` (no gem addition needed), and `User` already lists `:saml`
  in `omniauth_providers` with a fully generic (non-enterprise) callback
  controller already in place — the design spec assumed more new plumbing
  than turned out to be necessary.
- **Type/name consistency check:** `SamlSettings.resolve_role` (Task 2) is
  called identically in Task 4's `ensure_account_user`; `security.manage`
  string is identical across Task 6 (backend registry), Task 8 (frontend
  `hasPermission` call and nav gate); `security_saml_settings` resource/route
  name is identical across Task 7 (routes.rb, controller, policy) and Task 8
  (API client, `ApiClient` resource string).
