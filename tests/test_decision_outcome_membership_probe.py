import inspect
import unittest

import decision_outcome_membership_probe as probe


class DecisionOutcomeMembershipProbeTests(unittest.TestCase):
    def test_default_off_emits_no_sql(self):
        result = probe.build()
        self.assertEqual(result["mode"], "disabled")
        self.assertIsNone(result["sql"])

    def test_fixed_probe_is_valid_and_deterministic(self):
        first = probe.build(enabled=True)
        second = probe.build(enabled=True)
        self.assertEqual(first, second)
        self.assertTrue(first["ready"])
        self.assertTrue(probe.validate(first["sql"], enabled=True)["valid"])
        self.assertEqual(first["sqlHash"], probe.validate(first["sql"], enabled=True)["sqlHash"])

    def test_probe_is_intentional_exception_and_rollback_only(self):
        sql = probe.build(enabled=True)["sql"].lower()
        self.assertIn("raise exception 'membership_probe_result:%'", sql)
        self.assertEqual(sql.count("begin;"), 1)
        self.assertEqual(sql.count("rollback;"), 1)
        self.assertTrue(sql.rstrip().endswith("rollback;"))
        self.assertNotIn("commit;", sql)

    def test_probe_removes_creator_edges_before_reading_catalog(self):
        sql = probe.build(enabled=True)["sql"].lower()
        catalog = sql.index("from pg_catalog.pg_auth_members")
        for role in (probe.ROLE_OWNER, probe.ROLE_WRITER):
            revoke = f"revoke {role} from postgres;"
            self.assertIn(revoke, sql)
            self.assertLess(sql.index(revoke), catalog)

    def test_probe_uses_sql_coalesce_and_explicit_option_allowlist(self):
        sql = probe.build(enabled=True)["sql"]
        lowered = sql.lower()
        self.assertIn("select coalesce(", lowered)
        self.assertNotIn("pg_catalog.coalesce", lowered)
        self.assertNotIn("to_jsonb(m)", lowered)
        expected = (
            ("adminOption", "admin_option"),
            ("inheritOption", "inherit_option"),
            ("setOption", "set_option"),
        )
        for key, source in expected:
            self.assertEqual(sql.count(f"'{key}', m.{source}"), 1)
        for forbidden_key in ("'oid'", "'roleid'", "'member'", "'grantor'"):
            self.assertNotIn(forbidden_key, lowered)

    def test_empty_membership_has_deterministic_empty_json_array(self):
        sql = probe.build(enabled=True)["sql"].lower()
        self.assertIn("), '[]'::jsonb", sql)
        self.assertIn(
            "order by granted_role.rolname, member_role.rolname, grantor_role.rolname",
            sql,
        )

    def test_probe_has_no_migration_rpc_schema_table_or_data_access(self):
        sql = probe.build(enabled=True)["sql"].lower()
        for forbidden in ("create schema", "create table", "create function", "insert into",
                          "update ", "delete from", "truncate ", "append_decision_outcome",
                          "investment_decision_shadow", "service_role_key", "https://"):
            self.assertNotIn(forbidden, sql)

    def test_mutations_fail_static_validation(self):
        sql = probe.build(enabled=True)["sql"]
        for changed in (
            sql.replace("rollback;", "commit;"),
            sql.replace("revoke decision_outcome_owner from postgres;", ""),
            sql.replace("from pg_catalog.pg_auth_members", "from public.secret_rows"),
            sql.replace("select coalesce(", "select pg_catalog.coalesce("),
            sql.replace("'adminOption', m.admin_option", "'oid', m.admin_option"),
            sql.replace("'inheritOption', m.inherit_option", "'inheritOption', null"),
            sql.replace("'setOption', m.set_option", "'set_option', m.set_option"),
            sql.replace(
                "'options', pg_catalog.jsonb_build_object(\n"
                "          'adminOption', m.admin_option,\n"
                "          'inheritOption', m.inherit_option,\n"
                "          'setOption', m.set_option\n"
                "        )",
                "'options', pg_catalog.to_jsonb(m)",
            ),
            sql + "\ncreate table x(y int);",
        ):
            self.assertFalse(probe.validate(changed, enabled=True)["valid"])

    def test_module_has_no_io_network_env_db_client_or_formal_flow(self):
        source = inspect.getsource(probe).lower()
        for forbidden in ("open(", "pathlib", "requests", "urllib", "socket", "subprocess",
                          "psycopg", "\nimport supabase", "\nfrom supabase", "os.environ",
                          "getenv(", "\nimport candidate_manifest", "\nfrom candidate_manifest",
                          "\nimport strategy_tracker", "\nfrom strategy_tracker",
                          "\nimport promotion_status", "\nfrom promotion_status",
                          "\nimport telegram", "\nfrom telegram", "\nimport investment_advice",
                          "\nfrom investment_advice", "execute("):
            self.assertNotIn(forbidden, source)


if __name__ == "__main__":
    unittest.main()
