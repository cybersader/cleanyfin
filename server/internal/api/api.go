// Package api wires the cleanyfin HTTP endpoints over the store.
//
// Routing uses Go 1.22+ net/http method+wildcard patterns — no third-party
// router needed (tech-stack: keep the dependency tree tiny).
package api

import (
	"database/sql"
	"encoding/json"
	"errors"
	"log/slog"
	"net/http"
	"strings"
	"time"

	"github.com/cybersader/cleanyfin/server/internal/store"
)

// Fixed v1 taxonomy (decision R05). Categories are a closed set so federated
// data stays consistent; free-form extension is a future `tags` field.
var validCategories = map[string]bool{
	"profanity": true, "sexual_dialogue": true, "sex_scene": true, "nudity": true,
	"violence": true, "gore": true, "disturbing": true, "substance_use": true, "crude": true,
}

// Actions: blur/crop are schema-reserved but rendered as skip in v1 (R05).
var validActions = map[string]bool{"mute": true, "skip": true, "mark": true}

type API struct {
	st  *store.Store
	log *slog.Logger
}

// New returns the HTTP handler for the cleanyfin API.
func New(st *store.Store, log *slog.Logger) http.Handler {
	a := &API{st: st, log: log}
	mux := http.NewServeMux()
	mux.HandleFunc("GET /healthz", a.health)                       // liveness
	mux.HandleFunc("GET /readyz", a.ready)                         // readiness (DB reachable)
	mux.HandleFunc("GET /api/v1/stats", a.stats)                   //
	mux.HandleFunc("GET /api/v1/segments", a.getSegments)          // ?fp=<fingerprint>
	mux.HandleFunc("POST /api/v1/segments", a.postSegment)         // submit
	mux.HandleFunc("POST /api/v1/segments/{id}/vote", a.postVote)  // up/down vote
	return a.logMiddleware(mux)
}

func (a *API) logMiddleware(next http.Handler) http.Handler {
	return http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		start := time.Now()
		next.ServeHTTP(w, r)
		a.log.Info("request", "method", r.Method, "path", r.URL.Path, "dur", time.Since(start).String())
	})
}

func writeJSON(w http.ResponseWriter, code int, v any) {
	w.Header().Set("Content-Type", "application/json")
	w.WriteHeader(code)
	_ = json.NewEncoder(w).Encode(v)
}

func writeErr(w http.ResponseWriter, code int, msg string) {
	writeJSON(w, code, map[string]string{"error": msg})
}

func (a *API) health(w http.ResponseWriter, _ *http.Request) {
	w.Header().Set("Content-Type", "text/plain")
	w.WriteHeader(http.StatusOK)
	_, _ = w.Write([]byte("ok"))
}

func (a *API) ready(w http.ResponseWriter, r *http.Request) {
	if err := a.st.Ping(r.Context()); err != nil {
		writeErr(w, http.StatusServiceUnavailable, "database unavailable")
		return
	}
	writeJSON(w, http.StatusOK, map[string]string{"status": "ready"})
}

func (a *API) stats(w http.ResponseWriter, r *http.Request) {
	s, err := a.st.Stats(r.Context())
	if err != nil {
		a.log.Error("stats", "err", err)
		writeErr(w, http.StatusInternalServerError, "stats failed")
		return
	}
	writeJSON(w, http.StatusOK, s)
}

func (a *API) getSegments(w http.ResponseWriter, r *http.Request) {
	fp := strings.TrimSpace(r.URL.Query().Get("fp"))
	if fp == "" {
		writeErr(w, http.StatusBadRequest, "missing required query param 'fp' (release fingerprint)")
		return
	}
	segs, err := a.st.SegmentsByFingerprint(r.Context(), fp)
	if err != nil {
		a.log.Error("query segments", "err", err)
		writeErr(w, http.StatusInternalServerError, "query failed")
		return
	}
	writeJSON(w, http.StatusOK, map[string]any{"fingerprint": fp, "segments": segs})
}

type submitRequest struct {
	Fingerprint string `json:"fingerprint"`
	DurationMs  int64  `json:"durationMs"`
	StartMs     int64  `json:"startMs"`
	EndMs       int64  `json:"endMs"`
	Category    string `json:"category"`
	Severity    int    `json:"severity"`
	Action      string `json:"action"`
	SubmitterID string `json:"submitterId"`
}

func (a *API) postSegment(w http.ResponseWriter, r *http.Request) {
	var req submitRequest
	if err := json.NewDecoder(http.MaxBytesReader(w, r.Body, 1<<16)).Decode(&req); err != nil {
		writeErr(w, http.StatusBadRequest, "invalid JSON body")
		return
	}
	req.Fingerprint = strings.TrimSpace(req.Fingerprint)
	req.SubmitterID = strings.TrimSpace(req.SubmitterID)
	switch {
	case req.Fingerprint == "":
		writeErr(w, http.StatusBadRequest, "fingerprint is required")
	case req.SubmitterID == "":
		writeErr(w, http.StatusBadRequest, "submitterId is required")
	case req.EndMs <= req.StartMs:
		writeErr(w, http.StatusBadRequest, "endMs must be greater than startMs")
	case req.StartMs < 0:
		writeErr(w, http.StatusBadRequest, "startMs must be >= 0")
	case !validCategories[req.Category]:
		writeErr(w, http.StatusBadRequest, "invalid category")
	case !validActions[req.Action]:
		writeErr(w, http.StatusBadRequest, "invalid action (mute|skip|mark)")
	case req.Severity < 0 || req.Severity > 3:
		writeErr(w, http.StatusBadRequest, "severity must be 0-3")
	default:
		seg, err := a.st.InsertSegment(r.Context(), store.Segment{
			Fingerprint: req.Fingerprint, DurationMs: req.DurationMs,
			StartMs: req.StartMs, EndMs: req.EndMs, Category: req.Category,
			Severity: req.Severity, Action: req.Action, SubmitterID: req.SubmitterID,
		})
		if err != nil {
			a.log.Error("insert segment", "err", err)
			writeErr(w, http.StatusInternalServerError, "insert failed")
			return
		}
		writeJSON(w, http.StatusCreated, seg)
	}
}

type voteRequest struct {
	SubmitterID string `json:"submitterId"`
	Value       int    `json:"value"` // +1 or -1
}

func (a *API) postVote(w http.ResponseWriter, r *http.Request) {
	id := r.PathValue("id")
	var req voteRequest
	if err := json.NewDecoder(http.MaxBytesReader(w, r.Body, 1<<12)).Decode(&req); err != nil {
		writeErr(w, http.StatusBadRequest, "invalid JSON body")
		return
	}
	if strings.TrimSpace(req.SubmitterID) == "" {
		writeErr(w, http.StatusBadRequest, "submitterId is required")
		return
	}
	if req.Value != 1 && req.Value != -1 {
		writeErr(w, http.StatusBadRequest, "value must be 1 or -1")
		return
	}
	sum, err := a.st.Vote(r.Context(), id, req.SubmitterID, req.Value)
	if err != nil {
		if errors.Is(err, sql.ErrNoRows) {
			writeErr(w, http.StatusNotFound, "segment not found")
			return
		}
		a.log.Error("vote", "err", err)
		writeErr(w, http.StatusInternalServerError, "vote failed")
		return
	}
	writeJSON(w, http.StatusOK, map[string]any{"segmentId": id, "votes": sum})
}
