#!/usr/bin/env bash
set -euo pipefail

wheel_dir=$(mktemp -d)
trap 'rm -rf "$wheel_dir"' EXIT

python -m pip download --quiet --no-deps --only-binary=:all: \
  --cert "${MARKET_DATA_CA_BUNDLE:?Missing market data CA bundle}" \
  --index-url https://opensource.tbank.ru/api/v4/projects/238/packages/pypi/simple \
  --retries 2 --timeout 15 --dest "$wheel_dir" t-tech-investments==1.49.2
python -m pip install --quiet "$wheel_dir"/*.whl
