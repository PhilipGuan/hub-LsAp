"""无需密钥的测试：真实MCP子进程发现/调用/错误传播以及翻译输入校验。"""
import json
from pathlib import Path
import sys
import unittest

from agents.mcp import MCPServerStdio

from task1_translate import translate
from task2_agent import NEW_TOOLS


class HomeworkTests(unittest.IsolatedAsyncioTestCase):
    async def test_blank_translation_rejected_before_model_call(self):
        with self.assertRaises(ValueError):
            await translate(' \n\t')

    async def test_real_mcp_protocol(self):
        async with MCPServerStdio(
            name='test', params={'command': sys.executable,
                                'args': [str(Path(__file__).with_name('task2_server.py'))]},
        ) as server:
            names = {tool.name for tool in await server.list_tools()}
            self.assertEqual(names, NEW_TOOLS | {'roll_dice', 'get_news'})

            async def call(name, arguments):
                result = await server.call_tool(name, arguments)
                self.assertFalse(result.isError)
                return json.loads(result.content[0].text)

            self.assertEqual((await call('convert_temperature', {
                'value': 25, 'from_unit': 'C', 'to_unit': 'F',
            }))['result'], 77)
            self.assertEqual((await call('convert_temperature', {
                'value': 32, 'from_unit': 'F', 'to_unit': 'C',
            }))['result'], 0)
            self.assertEqual((await call('calculate_bmi', {
                'weight_kg': 70, 'height_cm': 175,
            }))['bmi'], 22.86)
            self.assertEqual(await call('text_statistics', {'text': '你好 OpenAI Agents'}), {
                'characters': 16, 'non_whitespace_characters': 14,
                'chinese_characters': 2, 'english_words': 2, 'lines': 1,
            })
            empty = await call('text_statistics', {'text': ''})
            self.assertTrue(all(value == 0 for value in empty.values()))
            dice = await call('roll_dice', {'n_dice': 100})
            self.assertEqual(len(dice), 100)
            self.assertTrue(all(1 <= point <= 6 for point in dice))
            for name, arguments in [
                ('calculate_bmi', {'weight_kg': 70, 'height_cm': 0}),
                ('convert_temperature', {'value': 20, 'from_unit': 'K', 'to_unit': 'C'}),
                ('convert_temperature', {'value': -274, 'from_unit': 'C', 'to_unit': 'F'}),
                ('roll_dice', {'n_dice': 0}),
            ]:
                with self.subTest(tool=name, arguments=arguments):
                    self.assertTrue((await server.call_tool(name, arguments)).isError)


if __name__ == '__main__':
    unittest.main()
