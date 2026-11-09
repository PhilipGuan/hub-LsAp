import hashlib
import unittest
from sql_agent import DBParser, DemoModel, SQLWorkflow, CASES, DEFAULT_DB


class WorkflowTests(unittest.TestCase):
    def setUp(self):
        self.db = DBParser()

    def test_schema(self):
        self.assertEqual(len(self.db.schema), 11)
        self.assertEqual(self.db.schema['customers']['row_count'], 59)
        self.assertTrue(self.db.schema['invoice_items']['foreign_keys'])

    def test_known_results(self):
        workflow = SQLWorkflow(self.db, DemoModel())
        results = [workflow.run(c['question']) for c in CASES]
        self.assertTrue(all(r['status'] == 'success' for r in results))
        self.assertEqual(results[0]['result']['rows'][0], ['USA', 523.06])
        self.assertEqual(results[1]['result']['rows'][0], ['Rock', 826.65])
        self.assertEqual(len(results[2]['result']['rows']), 12)
        self.assertEqual(results[3]['result']['rows'], [[59]])
        self.assertEqual(results[4]['result']['rows'], [])

    def test_rewrite(self):
        result = SQLWorkflow(self.db, DemoModel(True)).run(CASES[0]['question'])
        self.assertEqual(result['status'], 'success')
        stages = [r['stage'] for r in result['trace']]
        self.assertIn('sql_rewrite', stages)
        self.assertEqual(stages.count('sql_validate'), 2)

    def test_readonly_and_selection(self):
        before = hashlib.sha256(DEFAULT_DB.read_bytes()).digest()
        for sql in ['DELETE FROM customers', 'DROP TABLE customers',
                    'SELECT 1; DELETE FROM customers',
                    "ATTACH DATABASE 'x.db' AS x", "SELECT load_extension('x')",
                    'SELECT * FROM employees', 'SELECT * FROM sqlite_master',
                    'WITH x AS (SELECT 1) DELETE FROM customers']:
            with self.subTest(sql=sql):
                self.assertFalse(self.db.check_sql(sql, ['customers'])['ok'])
        self.assertEqual(before, hashlib.sha256(DEFAULT_DB.read_bytes()).digest())

    def test_truncation(self):
        result = DBParser(max_rows=2).query('SELECT CustomerId FROM customers ORDER BY CustomerId', ['customers'])
        self.assertEqual(result['rows'], [[1], [2]])
        self.assertTrue(result['truncated'])

    def test_timeout(self):
        sql = 'WITH RECURSIVE n(x) AS (SELECT 1 UNION ALL SELECT x+1 FROM n) SELECT SUM(x) FROM n'
        with self.assertRaises(Exception):
            DBParser(timeout=0.01).query(sql, ['customers'])

    def test_retry_bound(self):
        class Broken(DemoModel):
            def ask(self, stage, payload):
                return super().ask(stage, payload) if stage == 'select' else {'sql': 'SELECT Missing FROM invoices'}
        result = SQLWorkflow(self.db, Broken(), max_rewrites=2).run(CASES[0]['question'])
        self.assertEqual(result['status'], 'failed')
        self.assertEqual(sum(x['stage'] == 'sql_rewrite' for x in result['trace']), 2)

    def test_invalid_selection(self):
        class Invalid(DemoModel):
            def ask(self, stage, payload):
                return {'tables': ['invented']}
        self.assertEqual(SQLWorkflow(self.db, Invalid()).run('问题')['status'], 'failed')

    def test_unknown_demo_question(self):
        self.assertEqual(SQLWorkflow(self.db, DemoModel()).run('任意问题')['status'], 'failed')


if __name__ == '__main__':
    unittest.main(verbosity=2)
