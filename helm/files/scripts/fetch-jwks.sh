#!/bin/sh
set -eu

: "${JWKS_URI:?JWKS_URI is required}"
: "${CURL_VERSION:?CURL_VERSION is required}"
: "${JQ_VERSION:?JQ_VERSION is required}"

jwks_file=/etc/postgrest/jwks.json
temporary_file="${jwks_file}.tmp"

rm -f "$temporary_file"
trap 'rm -f "${temporary_file}"' EXIT

apk add --no-cache \
    "curl=${CURL_VERSION}" \
    "jq=${JQ_VERSION}"

curl \
    --fail \
    --silent \
    --show-error \
    --retry 5 \
    --retry-all-errors \
    --retry-delay 2 \
    --retry-max-time 120 \
    --connect-timeout 5 \
    --max-time 30 \
    "$JWKS_URI" \
    --output "$temporary_file"

jq -e '
  def nonempty_string:
    type == "string" and length > 0;

  .keys
  | type == "array"
  and length > 0
  and all(.[];
    (.kid | nonempty_string)
    and (
      (
        .kty == "RSA"
        and (.n | nonempty_string)
        and (.e | nonempty_string)
      )
      or
      (
        .kty == "EC"
        and (.crv | nonempty_string)
        and (.x | nonempty_string)
        and (.y | nonempty_string)
      )
      or
      (
        .kty == "OKP"
        and (.crv | nonempty_string)
        and (.x | nonempty_string)
      )
    )
  )
' "$temporary_file"

mv "$temporary_file" "$jwks_file"
trap - EXIT
