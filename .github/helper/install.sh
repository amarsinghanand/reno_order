#!/bin/bash
# CI-only bench. Test DB passwords live in this job, not in the app repo.
set -euo pipefail

FRAPPE_BRANCH="${FRAPPE_BRANCH:-version-16}"
ERPNEXT_BRANCH="${ERPNEXT_BRANCH:-version-16}"
HRMS_BRANCH="${HRMS_BRANCH:-version-16}"

sudo apt-get update -y
sudo apt-get install -y --no-install-recommends redis-server mariadb-client libmariadb-dev

python -m pip install --upgrade pip
pip install frappe-bench

echo "127.0.0.1 test_site" | sudo tee -a /etc/hosts

bench init ~/frappe-bench \
	--skip-assets \
	--skip-redis-config-generation \
	--frappe-branch "${FRAPPE_BRANCH}" \
	--python "$(command -v python)"

cd ~/frappe-bench

bench set-config -g db_host 127.0.0.1
bench set-config -g redis_cache redis://127.0.0.1:6379
bench set-config -g redis_queue redis://127.0.0.1:6379
bench set-config -g redis_socketio redis://127.0.0.1:6379

bench get-app erpnext --branch "${ERPNEXT_BRANCH}" --resolve-deps
bench get-app hrms --branch "${HRMS_BRANCH}"
bench get-app reno_order "${GITHUB_WORKSPACE}"

bench new-site test_site \
	--db-root-password root \
	--admin-password admin \
	--no-mariadb-socket \
	--install-app erpnext

bench --site test_site install-app hrms
bench --site test_site install-app reno_order
