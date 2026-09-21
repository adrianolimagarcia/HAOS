#!/usr/bin/env bash
set -euo pipefail
hook="distro/haos-linux/config/hooks/live/40-setup-rust-edge.chroot"
health="distro/haos-linux/config/includes.chroot/usr/local/libexec/hermes-exec-healthcheck"
bash -n "$hook" "$health"
grep -q 'manifest.tsv' "$hook"
grep -q 'sha256sum' "$hook"
grep -q 'readelf' "$hook"
grep -q 'timeout 5s' "$health"
printf '%s\n' 'HAOS artifact hook and healthcheck static validation passed'
