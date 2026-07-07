#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
ENV_FILE="${ROOT_DIR}/.env"

if [[ ! -f "${ENV_FILE}" ]]; then
  echo "Missing ${ENV_FILE}"
  exit 1
fi

set -a
source "${ENV_FILE}"
set +a

required_vars=(
  MAIL_USERNAME
  MAIL_PASSWORD
  MAIL_IMAP_HOST
  MAIL_IMAP_PORT
  MAIL_IMAP_FOLDER
  MAIL_SMTP_HOST
  MAIL_SMTP_PORT
)

for var in "${required_vars[@]}"; do
  if [[ -z "${!var:-}" ]]; then
    echo "Missing required variable: ${var}"
    exit 1
  fi
done

unset http_proxy https_proxy HTTP_PROXY HTTPS_PROXY all_proxy ALL_PROXY

echo "Testing IMAP login: ${MAIL_USERNAME} -> ${MAIL_IMAP_HOST}:${MAIL_IMAP_PORT}/${MAIL_IMAP_FOLDER}"
if curl --fail --silent --show-error \
  --connect-timeout 15 \
  --max-time 30 \
  --url "imaps://${MAIL_IMAP_HOST}:${MAIL_IMAP_PORT}/${MAIL_IMAP_FOLDER}" \
  --user "${MAIL_USERNAME}:${MAIL_PASSWORD}" \
  --request "NOOP" \
  --output /dev/null; then
  echo "IMAP login ok"
else
  echo "IMAP login failed"
  imap_failed=1
fi

echo
echo "Testing SMTP auth: ${MAIL_USERNAME} -> ${MAIL_SMTP_HOST}:${MAIL_SMTP_PORT}"
smtp_scheme="smtp"
smtp_ssl_flag=()
if [[ "${MAIL_SMTP_SSL:-false}" == "true" ]]; then
  smtp_scheme="smtps"
else
  smtp_ssl_flag=(--ssl-reqd)
fi

if curl --fail --silent --show-error \
  --connect-timeout 15 \
  --max-time 30 \
  --url "${smtp_scheme}://${MAIL_SMTP_HOST}:${MAIL_SMTP_PORT}" \
  "${smtp_ssl_flag[@]}" \
  --login-options "AUTH=${MAIL_SMTP_AUTH_MECHANISMS:-LOGIN}" \
  --user "${MAIL_USERNAME}:${MAIL_PASSWORD}" \
  --request "NOOP" \
  --output /dev/null; then
  echo "SMTP auth ok"
else
  echo "SMTP auth failed"
  smtp_failed=1
fi

if [[ "${imap_failed:-0}" == "1" || "${smtp_failed:-0}" == "1" ]]; then
  exit 1
fi
