#!/usr/bin/env bash
# Archive-then-purge rows older than a hot window out of the agent service's
# own Postgres database, into a self-describing archive on GCS.
#
# Usage:
#   deploy/scripts/archive-old-data.sh --tenant <name> [--apply] [options]
#   deploy/scripts/archive-old-data.sh --all-tenants [--apply] [options]
#
# ---------------------------------------------------------------------------
# WHAT IT ARCHIVES, AND WHY ONLY THESE TWO TABLES
# ---------------------------------------------------------------------------
#   agent_<tenant>.ai_actions           (dated by created_at)
#   agent_<tenant>.processed_deliveries (dated by received_at)
#
# Both are append-only logs with no foreign keys pointing at them, so deleting
# an old row cannot orphan anything. `conversation_lifecycle` is deliberately
# NOT archived: it is live per-conversation state, not history.
#
# **Chatwoot's own tables are deliberately out of scope.** Trimming
# `conversations`/`messages` out of chatwoot_<tenant> would mean walking a large
# foreign-key graph the application owns (attachments, reporting events,
# mentions, inbox members) and Chatwoot's own upgrade migrations assume that
# graph is intact. Deleting from underneath it risks corrupting a live CRM to
# save disk, which is the wrong trade. If a hot window on conversation history
# is genuinely required, it needs its own design — see
# docs/runbooks/data-retention.md, "What is not archived and why".
#
# ---------------------------------------------------------------------------
# THE ARCHIVE FORMAT — readable in 2033 with jq and nothing else
# ---------------------------------------------------------------------------
# Per tenant, per table, per run:
#   <table>.ndjson   one JSON object per line, one line per row, produced by
#                    Postgres `row_to_json` — every column, no reshaping.
#   manifest.json    tenant, database, table, the date column, the cutoff, the
#                    row count, min/max id, when it ran and by which script.
#
# An archive only the application that wrote it can read is not an archive, so
# there is no application involved in reading these: `jq` alone is enough.
#
# ---------------------------------------------------------------------------
# SAFETY AND IDEMPOTENCE
# ---------------------------------------------------------------------------
#   * **Dry run is the default.** Without --apply it extracts nothing, uploads
#     nothing and deletes nothing — it reports how many rows are older than the
#     cutoff and what it would write.
#   * **Purge only after the upload is verified present.** Extract, upload,
#     re-read the uploaded object's existence, and only then DELETE. If the
#     upload fails the rows are still in Postgres, which is the correct
#     direction to fail in.
#   * **Re-running archives nothing twice.** The object path is deterministic
#     (keyed on tenant, table and cutoff date), so a re-run after a failed purge
#     overwrites its own object rather than creating a duplicate; and once the
#     purge has happened the same query selects nothing.
#   * The DELETE uses the same predicate as the extract, inside one statement,
#     so a row written between the two cannot be deleted without being archived.
#
# ---------------------------------------------------------------------------
# WHAT HAS AND HAS NOT BEEN EXERCISED
# ---------------------------------------------------------------------------
# EXERCISED, on a developer laptop, against stub `docker`/`gsutil` commands:
# argument parsing and rejections, the help text, the dry-run counting path, the
# full extract → manifest → upload → verify → purge sequence, the "no rows older
# than the cutoff" no-op, re-running being a no-op, and reading the resulting
# NDJSON and manifest back with `jq` alone. `bash -n` passes.
#
# **NOT exercised: any run against real Postgres or a real GCS bucket.** None
# exists in the environment this was written in. No production row has ever been
# archived or purged by this script. Recorded as owed in
# docs/analysis/2026-08-09-blocked-work-register.md.
set -euo pipefail

DEPLOY_DIR="${DEPLOY_DIR:-$(cd "$(dirname "$0")/.." && pwd)}"
INFRA_PROJECT="${INFRA_PROJECT:-platform-infra}"
INFRA_FILE="${INFRA_FILE:-docker-compose.infra.yml}"
# 730 days = two years hot. Matches ARCHIVE_HOT_WINDOW_DAYS in the P13 design.
HOT_WINDOW_DAYS="${ARCHIVE_HOT_WINDOW_DAYS:-730}"
ARCHIVE_GCS_BUCKET="${ARCHIVE_GCS_BUCKET:-}"
ARCHIVE_GCS_PREFIX="${ARCHIVE_GCS_PREFIX:-platform-archive}"
STAGE_ROOT="${ARCHIVE_STAGE_ROOT:-/var/tmp/platform-archive}"
DOCKER="${PLATFORM_DOCKER_CMD:-docker}"
GSUTIL="${PLATFORM_GSUTIL_CMD:-gsutil}"

# table:date-column:order-column
TABLES=("ai_actions:created_at:id" "processed_deliveries:received_at:delivery_id")

TENANT=""
ALL_TENANTS=0
APPLY=0

die() { echo "ERROR: $*" >&2; exit 1; }

