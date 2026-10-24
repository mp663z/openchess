#!/bin/bash
# req.sh TASK TITLE MOD REDTASK REFTASK CONTRACTFILE ERR FN REQUIREMENTS_TEXT_FILE MUT_TEXT
T=$1; MOD=$2; RED=$3; REF=$4; ERR=$5; FN=$6
cat > /home/sandbox/openchess/requirements/tasks/$T.md <<EOT
# $T $7

## Normative requirements

$(cat $8)

## Requirement-to-test mapping

- R1: \`test_r2_production_imports_no_test_code\`, \`test_r3_public_surface\`.
- R2, R3: \`test_r4_every_fixture_request_agrees_with_the_reference\` and \`test_r5_production_and_reference_agree_on_hostile_variants\` (every fixture request, and every field and nested field replaced by junk, dropped or given a sibling: same record or same failure class and code as the $REF reference, request unchanged, no cause or context), \`test_r5_hostile_subclasses_fail_closed_without_running_caller_code\`, plus the full $RED battery bound to \`server.$MOD\`.
- R4: \`test_r1_red_battery_switch_is_exactly_the_two_binding_lines\` (switched back, the file hashes to the $RED battery as landed; the oracle import stays and carries one extra F401 noqa).

## Mutation check

$9

## Red/green transition

On main, switching the two $RED binding lines to \`server.$MOD\` fails at import because the module does not exist. With this change the same switch passes the whole $RED battery.
EOT
