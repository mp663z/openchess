# Naming policy (T0003, v2)

Until the name gate:
1. The only product name in code, configuration, templates, and user-visible
   surfaces (README, CLI output, manifests) is the literal placeholder
   `{{PRODUCT_NAME}}`, sourced from `brand.PRODUCT_NAME`.
2. Explicit exceptions (infrastructure identifiers and verbatim source
   documents, never user-visible product naming):
   - `pyproject.toml` `[project].name = "openchess"`: Python distribution
     slug; a placeholder is not a valid package name.
   - Repository URLs (`github.com/mp663z/openchess`): factual infrastructure
     addresses.
   - `docs/plan/development-plan-v9.md`, `docs/plan/product-report-v5.md`:
     verbatim source documents of record; editing them would falsify history.
3. `tests/test_naming.py` enforces this with a case-insensitive scan across
   ALL tracked text files (code, Markdown, YAML, JSON, TOML) against the
   explicit allowlist above - no directory-wide exemptions.
4. The name gate is an owner decision; adopting a real name is one change to
   `brand/__init__.py` plus user-visible surfaces, then a scan update.
