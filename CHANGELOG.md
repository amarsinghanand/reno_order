# Changelog

All notable changes to the Reno Order app are documented here.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project uses [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

## [1.0.0] - 2026-09-23

First release of the kitchen-renovation custom layer for ERPNext / Frappe v16.

### Added

- `Reno Order` DocType with server-side totals, discount approval, workflow, and role-based row filters
- Links to standard Sales Order, Delivery Note, Sales Invoice, Work Order, Material Request, and buying documents
- Site Supervisor REST API (status, remarks, private site photos) using Frappe token auth
- CRM sync on the `long` queue with retries, Integration Request audit, and HMAC webhook
- Idempotent `order_type` backfill patch and covering index for the monthly value report
- Leave Type `is_event_based` flag and `Leave Policy Assignment` class override so event leave is not prorated
- Duplicate-safe Installed processing (`RDN-<reno_order>`, row lock, idempotent Mark as Installed)

### Security

- CRM API token and webhook secret are Password fields; they are generated on the site and are not in this repository
- Permission checks stay on; Site Supervisor selling fields are locked at permlevel 1
