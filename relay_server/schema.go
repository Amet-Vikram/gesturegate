package main

import (
	"encoding/json"
	"fmt"
	"time"
)

type SubscriptionRequest struct {
	Action string `json:"action"` // "subscribe" or "unsubscribe"
	Topic  string `json:"topic"`
}

func parseSubscriptionRequest(raw []byte) (SubscriptionRequest, error) {
	var req SubscriptionRequest
	if err := json.Unmarshal(raw, &req); err != nil {
		return req, fmt.Errorf("invalid JSON: %w", err)
	}
	if req.Action != "subscribe" && req.Action != "unsubscribe" {
		return req, fmt.Errorf(`action must be "subscribe" or "unsubscribe", got %q`, req.Action)
	}
	if req.Topic == "" {
		return req, fmt.Errorf("topic must not be empty")
	}
	return req, nil
}

type EdgeEvent struct {
	SchemaVersion int    `json:"schema_version"`
	Event         string `json:"event"`
	Source        string `json:"source"`
	DeviceID      string `json:"device_id,omitempty"` // stamped by the relay, never trusted from the wire
	DetectedAt    string `json:"detected_at"`         // RFC3339
}

// GestureSequenceEvent carries a confirmed gesture sequence.
// schema_version=2, event="gesture_sequence_detected".
type GestureSequenceEvent struct {
	EdgeEvent
	Sequence    []string  `json:"sequence"`
	Confidences []float64 `json:"confidences"`
	ModelVocab  string    `json:"model_vocab"`
}

type SessionEvent struct {
	EdgeEvent
	Gate   string `json:"gate"`             // "static" or "dynamic" -- which gate changed
	Reason string `json:"reason,omitempty"` // "user_sequence" or "idle_timeout" (session_locked only)
}

func parseEdgeEvent(raw []byte) (interface{}, error) {
	// Step 1: parse the envelope to determine event type.
	var env EdgeEvent
	if err := json.Unmarshal(raw, &env); err != nil {
		return nil, fmt.Errorf("invalid JSON: %w", err)
	}
	if env.SchemaVersion != 2 {
		return nil, fmt.Errorf(
			"unsupported schema_version %d (expected 2 -- see INFERENCE_SERVER_SCHEMA.md)",
			env.SchemaVersion)
	}
	if env.Source == "" {
		return nil, fmt.Errorf("source must not be empty")
	}
	if _, err := time.Parse(time.RFC3339Nano, env.DetectedAt); err != nil {
		return nil, fmt.Errorf("detected_at not RFC3339: %w", err)
	}

	// Step 2: dispatch on event type.
	switch env.Event {

	case "gesture_sequence_detected":
		var ev GestureSequenceEvent
		if err := json.Unmarshal(raw, &ev); err != nil {
			return nil, fmt.Errorf("invalid gesture_sequence_detected payload: %w", err)
		}
		if len(ev.Sequence) == 0 {
			return nil, fmt.Errorf("sequence must not be empty")
		}
		if len(ev.Confidences) != len(ev.Sequence) {
			return nil, fmt.Errorf("confidences length (%d) must match sequence length (%d)",
				len(ev.Confidences), len(ev.Sequence))
		}
		for i, label := range ev.Sequence {
			if label == "" {
				return nil, fmt.Errorf("gesture label at position %d must not be empty", i)
			}
			if ev.Confidences[i] < 0 || ev.Confidences[i] > 1 {
				return nil, fmt.Errorf("confidence %v at position %d out of [0,1] range",
					ev.Confidences[i], i)
			}
		}
		if ev.ModelVocab == "" {
			return nil, fmt.Errorf("model_vocab must not be empty")
		}
		return ev, nil

	case "session_unlocked", "session_locked":
		var ev SessionEvent
		if err := json.Unmarshal(raw, &ev); err != nil {
			return nil, fmt.Errorf("invalid session event payload: %w", err)
		}
		if ev.Gate != "static" && ev.Gate != "dynamic" {
			return nil, fmt.Errorf(`gate must be "static" or "dynamic", got %q`, ev.Gate)
		}
		if ev.Event == "session_locked" {
			if ev.Reason != "user_sequence" && ev.Reason != "idle_timeout" {
				return nil, fmt.Errorf(
					`session_locked reason must be "user_sequence" or "idle_timeout", got %q`,
					ev.Reason)
			}
		}
		return ev, nil

	default:
		return nil, fmt.Errorf("unexpected event type %q", env.Event)
	}
}
