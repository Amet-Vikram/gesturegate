package main

import (
	"log"
	"time"

	"github.com/gorilla/websocket"
)

type broadcastMsg struct {
	sender *Client
	topic  string
	data   []byte
}

type subscriptionRequest struct {
	client *Client
	action string // "subscribe" or "unsubscribe"
	topic  string
}

type DeviceSummary struct {
	DeviceID    string    `json:"device_id"`
	ConnectedAt time.Time `json:"connected_at"`
}
type listRequest struct {
	resp chan []DeviceSummary
}

type shutdownRequest struct {
	done chan struct{}
}

type Hub struct {
	clients       map[*Client]time.Time
	subscriptions map[*Client]map[string]bool
	register      chan *Client
	unregister    chan *Client
	broadcast     chan broadcastMsg
	subscribe     chan subscriptionRequest
	list          chan listRequest
	shutdown      chan shutdownRequest
}

func newHub() *Hub {
	return &Hub{
		clients:       make(map[*Client]time.Time),
		subscriptions: make(map[*Client]map[string]bool),
		register:      make(chan *Client),
		unregister:    make(chan *Client),
		broadcast:     make(chan broadcastMsg),
		subscribe:     make(chan subscriptionRequest),
		list:          make(chan listRequest),
		shutdown:      make(chan shutdownRequest),
	}
}

// hasTopic reports whether topic appears in topics. Topic lists are tiny
// (a handful of device IDs per client), so a linear scan is simpler and
// plenty fast -- no need for a set/map here.
func hasTopic(topics []string, topic string) bool {
	for _, t := range topics {
		if t == topic {
			return true
		}
	}
	return false
}

func (h *Hub) run() {
	for {
		select {
		case client := <-h.register:
			h.clients[client] = time.Now()
			h.subscriptions[client] = make(map[string]bool) // starts empty -- must explicitly subscribe
			log.Printf("client registered, total=%d", len(h.clients))

		case client := <-h.unregister:
			if connectedAt, ok := h.clients[client]; ok {
				delete(h.clients, client)
				delete(h.subscriptions, client)
				close(client.send)
				log.Printf("client unregistered after %s, total=%d",
					time.Since(connectedAt).Round(time.Millisecond), len(h.clients))
			}

		case req := <-h.subscribe:

			if _, stillConnected := h.subscriptions[req.client]; !stillConnected {
				log.Printf("ignoring subscription request from already-disconnected device_id=%s",
					req.client.DeviceID)
				continue
			}
			if !hasTopic(req.client.Topics, req.topic) {
				log.Printf("rejected subscription: device_id=%s not authorized for topic=%s",
					req.client.DeviceID, req.topic)
				continue
			}
			switch req.action {
			case "subscribe":
				h.subscriptions[req.client][req.topic] = true
				log.Printf("device_id=%s subscribed to topic=%s", req.client.DeviceID, req.topic)
			case "unsubscribe":
				delete(h.subscriptions[req.client], req.topic)
				log.Printf("device_id=%s unsubscribed from topic=%s", req.client.DeviceID, req.topic)
			}

		case req := <-h.list:
			var devices []DeviceSummary
			for client, connectedAt := range h.clients {
				if client.Role == "edge" {
					devices = append(devices, DeviceSummary{
						DeviceID:    client.DeviceID,
						ConnectedAt: connectedAt,
					})
				}
			}
			req.resp <- devices

		case bm := <-h.broadcast:
			delivered := 0
			for client := range h.clients {
				if client == bm.sender {
					continue // never echo back to whoever sent it
				}
				if client.Role != "client" {
					continue // only subscriber-role connections receive fanned-out events
				}
				if !h.subscriptions[client][bm.topic] {
					continue // authorized, maybe, but not actively subscribed right now
				}
				select {
				case client.send <- bm.data:
					delivered++
				default:

					log.Printf("dropping slow client device_id=%s: send buffer full while delivering topic=%s",
						client.DeviceID, bm.topic)
					close(client.send)
					delete(h.clients, client)
					delete(h.subscriptions, client)
				}
			}

			log.Printf("topic=%s delivered to %d subscriber(s)", bm.topic, delivered)

		case req := <-h.shutdown:
			log.Printf("hub shutting down: closing %d connection(s)", len(h.clients))
			for client := range h.clients {

				closeMsg := websocket.FormatCloseMessage(websocket.CloseGoingAway, "relay server shutting down")
				_ = client.conn.WriteControl(websocket.CloseMessage, closeMsg, time.Now().Add(time.Second))
				client.conn.Close()
			}
			req.done <- struct{}{}
			return
		}
	}
}
