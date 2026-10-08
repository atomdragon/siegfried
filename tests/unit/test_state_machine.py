"""Unit tests for deterministic health state machine."""

import unittest
from siegfried.contracts.states import SystemState, MAX_CONTINUOUS_SITTING_SECONDS
from siegfried.core.state_machine import HealthStateMachine
from siegfried.core.errors import InvalidStateTransitionError, PostureLimitReachedError


class TestStateMachine(unittest.TestCase):

    def setUp(self):
        self.sm = HealthStateMachine()

    def test_initial_state_idle(self):
        self.assertEqual(self.sm.state, SystemState.IDLE)
        self.assertEqual(self.sm.continuous_sitting_seconds, 0.0)

    def test_valid_transitions(self):
        self.sm.transition_to(SystemState.POMODORO_RUNNING)
        self.assertEqual(self.sm.state, SystemState.POMODORO_RUNNING)

        self.sm.transition_to(SystemState.BREAK_RUNNING)
        self.assertEqual(self.sm.state, SystemState.BREAK_RUNNING)

        self.sm.transition_to(SystemState.IDLE)
        self.assertEqual(self.sm.state, SystemState.IDLE)

    def test_invalid_transition_raises_error(self):
        # Cannot go straight from IDLE to BREAK_RUNNING
        with self.assertRaises(InvalidStateTransitionError):
            self.sm.transition_to(SystemState.BREAK_RUNNING)

    def test_posture_hard_limit_at_60_minutes(self):
        self.sm.transition_to(SystemState.POMODORO_RUNNING)
        # Sitting for 3600 seconds (60 minutes)
        self.sm.add_sitting_time(3600.0)
        self.assertEqual(self.sm.state, SystemState.CRITICAL_BREAK_REQUIRED)

        # Cannot restart pomodoro without break
        with self.assertRaises(PostureLimitReachedError):
            self.sm.transition_to(SystemState.POMODORO_RUNNING)

        # Entering break allows recovery after reset
        self.sm.transition_to(SystemState.BREAK_RUNNING)
        self.sm.reset_sitting_time()
        self.sm.transition_to(SystemState.IDLE)
        self.assertEqual(self.sm.state, SystemState.IDLE)

    def test_can_postpone_check(self):
        self.sm.transition_to(SystemState.POMODORO_RUNNING)
        self.sm.add_sitting_time(3000.0) # 50 minutes
        # Can postpone 10 minutes (600s), hits 3600 exactly
        self.assertTrue(self.sm.can_postpone(600.0))
        # Cannot postpone 11 minutes (660s)
        self.assertFalse(self.sm.can_postpone(660.0))


if __name__ == "__main__":
    unittest.main()
