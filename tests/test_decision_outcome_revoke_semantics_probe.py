import inspect
import unittest

import decision_outcome_revoke_semantics_probe as probe


class DecisionOutcomeRevokeSemanticsProbeTests(unittest.TestCase):
    def test_default_off_emits_no_sql(self):
        result = probe.build()
        self.assertEqual(result["mode"], "disabled")
        self.assertIsNone(result["sql"])

    def test_probe_is_deterministic_and_valid(self):
        first = probe.build(enabled=True)
        second = probe.build(enabled=True)
        self.assertEqual(first, second)
        self.assertTrue(first["ready"], first["blockers"])
        checked = probe.validate(first["sql"], enabled=True)
        self.assertTrue(checked["valid"], checked["blockers"])
        self.assertFalse(first["productionApproved"])
        self.assertFalse(first["strategyValidated"])
        self.assertTrue(first["diagnosticOnly"])
        self.assertTrue(first["researchOnly"])
        self.assertFalse(first["promotionEligible"])
        self.assertFalse(first["pitCertified"])

    def test_probe_is_one_transaction_intentional_exception_and_rollback_only(self):
        sql = probe.build(enabled=True)["sql"].lower()
        self.assertEqual(sql.count("begin;"), 1)
        self.assertEqual(sql.count("rollback;"), 1)
        self.assertEqual(sql.count("commit;"), 0)
        self.assertTrue(sql.rstrip().endswith("rollback;"))
        self.assertIn("raise exception 'revoke_semantics_probe_result:%'", sql)

    def test_creator_edge_expectation_is_exact_and_fail_closed(self):
        sql = probe.build(enabled=True)["sql"].lower()
        for expected in (
            "member_role.rolname = 'postgres'",
            "grantor_role.rolname = 'supabase_admin'",
            "and m.admin_option",
            "and not m.inherit_option",
            "and not m.set_option",
            "if scoped_count <> 2 or exact_count <> 2",
            "observed_summary->0->>'grantedrole' <> 'decision_outcome_owner'",
            "observed_summary->1->>'grantedrole' <> 'decision_outcome_writer'",
        ):
            self.assertIn(expected, sql)

    def test_exact_grantor_revokes_are_after_observation_and_before_zero_check(self):
        sql = probe.build(enabled=True)["sql"].lower()
        first_catalog = sql.index("from pg_catalog.pg_auth_members")
        second_catalog = sql.index("from pg_catalog.pg_auth_members", first_catalog + 1)
        owner = (
            "revoke decision_outcome_owner from postgres\n"
            "    granted by supabase_admin restrict;"
        )
        writer = (
            "revoke decision_outcome_writer from postgres\n"
            "    granted by supabase_admin restrict;"
        )
        for statement in (owner, writer):
            self.assertIn(statement, sql)
            self.assertGreater(sql.index(statement), first_catalog)
            self.assertLess(sql.index(statement), second_catalog)
        self.assertNotIn(" cascade", sql)
        self.assertNotIn("set role", sql)

    def test_cleanup_requires_zero_scoped_memberships(self):
        sql = probe.build(enabled=True)["sql"].lower()
        self.assertIn("select pg_catalog.count(*) into remaining_count", sql)
        self.assertIn("if remaining_count <> 0", sql)
        self.assertIn("revoke_semantics_probe_cleanup_incomplete", sql)

    def test_output_is_explicit_allowlist_without_catalog_ids(self):
        sql = probe.build(enabled=True)["sql"]
        lowered = sql.lower()
        for key, source in (
            ("adminOption", "admin_option"),
            ("inheritOption", "inherit_option"),
            ("setOption", "set_option"),
        ):
            self.assertEqual(sql.count(f"'{key}', m.{source}"), 1)
        for forbidden in ("to_jsonb(m)", "'oid'", "'roleid'", "'member'", "'grantor'"):
            self.assertNotIn(forbidden, lowered)

    def test_mutations_fail_static_validation(self):
        sql = probe.build(enabled=True)["sql"]
        mutations = (
            sql.replace("rollback;", "commit;"),
            sql.replace("set local idle_in_transaction_session_timeout = '15s';", ""),
            sql.replace("if current_user <> 'postgres' then", "if false then"),
            sql.replace(
                "if pg_catalog.to_regrole('decision_outcome_owner') is not null",
                "if false",
            ),
            sql.replace("granted by supabase_admin restrict;", "restrict;", 1),
            sql.replace("granted by supabase_admin restrict;", "granted by postgres restrict;", 1),
            sql.replace("revoke decision_outcome_owner", "revoke decision_outcome_writer", 1),
            sql.replace("if scoped_count <> 2 or exact_count <> 2", "if scoped_count < 99"),
            sql.replace(
                "or observed_summary->0->>'grantedRole' <> 'decision_outcome_owner'",
                "",
            ),
            sql.replace(
                "or observed_summary->1->>'grantedRole' <> 'decision_outcome_writer'",
                "",
            ),
            sql.replace("nocreatedb", "createdb", 1),
            sql.replace("nocreaterole", "createrole", 1),
            sql.replace("noreplication", "replication", 1),
            sql.replace("nobypassrls", "bypassrls", 1),
            sql.replace("and m.admin_option", "and not m.admin_option"),
            sql.replace("and not m.inherit_option", "and m.inherit_option"),
            sql.replace("and not m.set_option", "and m.set_option"),
            sql.replace("if remaining_count <> 0", "if remaining_count < 0"),
            sql.replace("'setOption', m.set_option", "'set_option', m.set_option"),
            sql.replace("coalesce(", "pg_catalog.coalesce(", 1),
            sql.replace(
                "from pg_catalog.pg_auth_members m",
                "from pg_catalog.pg_auth_members m join pg_catalog.pg_roles extra on true",
                1,
            ),
            sql.replace(
                "create role decision_outcome_writer",
                "create role extra_role nologin;\n  create role decision_outcome_writer",
                1,
            ),
            sql + "\nset role supabase_admin;",
            sql + "\ngrant decision_outcome_owner to postgres;",
            sql + "\nalter role decision_outcome_owner login;",
            sql + "\ndrop role decision_outcome_owner;",
            sql + "\nrevoke decision_outcome_owner from postgres cascade;",
            sql + "\ncreate table x(y int);",
            sql + "\nselect * from public.app_rows;",
            sql.replace("\nrollback;", "\ncreate database x;\nrollback;"),
            sql.replace("\nrollback;", "\ndrop database x;\nrollback;"),
            sql.replace(
                "\nrollback;",
                "\ncreate procedure p() language sql as 'select 1';\nrollback;",
            ),
            sql.replace("\nrollback;", "\ncomment on role postgres is 'x';\nrollback;"),
            sql.replace(
                "\nrollback;",
                "\nsecurity label on role postgres is 'x';\nrollback;",
            ),
            sql.replace(
                "\nrollback;",
                "\nalter system set application_name = 'x';\nrollback;",
            ),
        )
        for index, changed in enumerate(mutations):
            with self.subTest(mutation=index, change=changed[-80:]):
                self.assertFalse(
                    probe.validate(changed, enabled=True)["valid"],
                    f"unsafe mutation {index} passed static validation",
                )

    def test_module_has_no_io_network_env_db_client_or_formal_flow(self):
        source = inspect.getsource(probe).lower()
        for forbidden in (
            "open(", "pathlib", "requests", "urllib", "socket", "subprocess",
            "psycopg", "\nimport supabase", "\nfrom supabase", "os.environ",
            "getenv(", "execute(", "\nimport candidate_manifest",
            "\nfrom candidate_manifest", "\nimport strategy_tracker",
            "\nfrom strategy_tracker", "\nimport promotion_status",
            "\nfrom promotion_status", "\nimport telegram", "\nfrom telegram",
            "\nimport investment_advice", "\nfrom investment_advice",
        ):
            self.assertNotIn(forbidden, source)


if __name__ == "__main__":
    unittest.main()
