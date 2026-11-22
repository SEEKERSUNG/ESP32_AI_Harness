import json
import os
import tempfile
import unittest

from harness.agent import Agent, AgentError
from harness.conversation import BackgroundStore, ConversationStore
from harness.executor import ExecutionResult
from harness.llm import LLMStreamCancelled, llm_error
from tests.support import DummyBoard, DummyConfig


class FakeLLM:
    def __init__(self):
        self.history = []

    def complete(self, messages, **kwargs):
        self.history.append(messages)
        return "ok"

    def complete_stream(self, messages, **kwargs):
        return self.complete(messages, **kwargs)


class FailingLLM:
    def __init__(self, error):
        self.error = error

    def complete(self, messages, **kwargs):
        raise self.error

    def complete_stream(self, messages, **kwargs):
        raise self.error


class ConversationStoreTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)

    def config(self, **overrides):
        values = {
            "persist_conversation": True,
            "conversation_file": "/state/conversation.json",
            "conversation_max_chars": 1000,
            "conversation_max_messages": 10,
        }
        values.update(overrides)
        return DummyConfig(**values)

    def test_round_trip(self):
        store = ConversationStore(self.directory.name, self.config())
        messages = [
            {"role": "user", "content": "hello"},
            {"role": "assistant", "content": "world"},
        ]
        self.assertTrue(store.save(
            messages, "summary text", "the original question"))
        state = store.load()
        self.assertEqual(messages, state["messages"])
        self.assertEqual("summary text", state["summary"])
        self.assertEqual("the original question", state["original_request"])
        self.assertTrue(store.exists())
        self.assertEqual(2, store.status()["messages"])

    def test_round_trip_preserves_message_kind(self):
        store = ConversationStore(self.directory.name, self.config())
        messages = [
            {"role": "user", "content": "hello", "kind": "user"},
            {"role": "assistant", "content": "world", "kind": "assistant",
             "reasoning": "thinking about world"},
            {"role": "user", "content": "Local tool result", "kind": "observation"},
        ]
        store.save(messages)
        self.assertEqual(messages, store.load()["messages"])

    def test_background_store_round_trip_and_clear(self):
        config = self.config()
        store = BackgroundStore(self.directory.name, config)
        job = {
            "id": 7,
            "token": "7@12345",
            "phase": "ready",
            "auto": True,
            "recovered": False,
            "driver": "web",
            "timeout": 45,
            "source_chars": 120,
            "task": "draw dashboard",
            "turns": 2,
            "error": "",
            "result": ExecutionResult(
                True, "output text", "", 321, False, False),
        }
        self.assertTrue(store.save(job))
        state = store.load()
        self.assertEqual(7, state["id"])
        self.assertEqual("7@12345", state["token"])
        self.assertEqual("ready", state["phase"])
        self.assertTrue(state["auto"])
        self.assertEqual(45, state["timeout"])
        self.assertEqual("draw dashboard", state["task"])
        self.assertEqual(2, state["turns"])
        self.assertEqual("output text", state["result"]["output"])
        self.assertTrue(state["result"]["ok"])
        self.assertTrue(store.exists())
        store.clear()
        self.assertIsNone(store.load())
        self.assertFalse(store.exists())

    def test_background_store_preserves_cancelled_result(self):
        store = BackgroundStore(self.directory.name, self.config())
        job = {
            "id": 8, "phase": "ready", "auto": True, "recovered": False,
            "driver": "web", "timeout": 0, "source_chars": 10,
            "task": "run until stopped", "turns": 1, "error": "",
            "result": ExecutionResult(
                False, "", "ExecutionCancelled: stop requested", 50,
                False, False, True),
        }
        store.save(job)
        state = store.load()
        self.assertTrue(state["result"]["cancelled"])
        self.assertEqual(0, state["timeout"])

    def test_background_store_round_trips_multiple_jobs(self):
        store = BackgroundStore(self.directory.name, self.config())
        self.assertTrue(store.save_jobs([
            {"id": 1, "token": "1@1", "phase": "ready", "timeout": 20,
             "source_chars": 10, "task": "one", "note": "alpha", "turns": 1,
             "result": ExecutionResult(True, "one-out", "", 12)},
            {"id": 2, "token": "2@2", "phase": "running", "timeout": 0,
             "source_chars": 20, "task": "two", "note": "beta", "turns": 2},
            {"id": 3, "token": "3@3", "phase": "error", "timeout": 20,
             "source_chars": 5, "task": "three", "note": "gamma", "turns": 1,
             "error": "boom"},
        ]))
        jobs = {job["id"]: job for job in store.load_jobs()}
        self.assertEqual(3, len(jobs))
        self.assertEqual("one-out", jobs[1]["result"]["output"])
        # An active job did not survive the restart; it becomes a visible
        # error and is never auto-run.
        self.assertEqual("error", jobs[2]["phase"])
        self.assertIn("restart", jobs[2]["error"])
        self.assertIsNone(jobs[2]["result"])
        self.assertEqual("beta", jobs[2]["note"])
        self.assertEqual("boom", jobs[3]["error"])

    def test_background_store_turns_missing_ready_result_into_error(self):
        store = BackgroundStore(self.directory.name, self.config())
        store.save_jobs([{
            "id": 4, "token": "4@4", "phase": "ready", "timeout": 20,
            "source_chars": 5, "task": "one", "note": "one", "turns": 1,
        }])
        job = store.load_jobs()[0]
        self.assertEqual("error", job["phase"])
        self.assertIn("unavailable", job["error"])

    def test_background_store_respects_persistence_opt_out(self):
        store = BackgroundStore(self.directory.name, self.config(
            persist_conversation=False))
        self.assertFalse(store.save({
            "id": 1, "phase": "ready", "result": None,
        }))
        self.assertIsNone(store.load())

    def test_limits_keep_newest_messages(self):
        store = ConversationStore(self.directory.name, self.config(
            conversation_max_chars=4096, conversation_max_messages=4))
        messages = [
            {"role": "user", "content": "a" * 3000},
            {"role": "assistant", "content": "b" * 3000},
        ]
        store.save(messages)
        state = store.load()
        self.assertEqual(1, len(state["messages"]))
        self.assertEqual("b" * 3000, state["messages"][0]["content"])

    def test_disabled_store_does_not_write(self):
        store = ConversationStore(self.directory.name, self.config(
            persist_conversation=False))
        self.assertFalse(store.save([{"role": "user", "content": "x"}]))
        self.assertIsNone(store.load())
        self.assertFalse(store.exists())

    def test_invalid_json_is_ignored(self):
        store = ConversationStore(self.directory.name, self.config())
        path = os.path.join(self.directory.name, "state", "conversation.json")
        os.makedirs(os.path.dirname(path))
        with open(path, "w") as handle:
            handle.write("{not json")
        self.assertIsNone(store.load())
        self.assertTrue(store.last_error)

    def test_clear_removes_file(self):
        store = ConversationStore(self.directory.name, self.config())
        store.save([{"role": "user", "content": "x"}])
        store.clear()
        self.assertFalse(store.exists())
        self.assertIsNone(store.load())

    def test_restore_conversation_into_agent(self):
        config = self.config()
        store = ConversationStore(self.directory.name, config)
        store.save([
            {"role": "user", "content": "old task"},
            {"role": "assistant", "content": "old answer"},
        ], "old summary", "the first task")
        agent = Agent(config, FakeLLM(), DummyBoard(), conversation=store)
        restored = agent.restore_conversation(store.load())
        self.assertEqual(2, restored)
        self.assertEqual("old summary", agent.summary)
        self.assertEqual("the first task", agent.original_request)
        self.assertEqual("old task", agent.history[0]["content"])

    def test_failed_turn_persists_user_message_and_error(self):
        config = self.config(api_retries=0, api_retry_base_ms=0)
        store = ConversationStore(self.directory.name, config)
        agent = Agent(
            config,
            FailingLLM(llm_error("SSE event idle timeout", retryable=True)),
            DummyBoard(),
            conversation=store,
        )
        with self.assertRaises(Exception):
            agent.ask("write a long program")
        state = store.load()
        self.assertIsNotNone(state)
        joined = "\n".join(message["content"] for message in state["messages"])
        self.assertIn("write a long program", joined)
        self.assertIn("did not finish", joined)
        self.assertIn("SSE event idle timeout", joined)
        self.assertEqual("assistant", state["messages"][-1]["role"])

    def test_cancelled_turn_keeps_context(self):
        config = self.config(api_retries=0, api_retry_base_ms=0)
        store = ConversationStore(self.directory.name, config)
        agent = Agent(
            config,
            FailingLLM(LLMStreamCancelled()),
            DummyBoard(),
            conversation=store,
        )
        with self.assertRaises(LLMStreamCancelled):
            agent.ask("stop me")
        joined = "\n".join(
            message["content"] for message in store.load()["messages"]
        )
        self.assertIn("stop me", joined)
        self.assertIn("did not finish", joined)
        self.assertIn("stopped", joined)

    def test_loop_limit_failure_keeps_partial_turn(self):
        config = self.config(
            api_retries=0, api_retry_base_ms=0, max_tool_calls=0)
        store = ConversationStore(self.directory.name, config)

        class ToolLLM:
            def complete(self, messages, **kwargs):
                return (
                    "```tool\n"
                    '{"name":"__memory","args":{"action":"read"}}\n'
                    "```"
                )

            complete_stream = complete

        agent = Agent(config, ToolLLM(), DummyBoard(), conversation=store)
        with self.assertRaises(AgentError):
            agent.ask("remember this")
        joined = "\n".join(
            message["content"] for message in store.load()["messages"]
        )
        self.assertIn("remember this", joined)
        self.assertIn("Tool call limit", joined)

    def test_successful_turn_is_persisted_once(self):
        config = self.config()
        store = ConversationStore(self.directory.name, config)
        agent = Agent(config, FakeLLM(), DummyBoard(), conversation=store)
        agent.ask("hello")
        messages = store.load()["messages"]
        self.assertEqual(2, len(messages))
        self.assertEqual("hello", messages[0]["content"])
        self.assertEqual("ok", messages[1]["content"])

    def test_agent_clear_removes_persisted_file(self):
        config = self.config()
        store = ConversationStore(self.directory.name, config)
        store.save([{"role": "user", "content": "x"}])
        agent = Agent(config, FakeLLM(), DummyBoard(), conversation=store)
        agent.restore_conversation(store.load())
        agent.clear()
        self.assertFalse(store.exists())
        self.assertEqual([], agent.history)


if __name__ == "__main__":
    unittest.main()
