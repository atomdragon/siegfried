"""Unit tests for Siegfried contracts."""

import unittest
from siegfried.contracts.states import SystemState, MAX_CONTINUOUS_SITTING_SECONDS
from siegfried.contracts.events import Event, EventType, EVENT_SCHEMA_VERSION
from siegfried.contracts.ipc import IPCRequest, IPCResponse, IPCCommand, IPCStatus
from siegfried.contracts.config import (
    get_default_core_profile,
    get_default_active_agenda,
    validate_core_profile,
    validate_active_agenda,
)


class TestContracts(unittest.TestCase):

    def test_event_schema_v1_serialization(self):
        event = Event.create(EventType.POMODORO_STARTED, {"duration_min": 50, "task": "Arquitectura"})
        d = event.to_dict()
        self.assertEqual(d["v"], EVENT_SCHEMA_VERSION)
        self.assertEqual(d["type"], "pomodoro_started")
        self.assertIn("ts", d)
        self.assertEqual(d["data"]["duration_min"], 50)

        # Deserialize
        reconstructed = Event.from_dict(d)
        self.assertEqual(reconstructed.v, 1)
        self.assertEqual(reconstructed.type, "pomodoro_started")
        self.assertEqual(reconstructed.data["task"], "Arquitectura")

    def test_event_schema_validation_rejects_bad_version(self):
        bad_data = {"v": 2, "ts": 12345.0, "type": "pomodoro_started", "data": {}}
        with self.assertRaises(ValueError):
            Event.from_dict(bad_data)

    def test_ipc_request_response(self):
        req = IPCRequest.create(IPCCommand.START_FOCUS, {"duration_min": 25})
        d = req.to_dict()
        self.assertEqual(d["v"], 1)
        self.assertEqual(d["cmd"], "START_FOCUS")
        self.assertEqual(d["args"]["duration_min"], 25)

        req2 = IPCRequest.from_dict(d)
        self.assertEqual(req2.cmd, "START_FOCUS")

        res = IPCResponse.ok(req.request_id, {"started": True})
        res_dict = res.to_dict()
        self.assertEqual(res_dict["status"], IPCStatus.OK.value)
        self.assertIsNone(res_dict["error_msg"])

    def test_config_validation(self):
        core = get_default_core_profile()
        validate_core_profile(core)

        agenda = get_default_active_agenda()
        validate_active_agenda(agenda)

        # Bad agenda with more than 2 secondary tasks
        bad_agenda = {
            "v": 1,
            "critical_task": None,
            "secondary_tasks": [{"id": 1}, {"id": 2}, {"id": 3}],
            "backlog": []
        }
        with self.assertRaises(ValueError):
            validate_active_agenda(bad_agenda)

    def test_inference_contracts_serialization_and_validation(self):
        from siegfried.contracts.inference import (
            InferenceMessage,
            InferenceRequest,
            InferenceResponse,
            InferenceUsage,
        )

        # Valid message
        msg = InferenceMessage(role="user", content="Hola Siegfried")
        self.assertEqual(msg.to_dict(), {"role": "user", "content": "Hola Siegfried"})
        reconstructed_msg = InferenceMessage.from_dict({"role": "assistant", "content": "A la orden"})
        self.assertEqual(reconstructed_msg.role, "assistant")

        # Invalid message
        with self.assertRaises(ValueError):
            InferenceMessage(role="", content="Test")
        with self.assertRaises(ValueError):
            InferenceMessage.from_dict("invalid")

        # Request validation
        req = InferenceRequest(
            messages=[msg],
            model="deepseek-chat",
            max_tokens=100,
            temperature=0.7,
            timeout_seconds=3.0,
            deadline=999999.0,
        )
        req.validate()
        payload = req.to_payload()
        self.assertEqual(payload["model"], "deepseek-chat")
        self.assertEqual(payload["max_tokens"], 100)
        self.assertEqual(payload["temperature"], 0.7)
        self.assertEqual(len(payload["messages"]), 1)

        # Invalid request
        with self.assertRaises(ValueError):
            InferenceRequest(messages=[]).validate()
        with self.assertRaises(ValueError):
            InferenceRequest(messages=[msg], timeout_seconds=-1.0).validate()
        with self.assertRaises(ValueError):
            InferenceRequest(messages=[msg], temperature=3.5).validate()

        # Usage
        usage = InferenceUsage.from_dict({"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15})
        self.assertEqual(usage.total_tokens, 15)

        # Response
        resp = InferenceResponse(
            content="Respuesta del modelo",
            model="deepseek-chat",
            usage=usage,
            duration_ms=12.5,
            finish_reason="stop",
        )
        d = resp.to_dict()
        self.assertEqual(d["content"], "Respuesta del modelo")
        self.assertEqual(d["usage"]["total_tokens"], 15)


if __name__ == "__main__":
    unittest.main()

