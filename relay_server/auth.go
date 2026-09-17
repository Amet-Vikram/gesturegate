package main

import (
	"encoding/json"
	"fmt"
	"os"
)

type DeviceAuth struct {
	Token    string   `json:"token"`
	DeviceID string   `json:"device_id"`
	Role     string   `json:"role"`   // "edge" (publishes predictions) or "client" (subscribes)
	Topics   []string `json:"topics"` // device IDs this token may publish as / subscribe to
}

func loadDeviceAuth(path string) (map[string]DeviceAuth, error) {
	data, err := os.ReadFile(path)
	if err != nil {
		return nil, fmt.Errorf("reading device auth file %q: %w", path, err)
	}

	var entries []DeviceAuth
	if err := json.Unmarshal(data, &entries); err != nil {
		return nil, fmt.Errorf("parsing device auth file %q: %w", path, err)
	}

	byToken := make(map[string]DeviceAuth, len(entries))
	for _, e := range entries {
		if e.Token == "" || e.DeviceID == "" {
			return nil, fmt.Errorf("device auth entry missing token or device_id: %+v", e)
		}
		byToken[e.Token] = e
	}
	return byToken, nil
}
