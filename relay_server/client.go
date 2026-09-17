package main

import (
	"encoding/json"
	"log"

	"github.com/gorilla/websocket"
)

// DeviceID/Role/Topics come from the DeviceAuth entry matched during the
// handshake -- every registered Client is, by construction, authenticated.
type Client struct {
	hub  *Hub
	conn *websocket.Conn
	send chan []byte

	DeviceID string
	Role     string
	Topics   []string
}

func (c *Client) readPump() {
	defer func() {
		c.hub.unregister <- c
		c.conn.Close()
	}()

	for {
		_, msg, err := c.conn.ReadMessage()
		if err != nil {
			log.Println("read error, client disconnecting:", err)
			break
		}

		switch c.Role {
		case "edge":
			c.handleEdgePublish(msg)
		case "client":
			c.handleSubscriptionRequest(msg)
		default:
			log.Printf("ignoring message from unrecognized role=%q device_id=%s", c.Role, c.DeviceID)
		}
	}
}

func (c *Client) handleEdgePublish(msg []byte) {
	result, err := parseEdgeEvent(msg)
	if err != nil {
		log.Printf("dropping malformed event from device_id=%s: %v -- raw message: %s", c.DeviceID, err, msg)
		return
	}

	switch ev := result.(type) {
	case GestureSequenceEvent:
		ev.DeviceID = c.DeviceID

		outgoing, err := json.Marshal(ev)
		if err != nil {
			log.Printf("failed to re-marshal event from device_id=%s: %v", c.DeviceID, err)
			return
		}

		log.Printf("gesture sequence event from device_id=%s: event=%s sequence=%v",
			c.DeviceID, ev.Event, ev.Sequence)

		c.hub.broadcast <- broadcastMsg{sender: c, topic: c.DeviceID, data: outgoing}

	case SessionEvent:
		ev.DeviceID = c.DeviceID

		outgoing, err := json.Marshal(ev)
		if err != nil {
			log.Printf("failed to re-marshal session event from device_id=%s: %v", c.DeviceID, err)
			return
		}

		log.Printf("session event from device_id=%s: event=%s reason=%q gate=%s",
			c.DeviceID, ev.Event, ev.Reason, ev.Gate)

		c.hub.broadcast <- broadcastMsg{sender: c, topic: c.DeviceID, data: outgoing}
	}
}

func (c *Client) handleSubscriptionRequest(msg []byte) {
	req, err := parseSubscriptionRequest(msg)
	if err != nil {
		log.Printf("dropping malformed subscription request from device_id=%s: %v -- raw message: %s", c.DeviceID, err, msg)
		return
	}
	c.hub.subscribe <- subscriptionRequest{client: c, action: req.Action, topic: req.Topic}
}

func (c *Client) writePump() {
	defer c.conn.Close()

	for msg := range c.send {
		if err := c.conn.WriteMessage(websocket.TextMessage, msg); err != nil {
			log.Println("write error:", err)
			return
		}
	}
}