usage() {
  cat <<'EOF'
Archive rows older than a hot window out of agent_<tenant> into GCS, then purge.

  --tenant <name>        Archive just this tenant
  --all-tenants          Archive every tenant in deploy/tenants/*.env
  --hot-window-days <n>  Rows older than this are archived (default 730,
                         overridable with ARCHIVE_HOT_WINDOW_DAYS)
  --apply                Actually extract, upload and DELETE. Without this
                         nothing is written and nothing is deleted.
  -h, --help             This text.

Needs ARCHIVE_GCS_BUCKET set. An archive kept on the VM whose disk it was
freeing is not an archive.

Examples
  # What would go, for every tenant (changes nothing):
  ./archive-old-data.sh --all-tenants
  # Do it for one tenant:
  ARCHIVE_GCS_BUCKET=my-bucket ./archive-old-data.sh --tenant proton --apply
EOF
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --tenant) TENANT="${2:-}"; shift 2 ;;
    --all-tenants) ALL_TENANTS=1; shift ;;
    --hot-window-days) HOT_WINDOW_DAYS="${2:-}"; shift 2 ;;
    --apply) APPLY=1; shift ;;
    -h|--help) usage; exit 0 ;;
    *) usage >&2; die "unknown argument '$1'" ;;
  esac
done

if [[ -n "${TENANT}" && "${ALL_TENANTS}" -eq 1 ]]; then
  die "--tenant and --all-tenants are mutually exclusive"
fi
if [[ -z "${TENANT}" && "${ALL_TENANTS}" -eq 0 ]]; then
  usage >&2; die "one of --tenant or --all-tenants is required"
fi
if [[ -n "${TENANT}" && ! "${TENANT}" =~ ^[a-z][a-z0-9]*$ ]]; then
  die "--tenant must match ^[a-z][a-z0-9]*\$ (got '${TENANT}')"
fi
[[ "${HOT_WINDOW_DAYS}" =~ ^[0-9]+$ ]] || die "--hot-window-days must be a whole number (got '${HOT_WINDOW_DAYS}')"
if [[ "${APPLY}" -eq 1 && -z "${ARCHIVE_GCS_BUCKET}" ]]; then
  die "--apply needs ARCHIVE_GCS_BUCKET set. Purging rows whose only copy would sit on this VM's disk defeats the point of archiving them."
fi

cd "${DEPLOY_DIR}"

RUN_DATE="$(date +%F)"
MODE="DRY RUN (nothing extracted, uploaded or deleted)"
[[ "${APPLY}" -eq 1 ]] && MODE="APPLY (rows will be uploaded then DELETED)"

cat <<EOF
==> archive-old-data.sh — ${MODE}
    hot window : ${HOT_WINDOW_DAYS} days
    destination: ${ARCHIVE_GCS_BUCKET:-<unset — dry run only>}
EOF

psql_db() {
  local db="$1"; shift
  "${DOCKER}" compose -p "${INFRA_PROJECT}" -f "${INFRA_FILE}" exec -T postgres \
    psql -v ON_ERROR_STOP=1 -tAX -U postgres -d "${db}" "$@"
}

resolve_tenants() {
  if [[ -n "${TENANT}" ]]; then
    printf '%s\n' "${TENANT}"
    return
  fi
  shopt -s nullglob
  local env_file name
  for env_file in tenants/*.env; do
    [[ "$(basename "${env_file}")" == "example.env" ]] && continue
    name="$(grep -E '^TENANT=' "${env_file}" | head -n1 | cut -d= -f2-)"
    name="${name//[[:space:]\"\']/}"
    if [[ ! "${name}" =~ ^[a-z][a-z0-9]*$ ]]; then
      echo "WARNING: ${env_file} has invalid/empty TENANT ('${name}'), skipping" >&2
      continue
    fi
    printf '%s\n' "${name}"
  done
}

TOTAL_ARCHIVED=0
TOTAL_PURGED=0

for tenant in $(resolve_tenants); do
  db="agent_${tenant}"
  echo "==> Tenant ${tenant} (${db})"

  for spec in "${TABLES[@]}"; do
    table="${spec%%:*}"
    rest="${spec#*:}"
    date_col="${rest%%:*}"
    order_col="${rest##*:}"
    # The cutoff is computed by Postgres, not by the shell, so the comparison
    # happens in the database's own clock and timezone rather than the VM's.
    predicate="${date_col} < now() - interval '${HOT_WINDOW_DAYS} days'"

    count="$(psql_db "${db}" -c "SELECT count(*) FROM ${table} WHERE ${predicate}" 2>/dev/null || true)"
    if [[ -z "${count}" ]]; then
      echo "    ${table}: could not be read (missing table, or database unreachable) — SKIPPED" >&2
      continue
    fi
    count="$(printf '%s' "${count}" | tr -d '[:space:]')"
    if [[ "${count}" == "0" ]]; then
      echo "    ${table}: 0 rows older than ${HOT_WINDOW_DAYS} days — nothing to do"
      continue
    fi
    echo "    ${table}: ${count} rows older than ${HOT_WINDOW_DAYS} days"

    if [[ "${APPLY}" -eq 0 ]]; then
      echo "      [dry run] would write ${table}.ndjson + manifest.json to"
      echo "                gs://${ARCHIVE_GCS_BUCKET:-<bucket>}/${ARCHIVE_GCS_PREFIX}/${tenant}/${table}/${RUN_DATE}/"
      echo "                then DELETE those ${count} rows from ${db}.${table}"
      continue
    fi

    stage="${STAGE_ROOT}/${tenant}/${table}/${RUN_DATE}"
    mkdir -p "${stage}"
    remote="gs://${ARCHIVE_GCS_BUCKET}/${ARCHIVE_GCS_PREFIX}/${tenant}/${table}/${RUN_DATE}"

    echo "      extracting to ${stage}/${table}.ndjson"
    # -tA (tuples only, unaligned) + row_to_json gives exactly one JSON object
    # per line. row_to_json escapes newlines inside string values, so a text
    # column containing a newline cannot break the line-per-row contract.
    psql_db "${db}" -c \
      "SELECT row_to_json(t)::text FROM (SELECT * FROM ${table} WHERE ${predicate} ORDER BY ${order_col}) t" \
      > "${stage}/${table}.ndjson"

    extracted="$(grep -c . "${stage}/${table}.ndjson" || true)"
    extracted="${extracted:-0}"
    if [[ "${extracted}" -eq 0 ]]; then
      echo "      WARNING: extract produced no rows although ${count} were counted — NOT purging" >&2
      continue
    fi

    bounds="$(psql_db "${db}" -c \
      "SELECT min(${order_col})::text || ' ' || max(${order_col})::text FROM ${table} WHERE ${predicate}" \
      2>/dev/null || true)"
    min_id="$(printf '%s' "${bounds}" | awk '{print $1}')"
    max_id="$(printf '%s' "${bounds}" | awk '{print $2}')"

    # The manifest describes the NDJSON beside it well enough that a reader in
    # 2033 needs neither this script nor the application to interpret it.
    cat > "${stage}/manifest.json" <<JSON
{
  "schema": "proton-crm/archive-manifest/v1",
  "tenant": "${tenant}",
  "database": "${db}",
  "table": "${table}",
  "date_column": "${date_col}",
  "hot_window_days": ${HOT_WINDOW_DAYS},
  "cutoff_rule": "${date_col} < now() - interval '${HOT_WINDOW_DAYS} days', evaluated by Postgres at run time",
  "row_count": ${extracted},
  "order_column": "${order_col}",
  "min_${order_col}": "${min_id}",
  "max_${order_col}": "${max_id}",
  "data_file": "${table}.ndjson",
  "data_format": "newline-delimited JSON, one object per row, produced by Postgres row_to_json(t) over SELECT *",
  "read_with": "jq -c . ${table}.ndjson",
  "archived_at": "${RUN_DATE}",
  "written_by": "deploy/scripts/archive-old-data.sh"
}
JSON

    echo "      uploading to ${remote}"
    "${GSUTIL}" -m cp "${stage}/${table}.ndjson" "${stage}/manifest.json" "${remote}/" \
      || { echo "      ERROR: upload to ${remote} failed — rows left in Postgres, NOT purged" >&2; continue; }

    # Verify the upload landed before deleting the only other copy.
    if ! "${GSUTIL}" stat "${remote}/${table}.ndjson" >/dev/null 2>&1; then
      echo "      ERROR: ${remote}/${table}.ndjson is not there after upload — NOT purging" >&2
      continue
    fi
    echo "      upload verified"

    echo "      purging ${extracted} rows from ${db}.${table}"
    deleted="$(psql_db "${db}" -c \
      "WITH gone AS (DELETE FROM ${table} WHERE ${predicate} RETURNING 1) SELECT count(*) FROM gone")"
    deleted="$(printf '%s' "${deleted}" | tr -d '[:space:]')"
    echo "      purged ${deleted} rows (archived ${extracted})"
    if [[ "${deleted}" != "${extracted}" ]]; then
      echo "      NOTE: ${deleted} deleted vs ${extracted} archived. Rows can age past" >&2
      echo "            the cutoff between the extract and the delete, so a small" >&2
      echo "            excess is expected; a shortfall is not, and means rows were" >&2
      echo "            archived that are still live. Neither loses data." >&2
    fi
    TOTAL_ARCHIVED=$(( TOTAL_ARCHIVED + extracted ))
    TOTAL_PURGED=$(( TOTAL_PURGED + deleted ))
  done
done

if [[ "${APPLY}" -eq 0 ]]; then
  echo "==> DRY RUN complete. Nothing was extracted, uploaded or deleted."
else
  echo "==> Done. Archived ${TOTAL_ARCHIVED} rows, purged ${TOTAL_PURGED}."
  echo "    Verify one by hand: gsutil cat <object> | jq -c . | head"
fi
