import unittest

from harness.agent import (
    Agent,
    builtin_tool_prompt,
    native_tool_definitions,
    parse_tool_call,
)
from tests.support import DummyBoard, DummyConfig


class StubLLM:
    supports_native_tools = False

    def complete(self, messages, **kwargs):
        return "ok"

    complete_stream = complete


class FakeRunner:
    def __init__(self):
        self.seq = 0
        self.tasks = {}
        self.calls = []

    def schedule_views(self):
        return [dict(task) for task in self.tasks.values()]

    def schedule_summary_lines(self):
        return ["Scheduled tasks: %s." % len(self.tasks)]

    def schedule_view(self, task_id, full=False):
        task = self.tasks.get(int(task_id))
        return dict(task) if task is not None else None

    def schedule_text(self, task_id, full=False):
        task = self.tasks.get(int(task_id))
        if task is None:
            return None
        return "id: %s\nname: %s\ntype: %s" % (
            task["id"], task.get("name", ""), task.get("type", ""))

    def create_schedule(self, fields):
        self.calls.append(("create", fields))
        self.seq += 1
        task = {
            "id": self.seq,
            "name": fields.get("name", "task"),
            "type": fields.get("type", "prompt"),
            "enabled": fields.get("enabled", True),
            "when": fields.get("when", "once"),
        }
        self.tasks[self.seq] = task
        return dict(task)

    def update_schedule(self, task_id, fields):
        self.calls.append(("update", task_id, fields))
        task = self.tasks[int(task_id)]
        task.update(fields)
        return dict(task)

    def delete_schedule(self, task_id):
        self.calls.append(("delete", int(task_id)))
        return dict(self.tasks.pop(int(task_id)))

    def set_schedule_enabled(self, task_id, enabled):
        self.calls.append(("enable", int(task_id), enabled))
        task = self.tasks[int(task_id)]
        task["enabled"] = bool(enabled)
        return dict(task)

    def request_schedule_run(self, task_id):
        self.calls.append(("run", int(task_id)))
        return dict(self.tasks[int(task_id)])


class FakeExecutor:
    def __init__(self, runner):
        self.main_runner = runner


class ScheduleToolTests(unittest.TestCase):
    def make_agent(self, runner=None, **overrides):
        config = DummyConfig(**overrides)
        runner = runner or FakeRunner()
        return Agent(
            config, StubLLM(), DummyBoard(),
            executor=FakeExecutor(runner)), runner

    def call(self, agent, **args):
        return agent._execute_tool({"name": "__schedule", "args": args})

    def test_tool_is_exposed_in_native_and_text_protocols(self):
        names = [
            tool["name"]
            for tool in native_tool_definitions(DummyConfig())
        ]
        self.assertIn("__schedule", names)
        prompt = builtin_tool_prompt(DummyConfig())
        self.assertIn("__schedule", prompt)
        self.assertIn("Scheduled tasks", prompt)
        explanation, call = parse_tool_call(
            "Later.\n```tool\n"
            '{"name":"__schedule","args":{"action":"list"}}\n'
            "```"
        )
        self.assertEqual("__schedule", call["name"])
        self.assertEqual("Later.", explanation)

    def test_prompt_task_create_lists_and_gets(self):
        agent, runner = self.make_agent()
        result = self.call(
            agent, action="create", type="prompt", name="sensor",
            prompt="Check the sensor", when="once", delay_s=600)
        self.assertTrue(result.ok, result.error)
        self.assertIn("Created scheduled task #1", result.output)
        self.assertEqual(1, len(runner.calls))
        self.assertEqual("prompt", runner.calls[0][1]["type"])

        result = self.call(agent, action="list")
        self.assertTrue(result.ok)
        self.assertIn("Scheduled tasks: 1", result.output)

        result = self.call(agent, action="get", id=1)
        self.assertTrue(result.ok)
        self.assertIn("name: sensor", result.output)

    def test_code_task_requires_auto_mode(self):
        agent, runner = self.make_agent(auto_run=False)
        result = self.call(
            agent, action="create", type="code", name="blink",
            source="print(1)", when="once", delay_s=30)
        self.assertFalse(result.ok)
        self.assertIn("AUTO", result.error)
        self.assertEqual([], runner.calls)

        agent, runner = self.make_agent(auto_run=True)
        result = self.call(
            agent, action="create", type="code", name="blink",
            source="print(1)", when="once", delay_s=30)
        self.assertTrue(result.ok, result.error)
        self.assertEqual("code", runner.calls[0][1]["type"])

    def test_updating_code_task_schedule_requires_auto_mode(self):
        runner = FakeRunner()
        runner.tasks[1] = {
            "id": 1, "name": "code", "type": "code",
            "enabled": True, "when": "once",
        }
        agent, _runner = self.make_agent(runner=runner, auto_run=False)
        result = self.call(agent, action="update", id=1, delay_s=60)
        self.assertFalse(result.ok)
        self.assertIn("AUTO", result.error)

        result = self.call(agent, action="update", id=1, name="renamed")
        self.assertTrue(result.ok, result.error)

    def test_enabling_or_running_code_task_requires_auto_mode(self):
        runner = FakeRunner()
        runner.tasks[1] = {
            "id": 1, "name": "code", "type": "code",
            "enabled": False, "when": "once",
        }
        agent, _runner = self.make_agent(runner=runner, auto_run=False)
        result = self.call(agent, action="enable", id=1)
        self.assertFalse(result.ok)
        self.assertIn("AUTO", result.error)
        self.assertFalse(runner.tasks[1]["enabled"])

        result = self.call(agent, action="run", id=1)
        self.assertFalse(result.ok)
        self.assertIn("AUTO", result.error)

        result = self.call(agent, action="update", id=1, enabled=True)
        self.assertFalse(result.ok)
        self.assertIn("AUTO", result.error)

        result = self.call(agent, action="disable", id=1)
        self.assertTrue(result.ok, result.error)

        agent, _runner = self.make_agent(runner=runner, auto_run=True)
        result = self.call(agent, action="enable", id=1)
        self.assertTrue(result.ok, result.error)
        result = self.call(agent, action="run", id=1)
        self.assertTrue(result.ok, result.error)

    def test_delete_enable_disable_and_run(self):
        agent, runner = self.make_agent()
        self.call(agent, action="create", type="prompt", name="a",
                  prompt="x", when="once", delay_s=30)
        result = self.call(agent, action="disable", id=1)
        self.assertTrue(result.ok)
        self.assertFalse(runner.tasks[1]["enabled"])
        result = self.call(agent, action="enable", id=1)
        self.assertTrue(result.ok)
        self.assertTrue(runner.tasks[1]["enabled"])
        result = self.call(agent, action="run", id=1)
        self.assertTrue(result.ok)
        self.assertIn("queued", result.output)
        result = self.call(agent, action="delete", id=1)
        self.assertTrue(result.ok)
        self.assertNotIn(1, runner.tasks)


if __name__ == "__main__":
    unittest.main()
