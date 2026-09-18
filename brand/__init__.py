"""Product naming: single source of truth.

Until the name gate (owner decision), every user-visible product-name string
is the literal placeholder {{PRODUCT_NAME}}. No real name is baked into code,
templates, or configuration. Working-codename references in plan/evidence
documents are descriptive prose, not product naming.
"""

PRODUCT_NAME = "{{PRODUCT_NAME}}"
