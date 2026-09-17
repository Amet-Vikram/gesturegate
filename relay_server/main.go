package main

import (
	"context"
	"encoding/json"
	"log"
	"net/http"
	"os"
	"os/signal"
	"strings"
	"syscall"
	"time"

	"github.com/gorilla/websocket"
)

// deviceAuth holds every valid token, loaded once at startup from
// devices.json (path overridable via RELAY_DEVICES_FILE). Read-only after
// startup, so it's safe to read from many goroutines without locking.
var deviceAuth map[string]DeviceAuth

func healthHandler(w http.ResponseWriter, r *http.Request) {
	w.WriteHeader(http.StatusOK)
	w.Write([]byte("ok"))
}

// extractBearerToken pulls the token out of a standard
// "Authorization: Bearer <token>" header, returning "" if the header is
// missing or malformed.
func extractBearerToken(r *http.Request) string {
	const prefix = "Bearer "
	h := r.Header.Get("Authorization")
	if !strings.HasPrefix(h, prefix) {
		return ""
	}
	return strings.TrimPrefix(h, prefix)
}

// upgrader turns a normal HTTP connection into a WebSocket connection.
// It's declared once at package level and reused for every incoming
// connection -- it holds no per-connection state, just config.
var upgrader = websocket.Upgrader{
	ReadBufferSize:  1024,
	WriteBufferSize: 1024,
	CheckOrigin:     func(r *http.Request) bool { return true },
}

func devicesHandler(hub *Hub) http.HandlerFunc {
	return func(w http.ResponseWriter, r *http.Request) {
		token := extractBearerToken(r)
		requester, ok := deviceAuth[token]
		if !ok {
			log.Println("rejected /devices request: bad or missing token from", r.RemoteAddr)
			http.Error(w, "unauthorized", http.StatusUnauthorized)
			return
		}

		resp := make(chan []DeviceSummary)
		hub.list <- listRequest{resp: resp}
		devices := <-resp

		visible := make([]DeviceSummary, 0, len(devices))
		for _, d := range devices {
			if hasTopic(requester.Topics, d.DeviceID) {
				visible = append(visible, d)
			}
		}

		log.Printf("/devices request from device_id=%s: %d visible of %d online",
			requester.DeviceID, len(visible), len(devices))

		w.Header().Set("Content-Type", "application/json")
		if err := json.NewEncoder(w).Encode(visible); err != nil {
			log.Println("failed to encode devices response:", err)
		}
	}
}

// gestureHandler upgrades the connection, wraps it in a Client, registers
// it with the hub, and starts the two pumps.
func gestureHandler(hub *Hub) http.HandlerFunc {
	return func(w http.ResponseWriter, r *http.Request) {
		// Check auth BEFORE upgrading -- a plain HTTP response here is much
		// simpler for a client to handle than a WebSocket close code, and
		// we avoid creating/registering a Client for a rejected connection.
		token := extractBearerToken(r)
		device, ok := deviceAuth[token]
		if !ok {
			log.Println("rejected connection: bad or missing token from", r.RemoteAddr)
			http.Error(w, "unauthorized", http.StatusUnauthorized)
			return
		}

		conn, err := upgrader.Upgrade(w, r, nil)
		if err != nil {
			log.Println("upgrade failed:", err)
			return
		}

		client := &Client{
			hub:      hub,
			conn:     conn,
			send:     make(chan []byte, 16),
			DeviceID: device.DeviceID,
			Role:     device.Role,
			Topics:   device.Topics,
		}
		hub.register <- client

		log.Printf("client authenticated: device_id=%s role=%s", client.DeviceID, client.Role)

		// writePump needs its own goroutine, since this goroutine is about
		// to block forever inside readPump.
		go client.writePump()

		// readPump blocks until the client disconnects, so we call it
		// directly (not with `go`) -- this goroutine's whole job from here
		// on is reading from this one connection.
		client.readPump()
	}
}

func main() {
	devicesPath := os.Getenv("RELAY_DEVICES_FILE")
	if devicesPath == "" {
		devicesPath = "devices.json"
	}

	var err error
	deviceAuth, err = loadDeviceAuth(devicesPath)
	if err != nil {
		log.Fatalf("failed to load device auth config: %v", err)
	}
	log.Printf("loaded %d device credential(s) from %s", len(deviceAuth), devicesPath)

	hub := newHub()
	go hub.run()

	mux := http.NewServeMux()
	mux.HandleFunc("/healthz", healthHandler)
	mux.HandleFunc("/devices", devicesHandler(hub))
	mux.HandleFunc("/ws/gesture", gestureHandler(hub))

	addr := os.Getenv("RELAY_ADDR")
	if addr == "" {
		addr = ":8080"
	}
	srv := &http.Server{Addr: addr, Handler: mux}

	go func() {
		log.Printf("relay server listening on %s", addr)
		if err := srv.ListenAndServe(); err != nil && err != http.ErrServerClosed {
			log.Fatalf("server error: %v", err)
		}
	}()

	ctx, stop := signal.NotifyContext(context.Background(), os.Interrupt, syscall.SIGTERM)
	defer stop()
	<-ctx.Done()
	log.Println("shutdown signal received, starting graceful shutdown")

	shutdownCtx, cancel := context.WithTimeout(context.Background(), 5*time.Second)
	defer cancel()
	if err := srv.Shutdown(shutdownCtx); err != nil {
		log.Printf("HTTP server shutdown error: %v", err)
	}

	done := make(chan struct{})
	hub.shutdown <- shutdownRequest{done: done}
	select {
	case <-done:
		log.Println("all connections closed cleanly")
	case <-time.After(5 * time.Second):
		log.Println("timed out waiting for connections to close, exiting anyway")
	}

	log.Println("shutdown complete")
}
