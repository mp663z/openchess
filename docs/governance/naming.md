# Naming policy (T0003)

Until the name gate:
1. The only product name in code, templates, configuration, and user-visible
   strings is the literal placeholder `{{PRODUCT_NAME}}`, imported from
   `brand.PRODUCT_NAME`. Never hardcode a real name.
2. Plan documents (`docs/plan/`), evidence files, and prose discussion may
   reference the working codename descriptively; they are not user-visible
   product naming.
3. A repository scan test (`tests/test_naming.py`) enforces both rules and
   fails CI on any hardcoded alternative.
4. The name gate is an owner decision; when a real name is chosen, it lands
   as a single change to `brand/__init__.py` plus a scan-test update.
